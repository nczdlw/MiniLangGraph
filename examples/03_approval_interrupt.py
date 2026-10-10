"""示例 3：审批中断与恢复（验收标准 2/5：审批中断、恢复后已完成节点不重跑）。

流程: START -> 准备 -> [中断点] 审批 -> 执行 -> END
先以 interrupt_before=["approve"] 在审批前中断；人工审批后 resume 并追加
state_update={"approved": True}。恢复后"准备"节点不应重跑（副作用计数核对）。

运行: python examples/03_approval_interrupt.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minigraph import START, END, GraphInterrupt, StateGraph

# 副作用计数器：用于核对"已完成节点不重跑"
side_effects = {"prepare": 0}


def prepare(state: dict) -> dict:
    side_effects["prepare"] += 1
    return {"prepared": True}


def approve(state: dict) -> dict:
    return {"approved": bool(state.get("approved", False))}


def execute(state: dict) -> dict:
    return {"executed": state.get("approved", False)}


def main() -> None:
    g = StateGraph()
    g.add_node("prepare", prepare)
    g.add_node("approve", approve)
    g.add_node("execute", execute)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "approve")
    g.add_edge("approve", "execute")
    g.add_edge("execute", END)

    compiled = g.compile()
    print("=== mermaid ===")
    print(compiled.to_mermaid())

    ckpt = tempfile.mkdtemp(prefix="mini_example3_")

    # 第一次运行：在 approve 前中断
    try:
        compiled.invoke({}, checkpoint_dir=ckpt, interrupt_before=["approve"])
    except GraphInterrupt as e:
        run_id = e.run_id
        print(f"\n=== 流程在审批前中断，等待人工审批（run_id={run_id}）===")

    # 人工审批通过：恢复并追加状态增量
    result = compiled.resume(
        run_id, state_update={"approved": True}, checkpoint_dir=ckpt
    )
    print("\n=== 终态 ===")
    print(result)

    assert side_effects["prepare"] == 1, (
        f"prepare 不应重跑，实际执行 {side_effects['prepare']} 次"
    )
    assert result.get("approved") is True
    assert result.get("executed") is True
    print("\n✅ 审批中断恢复通过：prepare 仅执行 1 次，恢复后已完成节点未重跑。")


if __name__ == "__main__":
    main()

# ---- 等价的 CLI 运行方式 ----
#   # 首次运行（在 approve 前中断，退出码 2）
#   python -m minigraph run \
#       --flow <本流程对应的 JSON> --checkpoint-dir .checkpoints \
#       --interrupt-before approve
#   # 审批通过后恢复（退出码 0）
#   python -m minigraph resume \
#       --flow <本流程对应的 JSON> --run-id <上一步的 run_id> \
#       --checkpoint-dir .checkpoints --state-update '{"approved": true}'
