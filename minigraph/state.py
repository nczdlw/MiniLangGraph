"""状态工具：JSON 原生类型契约校验、深拷贝隔离、reducer 合并。

设计要点（对应计划三.1/三.4）：
- 状态只接受 JSON 原生类型：None/bool/int/float/str/list/dict，且 dict 键必须为 str。
- 每个节点执行时拿到一份独立深拷贝的状态副本，避免同批次节点互相污染（验收 3）。
- reducer 按 key 合并，未配置 reducer 的 key 直接覆盖（replace）。
"""

from __future__ import annotations

import copy
from typing import Callable, Optional

from .errors import StateContractError

# reducer 类型： (current_value, update_value) -> new_value
Reducer = Callable[[object, object], object]


def _first_non_native(obj, path: str) -> Optional[str]:
    """返回第一个非 JSON 原生值的字段路径；全部合法则返回 None。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return None
    if isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            bad = _first_non_native(item, f"{path}[{i}]")
            if bad is not None:
                return bad
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                return (path + "." + str(k)) if path else str(k)
            child_path = (path + "." + str(k)) if path else str(k)
            bad = _first_non_native(v, child_path)
            if bad is not None:
                return bad
        return None
    # 其他类型（如 set / datetime / 自定义对象）不支持
    return path if path else "<root>"


def is_json_native(obj) -> bool:
    """obj 是否为可持久化的 JSON 原生类型。"""
    return _first_non_native(obj, "") is None


def validate_json_native(obj, *, node: Optional[str] = None) -> None:
    """校验是否为 JSON 原生类型；不合法抛出 StateContractError 并带字段路径。"""
    bad = _first_non_native(obj, "")
    if bad is not None:
        raise StateContractError(
            "值不是 JSON 原生类型（应为 None/bool/int/float/str/list/dict，且 dict 键为 str）",
            node=node,
            path=bad,
        )


def deep_copy_state(state: dict) -> dict:
    """深拷贝状态，给节点提供隔离副本，防止嵌套结构在同批次间污染。"""
    return copy.deepcopy(state)


def apply_update(
    state: dict,
    update,
    reducers: Optional[dict[str, Reducer]] = None,
    *,
    node: Optional[str] = None,
) -> dict:
    """把节点返回的增量 update 合并进 state，返回新状态（不修改入参）。

    - update 必须是 dict；否则报状态契约错误并指出节点。
    - update 的值必须 JSON 原生；否则报状态契约错误并带字段路径。
    - 配置了 reducer 的 key 走 reducer；否则直接覆盖（深拷贝后存入，避免与原对象共享引用）。
    """
    if not isinstance(update, dict):
        raise StateContractError(
            "节点返回值必须是 dict 形式的增量", node=node, path="<return>"
        )
    validate_json_native(update, node=node)

    new_state = dict(state)
    for k, v in update.items():
        if reducers and k in reducers:
            new_state[k] = reducers[k](new_state.get(k), v)
        else:
            new_state[k] = copy.deepcopy(v)
    return new_state
