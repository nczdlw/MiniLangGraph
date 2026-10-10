"""检查点持久化：原子写、meta、keep_last、损坏/版本/图哈希校验。

设计要点（对应计划三.4）：
- 持久化状态限定为 JSON 原生类型；可选 encoder/decoder 仅扩展编解码，编解码后都必须符合契约。
- 写盘采用临时文件 + os.replace 原子替换，避免半写文件。
- 损坏或版本不支持的检查点返回可读错误，绝不静默从头运行。
- keep_last=N 仅保留最近 N 个 step 检查点；最新检查点与 meta 永不删除（恢复所需）。
- 图哈希不匹配仅警告并允许继续（不承诺检测函数体变化）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .errors import CheckpointError
from .state import is_json_native, validate_json_native

SCHEMA_VERSION = 1
ENGINE_VERSION = "0.1.0"


class CheckpointStore:
    """一个 run_id 对应一个子目录，内含 meta.json 与 step_NNNN.json。"""

    def __init__(
        self,
        checkpoint_dir,
        run_id: str,
        *,
        encoder: Optional[Callable[[object], object]] = None,
        decoder: Optional[Callable[[object], object]] = None,
    ) -> None:
        self.root = Path(checkpoint_dir)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self.encoder = encoder
        self.decoder = decoder

    # ---------- 目录与冲突 ----------
    def exists(self) -> bool:
        return self.run_dir.exists()

    def init_new(self) -> None:
        """创建新运行的目录；若已存在则视为运行 ID 冲突。"""
        if self.run_dir.exists():
            raise CheckpointError(
                "运行 ID 已存在，禁止覆盖活动/已存在运行；请使用新 run_id 或 resume",
                run_id=self.run_id,
            )
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 读写底层 ----------
    def _atomic_write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            raise CheckpointError(f"检查点文件不存在: {path}", run_id=self.run_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            raise CheckpointError(
                f"检查点已损坏，无法解析（不是合法 JSON）: {path} —— {e}",
                run_id=self.run_id,
            )
        if not isinstance(data, dict):
            raise CheckpointError(f"检查点格式错误，根元素不是对象: {path}", run_id=self.run_id)
        ver = data.get("schema_version")
        if not isinstance(ver, int):
            raise CheckpointError(f"检查点缺少 schema_version: {path}", run_id=self.run_id)
        if ver > SCHEMA_VERSION:
            raise CheckpointError(
                f"检查点 schema_version={ver} 高于当前引擎支持的 {SCHEMA_VERSION}，无法读取",
                run_id=self.run_id,
            )
        # 校验状态契约（state 字段）
        state = data.get("state")
        if state is not None and not is_json_native(state):
            raise CheckpointError(
                f"检查点 state 不是 JSON 原生类型，无法安全恢复: {path}",
                run_id=self.run_id,
            )
        return data

    # ---------- meta ----------
    def save_meta(
        self,
        status: str,
        graph_hash: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        meta = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "engine_version": ENGINE_VERSION,
            "graph_hash": graph_hash,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        self._atomic_write(self.run_dir / "meta.json", meta)

    def load_meta(self) -> dict:
        return self._read_json(self.run_dir / "meta.json")

    # ---------- step ----------
    def save_step(self, payload: dict) -> int:
        """写入一个 step 检查点，返回 step 编号。payload 必须含 step 字段。"""
        step = int(payload["step"])
        # 应用可选 encoder 于 state，并校验契约
        state = payload.get("state")
        if self.encoder is not None and state is not None:
            state = self.encoder(state)
            payload = dict(payload)
            payload["state"] = state
        if state is not None:
            validate_json_native(state)
        self._atomic_write(self.run_dir / f"step_{step:04d}.json", payload)
        return step

    def list_steps(self) -> list[int]:
        if not self.run_dir.exists():
            return []
        steps = []
        for p in self.run_dir.glob("step_*.json"):
            name = p.stem[len("step_") :]
            if name.isdigit():
                steps.append(int(name))
        return sorted(steps)

    def load_step(self, step: int) -> dict:
        data = self._read_json(self.run_dir / f"step_{step:04d}.json")
        # 应用可选 decoder 于 state
        if self.decoder is not None and data.get("state") is not None:
            data = dict(data)
            data["state"] = self.decoder(data["state"])
            validate_json_native(data["state"])
        return data

    def load_latest(self):
        """返回 (step, payload)；无检查点抛 CheckpointError。"""
        steps = self.list_steps()
        if not steps:
            raise CheckpointError(
                "没有可恢复的检查点（目录下无 step 文件）", run_id=self.run_id
            )
        return steps[-1], self.load_step(steps[-1])

    # ---------- keep_last ----------
    def prune_keep_last(self, keep_last: Optional[int]) -> None:
        if keep_last is None:
            return
        steps = self.list_steps()
        if len(steps) <= keep_last:
            return
        to_delete = steps[: len(steps) - keep_last]
        for s in to_delete:
            try:
                (self.run_dir / f"step_{s:04d}.json").unlink()
            except OSError:
                pass
