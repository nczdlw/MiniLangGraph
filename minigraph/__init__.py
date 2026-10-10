"""minigraph — 本地简化版 LangGraph 风格工作流引擎（纯标准库）。

公共 API：
- StateGraph / START / END：声明式建图与编译
- errors：GraphError 体系
- load_flow：从 JSON 流程文件装配图（见 loader）
- run_cli：命令行入口（见 cli）
"""

from __future__ import annotations

from .errors import (
    CheckpointError,
    CompileError,
    GraphError,
    GraphInterrupt,
    NodeError,
    StateContractError,
)
from .graph import END, START, CompiledGraph, StateGraph

__all__ = [
    "StateGraph",
    "CompiledGraph",
    "START",
    "END",
    "GraphError",
    "CompileError",
    "StateContractError",
    "CheckpointError",
    "NodeError",
    "GraphInterrupt",
]
