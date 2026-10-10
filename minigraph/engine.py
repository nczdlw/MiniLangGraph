"""执行引擎：超步（super-step）、同步汇聚（token 到达模型）、条件路由、中断、失败保存与恢复。

同步汇聚模型（对应计划三.2）：
- 每个超步开始时固定状态快照；本轮候选节点按稳定注册顺序执行，每个节点拿到独立的状态深拷贝副本。
- 同一超步内节点写入在屏障处按节点稳定顺序经 reducer 合并，不能被同批次其他节点提前观察。
- 汇聚（join）节点在所有"前向"前驱到达后才进入下一超步；回边（循环）作为重入触发，需在前驱再次触发后才重入。
- 条件路由只把实际选择的目标计入本轮到达集合；动态非法目标抛可读 GraphError。
- 无条件环不会死锁：max_steps 超限写入 failed 检查点并抛可读错误。
"""

from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path
from typing import Callable, Optional, Union

from .checkpoint import SCHEMA_VERSION, CheckpointStore
from .errors import CheckpointError, GraphError, GraphInterrupt, NodeError, StateContractError
from .graph import END, START, CompiledGraph
from .state import apply_update, deep_copy_state, validate_json_native

logger = logging.getLogger("minigraph")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _is_ready(
    n: str,
    arrived: dict[str, set],
    has_fired: dict[str, bool],
    preds_all: dict[str, set],
    back_preds: dict[str, set],
) -> bool:
    required = set(preds_all[n]) - {START}
    if not has_fired[n]:
        # 首次触发：回边（循环）前驱尚未发生，不计入
        required = required - back_preds[n]
    if not required:
        # 仅 START 前驱（无前向前驱）：只在首次触发，之后不会因无新到达而重入
        return not has_fired[n]
    return required <= arrived[n]


def _resolve_router(
    result,
    mapping: Optional[dict[str, str]],
    compiled_nodes: set,
    source: str,
) -> list:
    """把路由器返回值解析为具体目标节点名或 END 列表；非法目标抛可读错误。"""
    if isinstance(result, str):
        keys = [result]
    elif isinstance(result, (list, tuple)):
        keys = list(result)
    else:
        raise StateContractError(
            f"路由器返回值必须是 str 或 list[str]，得到 {type(result).__name__}",
            node=source,
            path="<router>",
        )
    resolved = []
    for k in keys:
        if mapping is not None:
            if k not in mapping:
                raise GraphError(
                    f"路由结果 {k!r} 不在 mapping 中（可选: {list(mapping.keys())}），源节点 {source!r}"
                )
            target = mapping[k]
        else:
            target = k  # 动态路由：返回值即节点名或 END
        if target is END or target == "END":
            resolved.append(END)
        elif isinstance(target, str) and target in compiled_nodes:
            resolved.append(target)
        else:
            raise GraphError(
                f"路由目标无效: {target!r}（节点未注册且非 END），源节点 {source!r}"
            )
    logger.info("route %s -> %s", source, resolved)
    return resolved


def _successors(
    n: str,
    state: dict,
    compiled: CompiledGraph,
) -> list:
    """返回节点 n 在当前 state 下的后继节点名（不含 END）。"""
    out = []
    for t in compiled.fixed_succs.get(n, []):
        if t is END:
            continue
        out.append(t)
    for router, mapping in compiled.cond_succs.get(n, []):
        for tg in _resolve_router(router(state), mapping, set(compiled.nodes), n):
            if tg is END:
                continue
            out.append(tg)
    return out


def _step_payload(
    *,
    run_id: str,
    step: int,
    status: str,
    state: dict,
    arrived: dict[str, set],
    has_fired: dict[str, bool],
    pending: list,
    completed: Optional[list] = None,
    collected_writes: Optional[dict] = None,
    error: Optional[dict] = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "step": step,
        "status": status,
        "state": state,
        "arrived": {k: sorted(v) for k, v in arrived.items()},
        "has_fired": has_fired,
        "pending": pending,
        "completed": completed or [],
        "collected_writes": collected_writes or {},
        "error": error,
        "timestamp": _now(),
    }


def run_graph(
    compiled: CompiledGraph,
    *,
    input: Optional[dict] = None,
    run_id: Optional[str] = None,
    checkpoint_dir: Union[str, "os.PathLike", None] = ".checkpoints",
    resume_run_id: Optional[str] = None,
    state_update: Optional[dict] = None,
    interrupt_before: Optional[list] = None,
    interrupt_after: Optional[list] = None,
    max_steps: int = 100,
    keep_last: Optional[int] = None,
    encoder=None,
    decoder=None,
) -> dict:
    interrupt_before = set(interrupt_before or [])
    interrupt_after = set(interrupt_after or [])
    ckpt_dir = Path(checkpoint_dir) if checkpoint_dir else Path(".checkpoints")
    nodes = compiled.nodes
    reducers = compiled.reducers

    if resume_run_id is None:
        # ---- 新运行 ----
        run_id = run_id or uuid.uuid4().hex
        store = CheckpointStore(ckpt_dir, run_id, encoder=encoder, decoder=decoder)
        if store.exists():
            raise CheckpointError(
                f"运行 ID {run_id!r} 已存在，禁止覆盖；请用新 run_id 或 resume",
                run_id=run_id,
            )
        store.init_new()
        state = deep_copy_state(input) if input else {}
        validate_json_native(state)
        arrived = {n: set() for n in nodes}
        has_fired = {n: False for n in nodes}
        step0 = 0
        forced_ready: Optional[list] = None
        created = _now()
        store.save_meta("running", compiled.graph_hash, created_at=created, updated_at=created)
        is_resume = False
    else:
        # ---- 续跑 ----
        run_id = resume_run_id
        store = CheckpointStore(ckpt_dir, run_id, encoder=encoder, decoder=decoder)
        if not store.exists():
            raise CheckpointError(f"运行 {run_id!r} 不存在，无法 resume", run_id=run_id)
        meta = store.load_meta()
        if meta.get("graph_hash") != compiled.graph_hash:
            logger.warning(
                "图哈希不匹配（已保存=%s，当前=%s）；函数体变化无法检测，继续执行。",
                meta.get("graph_hash"),
                compiled.graph_hash,
            )
        step_latest, cp = store.load_latest()
        status = cp["status"]
        if status == "done":
            state = cp["state"]
            if state_update:
                state = apply_update(state, state_update, reducers)
            return state
        state = cp["state"]
        validate_json_native(state)
        arrived = {n: set(cp["arrived"].get(n, [])) for n in nodes}
        has_fired = {n: bool(cp["has_fired"].get(n, False)) for n in nodes}
        step0 = step_latest
        forced_ready = list(cp.get("pending") or [])
        is_resume = True
        if status == "failed":
            # 恢复：先把上一超步已成功节点的写入合并进 state，再只跑 pending
            collected = cp.get("collected_writes") or {}
            for m in sorted(collected.keys()):
                state = apply_update(state, collected[m], reducers, node=m)
        else:  # interrupted
            if state_update:
                state = apply_update(state, state_update, reducers)

    # ---- 主循环 ----
    while True:
        step = step0 + 1
        if step > max_steps:
            _save_failed(
                store, run_id, step, state, arrived, has_fired,
                pending=[],
                error={"node": None, "exc_type": "MaxStepsExceeded",
                       "message": f"超过 max_steps={max_steps}，图可能陷入无法终止的循环", "traceback": ""},
                reducers=reducers, completed=[],
            )
            raise GraphError(
                f"超过 max_steps={max_steps}，流程未终止（可能存在无法终止的循环）"
            )

        forced_this_step = forced_ready is not None
        if forced_this_step:
            ready = [n for n in forced_ready if n in compiled.node_fns]
            forced_ready = None
        else:
            ready = [n for n in nodes if _is_ready(n, arrived, has_fired, compiled.preds_all, compiled.back_preds)]
        if ready:
            logger.info("step %d: 候选节点 %s", step, ready)

        if not ready:
            if all(has_fired[n] for n in nodes):
                # 所有可达节点均已触发，无更多进展 -> 完成
                store.save_step(_step_payload(
                    run_id=run_id, step=step, status="done", state=state,
                    arrived=arrived, has_fired=has_fired, pending=[], completed=[],
                ))
                _update_meta(store, "done")
                return state
            else:
                # 存在永远无法被满足的前驱 -> 停滞
                stuck = [n for n in nodes if not has_fired[n] and not _is_ready(
                    n, arrived, has_fired, compiled.preds_all, compiled.back_preds)]
                _save_failed(
                    store, run_id, step, state, arrived, has_fired,
                    pending=[],
                    error={"node": None, "exc_type": "GraphStall",
                           "message": f"图无法前进：无法满足的汇聚或不可达节点: {stuck}", "traceback": ""},
                    reducers=reducers, completed=[],
                )
                raise GraphError(f"图无法前进：无法满足的汇聚/不可达节点 {stuck}")

        # 执行本超步（同一快照 + 隔离副本）
        snapshot = deep_copy_state(state)
        updates: dict[str, dict] = {}
        completed: list[str] = []
        for n in ready:  # ready 已是稳定顺序
            logger.info("run node %s", n)
            # interrupt_before：在节点执行前中断
            if n in interrupt_before and not forced_this_step:
                _save_interrupted(store, run_id, step, state, arrived, has_fired, pending=[n])
                raise GraphInterrupt(f"在节点 {n!r} 前中断，等待人工干预", run_id=run_id, state=state)
            try:
                upd = compiled.node_fns[n](deep_copy_state(snapshot))
                validate_json_native(upd, node=n)
                updates[n] = upd
                completed.append(n)
            except Exception as e:
                # 失败：保留已成功节点的写入，只重跑失败节点及尚未执行节点
                remaining = ready[len(completed) + 1:]  # 失败节点之后、尚未执行的节点
                pending_fail = [n] + remaining
                # 已成功节点在本超步已"触发"：在副本上更新 arrived/has_fired，
                # 使恢复时：① 不重跑它们；② 下游汇聚能收到它们的到达
                arrived_cp = {k: set(v) for k, v in arrived.items()}
                has_fired_cp = dict(has_fired)
                merged_for_route = state
                for m in completed:
                    merged_for_route = apply_update(merged_for_route, updates[m], reducers, node=m)
                    arrived_cp[m] = set()
                    has_fired_cp[m] = True
                    for t in compiled.fixed_succs.get(m, []):
                        if t is END:
                            continue
                        arrived_cp[t].add(m)
                    for router, mapping in compiled.cond_succs.get(m, []):
                        for tg in _resolve_router(router(merged_for_route), mapping, set(nodes), m):
                            if tg is END:
                                continue
                            arrived_cp[tg].add(m)
                # state 保存超步开始前的快照；collected_writes 由恢复时合并一次（避免重复合并）
                collected_writes = {m: updates[m] for m in completed}
                err = {
                    "node": n,
                    "exc_type": type(e).__name__,
                    "message": str(e),
                    "traceback": _tb(e),
                }
                _save_failed(store, run_id, step, state, arrived_cp, has_fired_cp,
                             pending=pending_fail, error=err, reducers=reducers,
                             completed=completed, collected_writes=collected_writes)
                if isinstance(e, GraphError):
                    raise
                raise NodeError(
                    f"节点 {n!r} 执行失败: {type(e).__name__}: {e}",
                    node=n, exc_type=type(e).__name__, original=e,
                ) from e
            # interrupt_after：在节点成功后中断
            if n in interrupt_after and not forced_this_step:
                state = apply_update(state, updates[n], reducers, node=n)
                arrived[n] = set()
                has_fired[n] = True
                for t in compiled.fixed_succs.get(n, []):
                    if t is END:
                        continue
                    arrived[t].add(n)
                for router, mapping in compiled.cond_succs.get(n, []):
                    for tg in _resolve_router(router(state), mapping, set(nodes), n):
                        if tg is END:
                            continue
                        arrived[tg].add(n)
                successors = _successors(n, state, compiled)
                _save_interrupted(store, run_id, step, state, arrived, has_fired, pending=successors)
                raise GraphInterrupt(f"在节点 {n!r} 后中断，等待人工干预", run_id=run_id, state=state)

        # 屏障合并（按节点稳定顺序）
        new_state = state
        for m in completed:
            new_state = apply_update(new_state, updates[m], reducers, node=m)
        state = new_state

        for n in completed:
            arrived[n] = set()
            has_fired[n] = True
            for t in compiled.fixed_succs.get(n, []):
                if t is END:
                    continue
                arrived[t].add(n)
            for router, mapping in compiled.cond_succs.get(n, []):
                for tg in _resolve_router(router(state), mapping, set(nodes), n):
                    if tg is END:
                        continue
                    arrived[tg].add(n)

        # 写入本步运行检查点
        next_frontier = []
        for m in completed:
            for s in _successors(m, state, compiled):
                if s not in next_frontier:
                    next_frontier.append(s)
        store.save_step(_step_payload(
            run_id=run_id, step=step, status="running", state=state,
            arrived=arrived, has_fired=has_fired, pending=next_frontier, completed=completed,
        ))
        _update_meta(store, "running")
        step0 = step
        if keep_last is not None:
            store.prune_keep_last(keep_last)


def _tb(e: Exception) -> str:
    import traceback
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))


def _update_meta(store: CheckpointStore, status: str) -> None:
    try:
        meta = store.load_meta()
    except Exception:
        meta = {}
    store.save_meta(
        status,
        meta.get("graph_hash", ""),
        created_at=meta.get("created_at"),
        updated_at=_now(),
    )


def _save_failed(
    store, run_id, step, state, arrived, has_fired, *,
    pending, error, reducers, completed, collected_writes=None,
) -> None:
    store.save_step(_step_payload(
        run_id=run_id, step=step, status="failed", state=state,
        arrived=arrived, has_fired=has_fired, pending=pending,
        completed=completed, collected_writes=collected_writes, error=error,
    ))
    _update_meta(store, "failed")


def _save_interrupted(store, run_id, step, state, arrived, has_fired, *, pending) -> None:
    store.save_step(_step_payload(
        run_id=run_id, step=step, status="interrupted", state=state,
        arrived=arrived, has_fired=has_fired, pending=pending, completed=[],
    ))
    _update_meta(store, "interrupted")
