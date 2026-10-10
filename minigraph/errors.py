"""minigraph 异常体系。

所有自定义异常都派生自 GraphError，便于调用方统一捕获。
"""

from __future__ import annotations

from typing import Optional


class GraphError(Exception):
    """所有 minigraph 错误的基类。"""


class CompileError(GraphError):
    """编译期校验失败：图结构不合法（入口不唯一、边引用无效等）。"""


class StateContractError(GraphError):
    """状态不符合 JSON 原生类型契约，或节点返回值非法。

    携带 node（涉及节点，可为 None）、path（字段路径，如 "a.b[0]"）以便定位。
    """

    def __init__(
        self,
        message: str,
        *,
        node: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        self.node = node
        self.path = path
        parts = []
        if node is not None:
            parts.append(f"node={node!r}")
        if path is not None:
            parts.append(f"path={path!r}")
        suffix = (" [" + ", ".join(parts) + "]") if parts else ""
        super().__init__(message + suffix)


class CheckpointError(GraphError):
    """检查点读写/校验失败：损坏、版本不支持、图哈希不匹配、运行 ID 冲突等。"""

    def __init__(
        self,
        message: str,
        *,
        run_id: Optional[str] = None,
        recoverable: bool = False,
    ) -> None:
        self.run_id = run_id
        self.recoverable = recoverable
        suffix = f" [run_id={run_id!r}]" if run_id is not None else ""
        super().__init__(message + suffix)


class NodeError(GraphError):
    """节点函数执行时抛出的异常，包裹原始异常以保留类型、信息与回溯。"""

    def __init__(
        self,
        message: str,
        *,
        node: str,
        exc_type: Optional[str] = None,
        original: Optional[BaseException] = None,
    ) -> None:
        self.node = node
        self.exc_type = exc_type
        self.original = original
        super().__init__(message)


class GraphInterrupt(GraphError):
    """流程在某个节点前/后中断，等待人工干预。

    携带当前状态，调用方可用相同的 run_id 通过 resume 续跑。
    """

    def __init__(self, message: str, *, run_id: str, state: Optional[dict] = None) -> None:
        self.run_id = run_id
        self.state = state
        super().__init__(message)
