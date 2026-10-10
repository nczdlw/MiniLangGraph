"""engine 模块单元测试：覆盖验收 2-5 的执行语义。"""

import tempfile
import unittest

from minigraph import END, START, GraphError, GraphInterrupt, NodeError, StateGraph


def _tmp():
    return tempfile.mkdtemp(prefix="mg_test_")


class TestLinear(unittest.TestCase):
    def test_linear_runs(self):
        g = StateGraph()
        g.add_node("a", lambda s: {"x": 1})
        g.add_node("b", lambda s: {"y": s["x"] + 1})
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        out = g.compile().invoke({})
        self.assertEqual(out["x"], 1)
        self.assertEqual(out["y"], 2)


class TestConditionalRetry(unittest.TestCase):
    def _build(self, max_failures):
        calls = {"attempt": 0}

        def attempt(s):
            calls["attempt"] += 1
            if calls["attempt"] <= max_failures:
                return {"status": "fail", "tries": calls["attempt"]}
            return {"status": "ok", "tries": calls["attempt"]}

        def router(s):
            return "retry" if s.get("status") == "fail" else "done"

        g = StateGraph()
        g.add_node("attempt", attempt)
        g.add_edge(START, "attempt")
        g.add_conditional_edges("attempt", router, {"retry": "attempt", "done": "END"})
        return g.compile(), calls

    def test_retry_loop_terminates(self):
        compiled, calls = self._build(max_failures=2)
        out = compiled.invoke({})
        # 重试 2 次后第 3 次成功
        self.assertEqual(calls["attempt"], 3)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["tries"], 3)

    def test_retry_no_failure(self):
        compiled, calls = self._build(max_failures=0)
        out = compiled.invoke({})
        self.assertEqual(calls["attempt"], 1)
        self.assertEqual(out["status"], "ok")


class TestJoin(unittest.TestCase):
    def test_diamond_fires_once(self):
        calls = {"c": 0}

        def c(s):
            calls["c"] += 1
            return {}

        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_node("c", c)
        g.add_edge(START, "a")
        g.add_edge(START, "b")
        g.add_edge("a", "c")
        g.add_edge("b", "c")
        g.add_edge("c", END)
        g.compile().invoke({})
        self.assertEqual(calls["c"], 1)

    def test_unequal_length_join_fires_once(self):
        calls = {"c": 0}

        def c(s):
            calls["c"] += 1
            return {}

        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_node("c", c)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge(START, "d")
        g.add_node("d", lambda s: {})
        g.add_edge("d", "c")
        g.add_edge("c", END)
        g.compile().invoke({})
        self.assertEqual(calls["c"], 1)


class TestFanoutSnapshot(unittest.TestCase):
    def test_same_snapshot_no_cross_observation(self):
        # x、y 同属一个超步，都应看到初始快照，而非彼此写入
        def x(s):
            cp = list(s["data"])
            cp.append(10)
            return {"len_x": len(cp)}

        def y(s):
            cp = list(s["data"])
            cp.append(20)
            return {"len_y": len(cp)}

        g = StateGraph()
        g.add_node("x", x)
        g.add_node("y", y)
        g.add_edge(START, "x")
        g.add_edge(START, "y")
        g.add_edge("x", END)
        g.add_edge("y", END)
        out = g.compile().invoke({"data": [1]})
        self.assertEqual(out["len_x"], 2)  # 初始 [1] + 自身 append
        self.assertEqual(out["len_y"], 2)
        # 原 data 未被污染
        self.assertEqual(out["data"], [1])

    def test_nested_mutation_isolation(self):
        # 节点原地修改输入副本不应污染共享状态
        def m(s):
            s["nested"]["list"].append(999)
            return {}

        g = StateGraph()
        g.add_node("m", m)
        g.add_edge(START, "m")
        g.add_edge("m", END)
        out = g.compile().invoke({"nested": {"list": [1, 2]}})
        self.assertEqual(out["nested"]["list"], [1, 2])

    def test_reducer_merge_order_deterministic(self):
        reducers = {"total": lambda cur, upd: (cur or 0) + upd}

        def x(s):
            return {"total": 1}

        def y(s):
            return {"total": 2}

        g = StateGraph(reducers)
        g.add_node("x", x)
        g.add_node("y", y)
        g.add_edge(START, "x")
        g.add_edge(START, "y")
        g.add_edge("x", END)
        g.add_edge("y", END)
        out = g.compile().invoke({})
        self.assertEqual(out["total"], 3)  # 1 + 2


class TestInterrupt(unittest.TestCase):
    def _build(self):
        calls = {"prep": 0, "approve": 0}

        def prep(s):
            calls["prep"] += 1
            return {"prep_done": True}

        def approve(s):
            calls["approve"] += 1
            return {"approved": True}

        g = StateGraph()
        g.add_node("prep", prep)
        g.add_node("approve", approve)
        g.add_edge(START, "prep")
        g.add_edge("prep", "approve")
        g.add_edge("approve", END)
        return g.compile(), calls

    def test_interrupt_before_resume_no_rerun(self):
        ck = _tmp()
        compiled, calls = self._build()
        with self.assertRaises(GraphInterrupt):
            compiled.invoke({}, run_id="R1", checkpoint_dir=ck, interrupt_before=["approve"])
        self.assertEqual(calls["prep"], 1)
        self.assertEqual(calls["approve"], 0)
        out = compiled.resume("R1", checkpoint_dir=ck)
        self.assertEqual(calls["prep"], 1)  # 不重跑
        self.assertEqual(calls["approve"], 1)
        self.assertTrue(out["approved"])

    def test_interrupt_after_resume_no_rerun(self):
        ck = _tmp()
        compiled, calls = self._build()
        with self.assertRaises(GraphInterrupt):
            compiled.invoke({}, run_id="R2", checkpoint_dir=ck, interrupt_after=["prep"])
        self.assertEqual(calls["prep"], 1)
        out = compiled.resume("R2", checkpoint_dir=ck)
        self.assertEqual(calls["prep"], 1)  # 不重跑
        self.assertEqual(calls["approve"], 1)
        self.assertTrue(out["approved"])


class TestFailureRecovery(unittest.TestCase):
    def _build(self):
        calls = {"x": 0, "y": 0}

        def x(s):
            calls["x"] += 1
            return {"x_out": 10}

        def y(s):
            calls["y"] += 1
            if calls["y"] == 1:
                raise RuntimeError("y boom on first")
            return {"y_out": 20}

        g = StateGraph()
        g.add_node("x", x)
        g.add_node("y", y)
        g.add_edge(START, "x")
        g.add_edge(START, "y")
        g.add_edge("x", END)
        g.add_edge("y", END)
        return g.compile(), calls

    def test_recovery_runs_only_failed(self):
        ck = _tmp()
        compiled, calls = self._build()
        with self.assertRaises(NodeError):
            compiled.invoke({}, run_id="F1", checkpoint_dir=ck)
        self.assertEqual(calls["x"], 1)
        self.assertEqual(calls["y"], 1)
        out = compiled.resume("F1", checkpoint_dir=ck)
        # x 不重跑，y 重跑一次
        self.assertEqual(calls["x"], 1)
        self.assertEqual(calls["y"], 2)
        self.assertEqual(out["x_out"], 10)
        self.assertEqual(out["y_out"], 20)


class TestLoopsAndErrors(unittest.TestCase):
    def test_max_steps_raises(self):
        ck = _tmp()
        g = StateGraph()
        g.add_node("loop", lambda s: {"n": s.get("n", 0) + 1})
        g.add_edge(START, "loop")
        g.add_conditional_edges("loop", lambda s: "again", {"again": "loop"})
        with self.assertRaises(GraphError):
            g.compile().invoke({}, run_id="L1", checkpoint_dir=ck, max_steps=5)

    def test_dynamic_illegal_target(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(START, "a")
        g.add_conditional_edges("a", lambda s: "ghost")  # mapping=None，返回未注册节点
        with self.assertRaises(GraphError):
            g.compile().invoke({}, run_id="D1", checkpoint_dir=_tmp())

    def test_mapping_missing_key(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(START, "a")
        g.add_conditional_edges("a", lambda s: "unknown", {"known": "END"})
        with self.assertRaises(GraphError):
            g.compile().invoke({}, run_id="M1", checkpoint_dir=_tmp())


class TestGraphHashMismatch(unittest.TestCase):
    def test_mismatch_warns_but_continues(self):
        import logging
        ck = _tmp()

        def build():
            g = StateGraph()
            g.add_node("prep", lambda s: {"p": 1})
            g.add_node("approve", lambda s: {"a": 2})
            g.add_edge(START, "prep")
            g.add_edge("prep", "approve")
            g.add_edge("approve", END)
            return g.compile()

        compiled = build()
        with self.assertRaises(GraphInterrupt):
            compiled.invoke({}, run_id="H1", checkpoint_dir=ck, interrupt_before=["approve"])

        # 篡改 meta 中的 graph_hash，模拟图结构变化
        store = __import__("minigraph.checkpoint", fromlist=["CheckpointStore"]).CheckpointStore(ck, "H1")
        meta = store.load_meta()
        meta["graph_hash"] = "changed_hash_xyz"
        store.save_meta(meta["status"], meta["graph_hash"],
                       created_at=meta.get("created_at"), updated_at=meta.get("updated_at"))

        with self.assertLogs("minigraph", level="WARNING") as cm:
            out = compiled.resume("H1", checkpoint_dir=ck)
        self.assertTrue(any("图哈希不匹配" in m for m in cm.output))
        self.assertEqual(out["a"], 2)


if __name__ == "__main__":
    unittest.main()
