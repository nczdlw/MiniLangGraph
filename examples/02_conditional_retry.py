"""示例 2：条件重试流程（验收标准 2/4：条件重试、循环、动态路由可读结果）。

通过 JSON 流程文件（examples/flows/retry_flow.json）声明式装配：
  START -> attempt --route--> (retry: 回到 attempt 自环 | ok: finalize) -> END
attempt 前两次失败、第三次成功，演示"条件路由 + 自环重试"的同步汇聚语义。

也可经 CLI 运行（见文件末尾说明）。

运行: python examples/02_conditional_retry.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minigraph.loader import load_flow

FLOW = Path(__file__).resolve().parent / "flows" / "retry_flow.json"


def main() -> None:
    compiled = load_flow(str(FLOW))
    print("=== mermaid ===")
    print(compiled.to_mermaid())

    ckpt = tempfile.mkdtemp(prefix="mini_example2_")
    result = compiled.invoke({"tries": 0}, checkpoint_dir=ckpt)
    print("\n=== 终态 ===")
    print(result)
    # attempt 共执行 3 次（两次失败 + 一次成功），最终进入 finalize
    assert result.get("tries") == 3, f"重试次数应为 3，实际 {result.get('tries')}"
    assert result.get("status") == "ok", f"最终状态应为 ok，实际 {result.get('status')}"
    assert result.get("done") is True, f"应已完成，实际 {result}"
    print("\n✅ 条件重试流程通过：attempt 重试 2 次后成功，第 3 次进入 finalize。")


if __name__ == "__main__":
    main()

# ---- 等价的 CLI 运行方式 ----
#   python -m minigraph run \
#       --flow examples/flows/retry_flow.json \
#       --input '{"tries": 0}' \
#       --checkpoint-dir .checkpoints
#   python -m minigraph mermaid --flow examples/flows/retry_flow.json
#   python -m minigraph list --checkpoint-dir .checkpoints
