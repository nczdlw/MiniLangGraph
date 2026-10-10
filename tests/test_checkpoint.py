"""checkpoint 模块单元测试：原子写、损坏/版本校验、keep_last、图哈希、运行 ID 冲突。"""

import json
import tempfile
import unittest

from minigraph.checkpoint import CheckpointStore, SCHEMA_VERSION
from minigraph.errors import CheckpointError, StateContractError


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mg_ckpt_")
        self.store = CheckpointStore(self.dir, "R1")

    def test_init_new_and_conflict(self):
        self.store.init_new()
        self.assertTrue(self.store.exists())
        with self.assertRaises(CheckpointError):
            self.store.init_new()  # 已存在 -> 冲突

    def test_atomic_write_creates_file(self):
        self.store.init_new()
        self.store.save_meta("running", "abc123")
        self.store.save_step({
            "schema_version": SCHEMA_VERSION, "run_id": "R1", "step": 1,
            "status": "running", "state": {"a": 1},
            "arrived": {}, "has_fired": {}, "pending": [], "completed": [],
            "collected_writes": {}, "error": None, "timestamp": "t",
        })
        self.assertTrue((self.store.run_dir / "step_0001.json").exists())
        self.assertTrue((self.store.run_dir / "meta.json").exists())

    def test_corrupted_checkpoint_raises(self):
        self.store.init_new()
        p = self.store.run_dir / "step_0001.json"
        p.write_text("{ this is not valid json ", encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.store.load_step(1)

    def test_version_too_high_raises(self):
        self.store.init_new()
        p = self.store.run_dir / "step_0001.json"
        p.write_text(json.dumps({"schema_version": 9999, "state": {}}), encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.store.load_step(1)

    def test_state_contract_violation_raises(self):
        self.store.init_new()
        with self.assertRaises(StateContractError):
            self.store.save_step({
                "schema_version": SCHEMA_VERSION, "run_id": "R1", "step": 1,
                "status": "running", "state": {"bad": {1, 2}},
                "arrived": {}, "has_fired": {}, "pending": [], "completed": [],
                "collected_writes": {}, "error": None, "timestamp": "t",
            })

    def test_keep_last_prunes_old(self):
        self.store.init_new()
        for i in range(1, 6):
            self.store.save_step({
                "schema_version": SCHEMA_VERSION, "run_id": "R1", "step": i,
                "status": "running", "state": {"i": i},
                "arrived": {}, "has_fired": {}, "pending": [], "completed": [],
                "collected_writes": {}, "error": None, "timestamp": "t",
            })
        self.store.prune_keep_last(2)
        remaining = self.store.list_steps()
        self.assertEqual(remaining, [4, 5])  # 仅保留最近 2 个 + meta

    def test_load_latest_returns_max_step(self):
        self.store.init_new()
        for i in range(1, 4):
            self.store.save_step({
                "schema_version": SCHEMA_VERSION, "run_id": "R1", "step": i,
                "status": "running", "state": {"i": i},
                "arrived": {}, "has_fired": {}, "pending": [], "completed": [],
                "collected_writes": {}, "error": None, "timestamp": "t",
            })
        step, _ = self.store.load_latest()
        self.assertEqual(step, 3)

    def test_meta_roundtrip_and_graph_hash(self):
        self.store.init_new()
        self.store.save_meta("running", "hashXYZ")
        meta = self.store.load_meta()
        self.assertEqual(meta["graph_hash"], "hashXYZ")
        self.assertEqual(meta["status"], "running")


if __name__ == "__main__":
    unittest.main()
