"""注册表：通过装饰器收集 JSON 流程所需的节点 / 路由器 / reducer。

imports 导入的 Python 模块用这些装饰器注册符号；loader 按名装配。
"""

from __future__ import annotations

from typing import Callable

NODES: dict[str, Callable] = {}
ROUTERS: dict[str, Callable] = {}
REDUCERS: dict[str, Callable] = {}

NodeFn = Callable[[dict], dict]
RouterFn = Callable[[dict], object]
ReducerFn = Callable[[object, object], object]


def register_node(name: str):
    def deco(fn: NodeFn) -> NodeFn:
        if name in NODES:
            raise ValueError(f"节点 {name!r} 已注册，不能重复")
        NODES[name] = fn
        return fn
    return deco


def register_router(name: str):
    def deco(fn: RouterFn) -> RouterFn:
        if name in ROUTERS:
            raise ValueError(f"路由器 {name!r} 已注册，不能重复")
        ROUTERS[name] = fn
        return fn
    return deco


def register_reducer(name: str):
    def deco(fn: ReducerFn) -> ReducerFn:
        if name in REDUCERS:
            raise ValueError(f"reducer {name!r} 已注册，不能重复")
        REDUCERS[name] = fn
        return fn
    return deco


def clear_registry() -> None:
    """清空注册表，主要用于测试隔离。"""
    NODES.clear()
    ROUTERS.clear()
    REDUCERS.clear()
