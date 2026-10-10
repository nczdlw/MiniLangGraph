"""state 模块单元测试：reducer 合并、深拷贝隔离、JSON 原生契约。"""

import unittest

from minigraph.state import (
    apply_update,
    deep_copy_state,
    is_json_native,
    validate_json_native,
)
from minigraph.errors import StateContractError


class TestJsonNative(unittest.TestCase):
    def test_native_ok(self):
        self.assertTrue(is_json_native({"a": 1, "b": [1, "x", None], "c": {"d": True}}))

    def test_native_rejects_set(self):
        self.assertFalse(is_json_native({"a": {1, 2}}))

    def test_native_rejects_datetime(self):
        import datetime
        self.assertFalse(is_json_native({"t": datetime.datetime(2020, 1, 1)}))

    def test_native_rejects_non_str_key(self):
        self.assertFalse(is_json_native({1: "x"}))

    def test_validate_raises_with_path(self):
        with self.assertRaises(StateContractError) as cm:
            validate_json_native({"a": {"b": {1, 2}}})
        self.assertIn("a.b", str(cm.exception))


class TestDeepCopy(unittest.TestCase):
    def test_isolation(self):
        s = {"nested": [1, 2, {"k": [3]}]}
        cp = deep_copy_state(s)
        cp["nested"][2]["k"].append(4)
        self.assertEqual(s["nested"][2]["k"], [3])  # 原对象未被污染


class TestApplyUpdate(unittest.TestCase):
    def test_replace(self):
        out = apply_update({"a": 1}, {"a": 2})
        self.assertEqual(out["a"], 2)
        self.assertIsNot(out, {"a": 2})

    def test_reducer_sum(self):
        reducers = {"total": lambda cur, upd: (cur or 0) + upd}
        out = apply_update({"total": 1}, {"total": 5}, reducers)
        self.assertEqual(out["total"], 6)

    def test_reducer_deterministic_order(self):
        # 两个 reducer，按 key 顺序合并，结果确定
        calls = []
        def r1(cur, upd):
            calls.append("a")
            return (cur or 0) + upd
        def r2(cur, upd):
            calls.append("b")
            return (cur or 0) + upd
        reducers = {"a": r1, "b": r2}
        out = apply_update({}, {"b": 1, "a": 2}, reducers)
        self.assertEqual(out, {"a": 2, "b": 1})
        self.assertEqual(calls, ["b", "a"])  # 按 update 中键的顺序

    def test_new_key(self):
        out = apply_update({}, {"x": [1, 2]})
        self.assertEqual(out["x"], [1, 2])
        # 返回值深拷贝，外部修改不影响 state
        out["x"].append(3)
        self.assertEqual(out["x"], [1, 2, 3])

    def test_non_dict_update_raises(self):
        with self.assertRaises(StateContractError):
            apply_update({}, [1, 2, 3])

    def test_non_native_value_raises(self):
        with self.assertRaises(StateContractError):
            apply_update({}, {"bad": {1, 2}})


if __name__ == "__main__":
    unittest.main()
