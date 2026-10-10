"""示例 1：线性工作流（验收标准 2：线性流程可运行）。

流程: START -> 抓取 -> 清洗 -> 入库 -> END
演示节点、固定边、状态增量累积、to_mermaid 与检查点目录。

运行: python examples/01_linear_flow.py
"""

import sys
import tempfile
from pathlib import Path

# 让示例无论从哪个工作目录运行都能 import 到 minigraph 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minigraph import START, END, StateGraph


def fetch(state: dict) -> dict:
    # 节点返回"状态增量"，引擎按 key 合并（无 reducer 时为后写覆盖）
    return {"raw": ["alice", "bob", "carol"]}


def clean(state: dict) -> dict:
    return {"clean": [name.strip().upper() for name in state["raw"]]}


def store(state: dict) -> dict:
    return {"stored": len(state["clean"])}


def main() -> None:
    g = StateGraph()
    g.add_node("fetch", fetch)
    g.add_node("clean", clean)
    g.add_node("store", store)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "clean")
    g.add_edge("clean", "store")
    g.add_edge("store", END)

    compiled = g.compile()
    print("=== mermaid ===")
    print(compiled.to_mermaid())

    ckpt = tempfile.mkdtemp(prefix="mini_example1_")
    result = compiled.invoke({}, checkpoint_dir=ckpt)
    print("\n=== 终态 ===")
    print(result)
    assert result == {
        "raw": ["alice", "bob", "carol"],
        "clean": ["ALICE", "BOB", "CAROL"],
        "stored": 3,
    }, f"终态与预期不符: {result}"
    print("\n✅ 线性流程运行通过，三个节点依次执行并累积状态。")


if __name__ == "__main__":
    main()
