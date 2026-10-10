"""graph 模块单元测试：编译校验、graph_hash、to_mermaid、START/END。"""

import unittest

from minigraph import END, START, CompileError, StateGraph


def _node(name, fn=None):
    if fn is None:
        fn = lambda s: {}  # noqa: E731
    return name, fn


class TestCompileValidation(unittest.TestCase):
    def test_linear_compiles(self):
        g = StateGraph()
        g.add_node("a", lambda s: {"x": 1})
        g.add_node("b", lambda s: {"y": 2})
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        compiled = g.compile()
        self.assertIn("a", compiled.nodes)
        self.assertIn("b", compiled.nodes)

    def test_duplicate_node_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        with self.assertRaises(CompileError):
            g.add_node("a", lambda s: {})

    def test_start_without_outgoing_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        with self.assertRaises(CompileError):
            g.compile()

    def test_invalid_edge_target_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(START, "a")
        g.add_edge("a", "ghost")
        with self.assertRaises(CompileError):
            g.compile()

    def test_end_as_source_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(END, "a")
        with self.assertRaises(CompileError):
            g.compile()

    def test_fixed_and_conditional_mutually_exclusive(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_conditional_edges("a", lambda s: "b")
        with self.assertRaises(CompileError):
            g.compile()

    def test_conditional_mapping_invalid_target_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(START, "a")
        g.add_conditional_edges("a", lambda s: "c", mapping={"c": "ghost"})
        with self.assertRaises(CompileError):
            g.compile()

    def test_unreachable_node_raises(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("orphan", lambda s: {})
        g.add_edge(START, "a")
        g.add_edge("a", END)
        with self.assertRaises(CompileError):
            g.compile()

    def test_no_static_end_path_raises(self):
        # 没有 END 可达路径，且没有 mapping=None 的动态条件路由 -> 编译失败
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        # b 没有到 END 的边
        with self.assertRaises(CompileError):
            g.compile()

    def test_dynamic_conditional_allows_no_static_end(self):
        # mapping=None 的动态路由，编译给出提示但允许
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(START, "a")
        g.add_conditional_edges("a", lambda s: "END")  # 动态返回 END
        compiled = g.compile()  # 不应抛错
        self.assertIsNotNone(compiled)


class TestGraphHashAndMermaid(unittest.TestCase):
    def test_hash_stable(self):
        def build():
            g = StateGraph()
            g.add_node("a", lambda s: {})
            g.add_node("b", lambda s: {})
            g.add_edge(START, "a")
            g.add_edge("a", "b")
            g.add_edge("b", END)
            return g.compile()
        self.assertEqual(build().graph_hash, build().graph_hash)

    def test_hash_differs_on_structure(self):
        g1 = StateGraph()
        g1.add_node("a", lambda s: {})
        g1.add_edge(START, "a")
        g1.add_edge("a", END)
        g2 = StateGraph()
        g2.add_node("a", lambda s: {})
        g2.add_node("b", lambda s: {})
        g2.add_edge(START, "a")
        g2.add_edge("a", "b")
        g2.add_edge("b", END)
        self.assertNotEqual(g1.compile().graph_hash, g2.compile().graph_hash)

    def test_to_mermaid(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        m = g.compile().to_mermaid()
        self.assertIn("START", m)
        self.assertIn("END", m)
        self.assertIn("a", m)


if __name__ == "__main__":
    unittest.main()
