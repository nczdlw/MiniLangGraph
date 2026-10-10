"""命令行接口：run / resume / list / mermaid。

退出码：成功 0；运行失败 1；中断/等待人工 2；参数错误沿用 argparse 的 2。
minigraph logger 默认静默，--verbose 显示节点、路由与超步进度。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .errors import GraphError, GraphInterrupt
from .loader import load_flow

logger = logging.getLogger("minigraph")


def _parse_json_arg(raw, what):
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"参数 {what} 不是合法 JSON: {e}", file=sys.stderr)
        sys.exit(2)


def _setup_verbose(verbose: bool) -> None:
    if verbose:
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("minigraph: %(levelname)s %(message)s"))
            logger.addHandler(h)


def _load_graph(flow):
    try:
        return load_flow(flow)
    except GraphError as e:
        print(f"加载流程失败: {e}", file=sys.stderr)
        sys.exit(2)


def _cmd_run(args):
    _setup_verbose(args.verbose)
    compiled = _load_graph(args.flow)
    inp = _parse_json_arg(args.input, "--input")
    try:
        result = compiled.invoke(
            inp,
            run_id=args.run_id,
            checkpoint_dir=args.checkpoint_dir,
            interrupt_before=args.interrupt_before or (),
            interrupt_after=args.interrupt_after or (),
            max_steps=args.max_steps,
            keep_last=args.keep_last,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except GraphInterrupt as e:
        print(f"流程在 run_id={e.run_id} 处中断，等待人工干预。恢复命令示例：", file=sys.stderr)
        print(f"  minigraph resume --flow {args.flow} --run-id {e.run_id} "
              f"--checkpoint-dir {args.checkpoint_dir}", file=sys.stderr)
        return 2
    except GraphError as e:
        print(f"运行失败: {e}", file=sys.stderr)
        return 1


def _cmd_resume(args):
    _setup_verbose(args.verbose)
    compiled = _load_graph(args.flow)
    state_update = _parse_json_arg(args.state_update, "--state-update")
    try:
        result = compiled.resume(
            args.run_id,
            state_update=state_update or None,
            checkpoint_dir=args.checkpoint_dir,
            interrupt_before=args.interrupt_before or (),
            interrupt_after=args.interrupt_after or (),
            max_steps=args.max_steps,
            keep_last=args.keep_last,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except GraphInterrupt as e:
        print(f"流程在 run_id={e.run_id} 处中断，等待人工干预。", file=sys.stderr)
        return 2
    except GraphError as e:
        print(f"恢复失败: {e}", file=sys.stderr)
        return 1


def _cmd_list(args):
    ckpt = Path(args.checkpoint_dir)
    if not ckpt.exists():
        print(f"检查点目录不存在: {ckpt}", file=sys.stderr)
        return 0
    rows = []
    for d in sorted(ckpt.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append((meta.get("run_id", d.name), meta.get("status", "?"),
                    meta.get("graph_hash", "?"), meta.get("updated_at", "?")))
    if not rows:
        print("没有已保存的运行。")
        return 0
    print(f"{'RUN_ID':<24} {'STATUS':<12} {'GRAPH_HASH':<18} UPDATED_AT")
    for r in rows:
        print(f"{r[0]:<24} {r[1]:<12} {r[2]:<18} {r[3]}")
    return 0


def _cmd_mermaid(args):
    compiled = _load_graph(args.flow)
    print(compiled.to_mermaid())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="minigraph", description="本地简化版 LangGraph 工作流引擎")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--flow", required=True, help="JSON 流程文件路径")
        sp.add_argument("--checkpoint-dir", default=".checkpoints", help="检查点目录")
        sp.add_argument("--interrupt-before", action="append", default=[], help="在该节点前中断（可重复）")
        sp.add_argument("--interrupt-after", action="append", default=[], help="在该节点后中断（可重复）")
        sp.add_argument("--max-steps", type=int, default=100, help="最大超步数")
        sp.add_argument("--keep-last", type=int, default=None, help="保留最近 N 个 step 检查点")
        sp.add_argument("--verbose", action="store_true", help="显示节点/路由/超步进度")

    sp_run = sub.add_parser("run", help="运行流程")
    add_common(sp_run)
    sp_run.add_argument("--input", default=None, help="初始状态 JSON 字符串")
    sp_run.add_argument("--run-id", default=None, help="运行 ID（默认自动生成 UUID）")
    sp_run.set_defaults(func=_cmd_run)

    sp_res = sub.add_parser("resume", help="从检查点续跑/恢复")
    add_common(sp_res)
    sp_res.add_argument("--run-id", required=True, help="要恢复的运行 ID")
    sp_res.add_argument("--state-update", default=None, help="追加到当前状态的状态增量 JSON")
    sp_res.set_defaults(func=_cmd_resume)

    sp_list = sub.add_parser("list", help="列出已保存的运行")
    sp_list.add_argument("--checkpoint-dir", default=".checkpoints")
    sp_list.set_defaults(func=_cmd_list)

    sp_mm = sub.add_parser("mermaid", help="输出流程的 mermaid 图")
    sp_mm.add_argument("--flow", required=True)
    sp_mm.set_defaults(func=_cmd_mermaid)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
