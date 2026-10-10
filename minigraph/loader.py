"""声明式加载：从 JSON 流程文件装配 StateGraph。

JSON 结构：
{
  "imports": ["some.module", "path/to/file.py"],
  "reducers": {"total": "sum_reducer"},
  "nodes": [{"name": "a", "fn": "node_a"}, ...],
  "edges": [["a", "b"], {"source": "a", "target": "b"}],
  "conditional_edges": [
    {"source": "a", "router": "route_a", "mapping": {"x": "b", "y": "END"}}
  ]
}

imports 支持 Python 模块名或 .py 文件路径；导入模块通过注册装饰器提供节点/路由器/reducer。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import sys
from typing import Union

from .errors import CompileError
from .graph import END, START, StateGraph
from .registry import NODES, REDUCERS, ROUTERS


def _resolve_endpoint(s: str):
    if s == "START":
        return START
    if s == "END":
        return END
    return s  # 节点名


def _import_spec(spec: str, base_dir: pathlib.Path) -> None:
    if spec.endswith(".py") or spec.endswith(".pyw"):
        path = (base_dir / spec).resolve() if not pathlib.Path(spec).is_absolute() else pathlib.Path(spec)
        if not path.exists():
            raise CompileError(f"imports 引用的文件不存在: {spec}（解析为 {path}）")
        modname = "minigraph_user_" + path.stem
        spec_obj = importlib.util.spec_from_file_location(modname, str(path))
        if spec_obj is None or spec_obj.loader is None:
            raise CompileError(f"无法为 {spec} 创建导入规范")
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[modname] = module
        spec_obj.loader.exec_module(module)
    else:
        importlib.import_module(spec)


def load_flow(source: Union[str, pathlib.Path, dict]) -> StateGraph:
    """从 JSON 文件或 dict 加载并编译出 CompiledGraph。"""
    if isinstance(source, (str, pathlib.Path)):
        path = pathlib.Path(source)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        base_dir = path.parent
    else:
        data = source
        base_dir = pathlib.Path(".")

    if not isinstance(data, dict):
        raise CompileError("流程 JSON 根元素必须是对象")

    for imp in data.get("imports", []):
        try:
            _import_spec(imp, base_dir)
        except CompileError:
            raise
        except Exception as e:
            raise CompileError(f"导入 {imp!r} 失败: {type(e).__name__}: {e}")

    sg = StateGraph()
    for key, fname in data.get("reducers", {}).items():
        if fname not in REDUCERS:
            raise CompileError(f"reducer 未注册: {fname!r}（键 {key!r}；需在 imports 模块中用 @register_reducer 注册）")
        sg.reducers[key] = REDUCERS[fname]

    for nd in data.get("nodes", []):
        nm = nd.get("name")
        fn = nd.get("fn")
        if not nm or not fn:
            raise CompileError(f"节点声明缺少 name/fn: {nd!r}")
        if fn not in NODES:
            raise CompileError(f"节点函数未注册: {fn!r}（节点 {nm!r} 引用；需 @register_node 注册）")
        sg.add_node(nm, NODES[fn])

    for e in data.get("edges", []):
        if isinstance(e, (list, tuple)):
            s, t = e[0], e[1]
        elif isinstance(e, dict):
            s, t = e.get("source"), e.get("target")
        else:
            raise CompileError(f"边格式不支持: {e!r}")
        if s is None or t is None:
            raise CompileError(f"边缺少 source/target: {e!r}")
        sg.add_edge(_resolve_endpoint(s), _resolve_endpoint(t))

    for ce in data.get("conditional_edges", []):
        s = _resolve_endpoint(ce.get("source"))
        rname = ce.get("router")
        if rname not in ROUTERS:
            raise CompileError(f"路由器未注册: {rname!r}（源 {ce.get('source')!r}；需 @register_router 注册）")
        mapping = ce.get("mapping")
        if mapping is not None:
            mapping = {
                k: (_resolve_endpoint(v) if v in ("START", "END") else v)
                for k, v in mapping.items()
            }
        sg.add_conditional_edges(s, ROUTERS[rname], mapping)

    return sg.compile()
