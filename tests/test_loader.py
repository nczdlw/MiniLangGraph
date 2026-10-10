"""loader 模块单元测试：JSON 流程加载、imports 注册、缺失符号与非法映射报错。"""

import json
import os
import tempfile
import unittest

from minigraph.loader import load_flow
from minigraph.registry import clear_registry
from minigraph.errors import CompileError


def _write(dir_path, name, content):
    p = os.path.join(dir_path, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


NODES_PY = '''
from minigraph.registry import register_node, register_router, register_reducer

@register_node("add_one")
def add_one(s):
    return {"v": 1}  # 返回增量；配合 sum reducer 累加

@register_router("to_end")
def to_end(s):
    return "x"  # 返回 mapping 的键，由 mapping 映射到 END

@register_reducer("sum_red")
def sum_red(cur, upd):
    return (cur or 0) + upd
'''


class TestLoader(unittest.TestCase):
    def setUp(self):
        clear_registry()
        self.dir = tempfile.mkdtemp(prefix="mg_loader_")

    def tearDown(self):
        clear_registry()

    def _flow(self, nodes_body, edges, extra=""):
        _write(self.dir, "nodes_mod.py", NODES_PY)
        flow = {
            "imports": ["nodes_mod.py"],
            "reducers": {"v": "sum_red"},
            "nodes": nodes_body,
            "edges": edges,
        }
        if extra:
            flow.update(json.loads(extra))
        path = os.path.join(self.dir, "flow.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False)
        return path

    def test_load_and_run(self):
        path = self._flow(
            [{"name": "a", "fn": "add_one"}, {"name": "b", "fn": "add_one"}],
            [["START", "a"], ["a", "b"], ["b", "END"]],
        )
        compiled = load_flow(path)
        out = compiled.invoke({})
        self.assertEqual(out["v"], 2)  # 两次 add_one 经 reducer 累加

    def test_missing_node_fn_raises(self):
        path = self._flow(
            [{"name": "a", "fn": "does_not_exist"}],
            [["START", "a"], ["a", "END"]],
        )
        with self.assertRaises(CompileError):
            load_flow(path)

    def test_missing_import_file_raises(self):
        _write(self.dir, "flow.json", json.dumps({
            "imports": ["no_such_file.py"],
            "nodes": [{"name": "a", "fn": "add_one"}],
            "edges": [["START", "a"], ["a", "END"]],
        }, ensure_ascii=False))
        with self.assertRaises(CompileError):
            load_flow(os.path.join(self.dir, "flow.json"))

    def test_bad_mapping_target_raises(self):
        flow = {
            "imports": ["nodes_mod.py"],
            "nodes": [{"name": "a", "fn": "add_one"}],
            "edges": [["START", "a"]],
            "conditional_edges": [
                {"source": "a", "router": "to_end", "mapping": {"x": "ghost"}}
            ],
        }
        _write(self.dir, "nodes_mod.py", NODES_PY)
        path = os.path.join(self.dir, "flow.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False)
        with self.assertRaises(CompileError):
            load_flow(path)

    def test_conditional_edge_runs(self):
        flow = {
            "imports": ["nodes_mod.py"],
            "nodes": [{"name": "a", "fn": "add_one"}],
            "edges": [["START", "a"]],
            "conditional_edges": [
                {"source": "a", "router": "to_end", "mapping": {"x": "END"}}
            ],
        }
        _write(self.dir, "nodes_mod.py", NODES_PY)
        path = os.path.join(self.dir, "flow.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False)
        compiled = load_flow(path)
        out = compiled.invoke({})
        self.assertEqual(out["v"], 1)


if __name__ == "__main__":
    unittest.main()
