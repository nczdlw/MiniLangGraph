"""图定义与编译：StateGraph 建图 API、编译期校验、graph_hash、to_mermaid。

入口/结束用 START / END 哨兵声明。编译校验覆盖计划三.3 的 6 条规则。
实际执行逻辑在 engine 模块，CompiledGraph.invoke/resume 通过懒导入调用。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable, Iterable, Optional, Union

from .errors import CompileError

logger = logging.getLogger("minigraph")

# 哨兵：入口与结束。用对象而非字符串，避免与同名节点冲突。
class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        return hash(("__sentinel__", self.name))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Sentinel) and other.name == self.name


START = _Sentinel("START")
END = _Sentinel("END")

NodeFn = Callable[[dict], dict]
RouterFn = Callable[[dict], Union[str, list[str]]]
Reducer = Callable[[object, object], object]

EdgeEndpoint = Union[str, _Sentinel]


class StateGraph:
    """声明式建图：增节点、固定边、条件边，最后 compile()。"""

    def __init__(self, reducers: Optional[dict[str, Reducer]] = None) -> None:
        self.reducers = dict(reducers) if reducers else {}
        self._nodes: dict[str, NodeFn] = {}  # 注册顺序即执行稳定顺序
        self._fixed_edges: list[tuple[EdgeEndpoint, EdgeEndpoint]] = []
        self._cond_edges: list[tuple[EdgeEndpoint, RouterFn, Optional[dict[str, str]]]] = []

    # ---------- 建图 API ----------
    def add_node(self, name: str, fn: NodeFn) -> "StateGraph":
        if name in self._nodes:
            raise CompileError(f"节点名重复注册: {name!r}")
        if name in ("START", "END"):
            raise CompileError("节点名不能使用保留字 START/END")
        self._nodes[name] = fn
        return self

    def add_edge(self, source: EdgeEndpoint, target: EdgeEndpoint) -> "StateGraph":
        self._fixed_edges.append((source, target))
        return self

    def add_conditional_edges(
        self,
        source: EdgeEndpoint,
        router: RouterFn,
        mapping: Optional[dict[str, str]] = None,
    ) -> "StateGraph":
        self._cond_edges.append((source, router, mapping))
        return self

    # ---------- 编译 ----------
    def compile(self) -> "CompiledGraph":
        nodes = list(self._nodes.keys())
        node_set = set(nodes)

        # 归一化边端点，并做基础有效性检查
        fixed: list[tuple[EdgeEndpoint, EdgeEndpoint]] = []
        cond: list[tuple[EdgeEndpoint, RouterFn, Optional[dict[str, str]]]] = []
        for s, t in self._fixed_edges:
            if s is END:
                raise CompileError("END 不能作为边的源（起点）")
            if t is START:
                raise CompileError("START 不能作为边的目标（终点）")
            if isinstance(s, str) and s not in node_set and s is not START:
                raise CompileError(f"边源节点未注册: {s!r}")
            if isinstance(t, str) and t not in node_set and t is not END:
                raise CompileError(f"边目标节点未注册: {t!r}")
            fixed.append((s, t))
        for s, router, mapping in self._cond_edges:
            if s is END:
                raise CompileError("END 不能作为条件边的源")
            if isinstance(s, str) and s not in node_set and s is not START:
                raise CompileError(f"条件边源节点未注册: {s!r}")
            if mapping is not None:
                for key, target in mapping.items():
                    if target is END or target == "END":
                        continue
                    if isinstance(target, str) and target not in node_set:
                        raise CompileError(
                            f"条件映射目标未注册: {target!r}（键 {key!r}）"
                        )
            cond.append((s, router, mapping))

        # 每个节点/START 的出边类型互斥（固定 XOR 条件）
        def _out_kind(x: EdgeEndpoint) -> str:
            has_fixed = any(s is x for s, _ in fixed)
            has_cond = any(s is x for s, _, _ in cond)
            if has_fixed and has_cond:
                raise CompileError(
                    f"{_name_of(x)} 同时配置了固定边与条件边，二者互斥"
                )
            return "fixed" if has_fixed else ("cond" if has_cond else "none")

        for n in nodes:
            _out_kind(n)
        _out_kind(START)

        # START 必须至少有一种出口
        start_kind = _out_kind(START)
        if start_kind == "none":
            raise CompileError("START 没有任何出边，图无法启动")

        # 邻接表
        fixed_succs: dict[EdgeEndpoint, list[EdgeEndpoint]] = {}
        cond_succs: dict[EdgeEndpoint, list[tuple[RouterFn, Optional[dict[str, str]]]]] = {}
        preds_all: dict[str, set[EdgeEndpoint]] = {n: set() for n in nodes}
        for s, t in fixed:
            fixed_succs.setdefault(s, []).append(t)
            if isinstance(t, str):
                preds_all[t].add(s)
        for s, router, mapping in cond:
            cond_succs.setdefault(s, []).append((router, mapping))
            # 静态可达目标（仅 mapping 已知时）
            if mapping is not None:
                for target in mapping.values():
                    if target is END or target == "END":
                        continue
                    if isinstance(target, str):
                        preds_all[target].add(s)

        # 可达性：从 START 出发（静态边）；END 也作为后继返回，供 BFS 检测可达
        def _static_succs(x: EdgeEndpoint) -> list[EdgeEndpoint]:
            out = list(fixed_succs.get(x, []))
            if x in cond_succs:
                for _r, mapping in cond_succs[x]:
                    if mapping is not None:
                        for target in mapping.values():
                            if target is END or target == "END":
                                out.append(END)
                            elif isinstance(target, str):
                                out.append(target)
            return out

        reachable = set()
        reached_end = False
        stack = [START]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            for nxt in _static_succs(cur):
                if nxt is END:
                    reached_end = True
                    continue
                if isinstance(nxt, str) and nxt not in reachable:
                    stack.append(nxt)

        for n in nodes:
            if n not in reachable:
                raise CompileError(f"节点 {n!r} 无法从 START 到达")

        # 静态 END 路径 + mapping=None 动态路由提示
        has_static_end = reached_end
        has_dynamic_cond = any(mapping is None for _s, _r, mapping in cond)
        if not has_static_end and not has_dynamic_cond:
            raise CompileError("不存在静态可见的 END 路径，且无条件路由可达 END")
        if not has_static_end and has_dynamic_cond:
            logger.warning(
                "未检测到静态 END 路径，存在 mapping=None 的动态条件路由，"
                "运行时将校验目标；请确认存在可达 END 的路径。"
            )

        # 回边检测（用于同步汇聚的循环重入语义）
        back_preds: dict[str, set[str]] = {n: set() for n in nodes}
        visited: set[EdgeEndpoint] = set()
        on_stack: set[EdgeEndpoint] = set()

        def _dfs(u: EdgeEndpoint) -> None:
            on_stack.add(u)
            for v in _static_succs(u):
                if v is END or not isinstance(v, str):
                    continue
                if v in on_stack:
                    # 回边（含自环）：标记反向前驱，不递归以避免无限递归
                    back_preds[v].add(u)
                elif v not in visited:
                    _dfs(v)
            on_stack.discard(u)
            visited.add(u)

        _dfs(START)

        graph_hash = _compute_graph_hash(nodes, fixed, cond)

        return CompiledGraph(
            node_fns=dict(self._nodes),
            reducers=dict(self.reducers),
            fixed_edges=fixed,
            cond_edges=cond,
            fixed_succs=fixed_succs,
            cond_succs=cond_succs,
            preds_all=preds_all,
            back_preds=back_preds,
            graph_hash=graph_hash,
        )


def _name_of(x: EdgeEndpoint) -> str:
    return "START" if x is START else ("END" if x is END else repr(x))


def _canon_endpoint(x: EdgeEndpoint) -> str:
    if x is START:
        return "__start__"
    if x is END:
        return "__end__"
    return x


def _compute_graph_hash(nodes, fixed, cond) -> str:
    canon = {
        "nodes": sorted(nodes),
        "fixed": sorted(
            (_canon_endpoint(s), _canon_endpoint(t)) for s, t in fixed
        ),
        "cond": sorted(
            (
                _canon_endpoint(s),
                getattr(r, "__module__", "") + "." + getattr(r, "__qualname__", ""),
                (tuple(sorted(mapping.items())) if mapping else None),
            )
            for s, r, mapping in cond
        ),
    }
    blob = repr(canon).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class CompiledGraph:
    """编译后的图：保存执行所需的全部静态信息，并暴露 invoke/resume/to_mermaid。"""

    def __init__(
        self,
        *,
        node_fns: dict[str, NodeFn],
        reducers: dict[str, Reducer],
        fixed_edges: list[tuple[EdgeEndpoint, EdgeEndpoint]],
        cond_edges: list[tuple[EdgeEndpoint, RouterFn, Optional[dict[str, str]]]],
        fixed_succs: dict[EdgeEndpoint, list[EdgeEndpoint]],
        cond_succs: dict[EdgeEndpoint, list[tuple[RouterFn, Optional[dict[str, str]]]]],
        preds_all: dict[str, set[EdgeEndpoint]],
        back_preds: dict[str, set[str]],
        graph_hash: str,
    ) -> None:
        self.node_fns = node_fns
        self.reducers = reducers
        self.fixed_edges = fixed_edges
        self.cond_edges = cond_edges
        self.fixed_succs = fixed_succs
        self.cond_succs = cond_succs
        self.preds_all = preds_all
        self.back_preds = back_preds
        self.graph_hash = graph_hash
        self.nodes = list(node_fns.keys())

    # ---------- 公共执行 API ----------
    def invoke(
        self,
        input: Optional[dict] = None,
        *,
        run_id: Optional[str] = None,
        checkpoint_dir: Union[str, "os.PathLike", None] = ".checkpoints",
        interrupt_before: Iterable[str] = (),
        interrupt_after: Iterable[str] = (),
        max_steps: int = 100,
        keep_last: Optional[int] = None,
        encoder=None,
        decoder=None,
    ) -> dict:
        from .engine import run_graph

        return run_graph(
            self,
            input=input,
            run_id=run_id,
            checkpoint_dir=checkpoint_dir,
            resume_run_id=None,
            state_update=None,
            interrupt_before=list(interrupt_before),
            interrupt_after=list(interrupt_after),
            max_steps=max_steps,
            keep_last=keep_last,
            encoder=encoder,
            decoder=decoder,
        )

    def resume(
        self,
        run_id: str,
        *,
        state_update: Optional[dict] = None,
        checkpoint_dir: Union[str, "os.PathLike", None] = ".checkpoints",
        interrupt_before: Iterable[str] = (),
        interrupt_after: Iterable[str] = (),
        max_steps: int = 100,
        keep_last: Optional[int] = None,
        encoder=None,
        decoder=None,
    ) -> dict:
        from .engine import run_graph

        return run_graph(
            self,
            input=None,
            run_id=run_id,
            checkpoint_dir=checkpoint_dir,
            resume_run_id=run_id,
            state_update=state_update,
            interrupt_before=list(interrupt_before),
            interrupt_after=list(interrupt_after),
            max_steps=max_steps,
            keep_last=keep_last,
            encoder=encoder,
            decoder=decoder,
        )

    # ---------- 可视化 ----------
    def to_mermaid(self) -> str:
        lines = ["flowchart TD"]
        lines.append("    START([START])")
        lines.append("    END((END))")
        for name in self.nodes:
            lines.append(f'    {name}["{name}"]')
        for s, t in self.fixed_edges:
            lines.append(f"    {_canon_endpoint(s)} --> {_canon_endpoint(t)}")
        for s, _r, mapping in self.cond_edges:
            if mapping is not None:
                for key, target in mapping.items():
                    lines.append(
                        f"    {_canon_endpoint(s)} -->|{key}| {_canon_endpoint(target)}"
                    )
            else:
                lines.append(f"    {_canon_endpoint(s)} -->|route| NODE")
        return "\n".join(lines)
