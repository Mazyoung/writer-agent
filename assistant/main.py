"""
一致性辅助工具 — CLI 入口

网页版 Claude 完成主体创作，本工具负责一致性维护。

用法:
    # 初始化
    python assistant/main.py init <小说名> ["故事前提"]

    # 一致性扫描
    python assistant/main.py check <小说名> --chapter N [--file path/to/chapter.md]

    # 事实提取 & 追踪文档更新
    python assistant/main.py sync <小说名> --chapter N [--file path/to/chapter.md]

    # AI句式检测
    python assistant/main.py style <小说名> --chapter N [--file path/to/chapter.md]

    # 设定变更检测
    python assistant/main.py snapshot <小说名>          # 创建当前文档快照
    python assistant/main.py detect <小说名>            # 对比快照，检测变更

    # 状态查看
    python assistant/main.py status <小说名>

工作流:
    init → 生成初始模板
    ↓
    网页 Claude 创作第N章 → 粘贴到 chapters/chapter_NNNN_draft.md
    ↓
    check → 一致性扫描，找出问题
    ↓
    网页 Claude 根据扫描结果修改正文
    ↓
    sync → 提取事实，更新追踪文档
    ↓
    snapshot → 为所有文档创建 .bak 快照
    ↓
    [你编辑 world_setting.md / book_plan.md / volume_plan.md]
    ↓
    detect → 检测变更 → 列出受影响的章节和追踪文档
    ↓
    网页 Claude 根据检测结果修复受影响文件
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from assistant.engine import ConsistencyEngine


def _safe_print(text: str, max_len: int = 2000):
    suffix = "..." if len(text) > max_len else ""
    safe = text[:max_len].encode('gbk', errors='replace').decode('gbk')
    print(safe + suffix)


def _read_chapter(name: str, chapter: int, filepath: str = "") -> str:
    if filepath:
        return Path(filepath).read_text(encoding="utf-8")
    eng = ConsistencyEngine(name)
    return eng._load_chapter_text(chapter)


def cmd_init(args):
    eng = ConsistencyEngine(args.name)
    premise = args.premise if hasattr(args, 'premise') and args.premise else ""
    eng.init_all(premise)


def cmd_check(args):
    chapter_text = _read_chapter(args.name, args.chapter, args.file)
    if not chapter_text:
        print(f"第{args.chapter}章正文不存在。用 --file 指定文件路径。")
        return

    eng = ConsistencyEngine(args.name)
    print(f"一致性扫描 — 第{args.chapter}章...")
    result = eng.check_chapter(args.chapter, chapter_text)
    if "raw" in result:
        _safe_print(result["raw"])


def cmd_sync(args):
    chapter_text = _read_chapter(args.name, args.chapter, args.file)
    if not chapter_text:
        print(f"第{args.chapter}章正文不存在。用 --file 指定文件路径。")
        return

    eng = ConsistencyEngine(args.name)
    print(f"事实提取 & 追踪更新 — 第{args.chapter}章...")
    changes = eng.sync_chapter(args.chapter, chapter_text)

    print(f"\n更新完成:")
    for key, val in changes.items():
        if val and key != "raw_analysis":
            print(f"  {key}: {val}")
    print(f"\n下一步: 编辑事实摘要 states/fact_digest_ch{args.chapter:04d}.md（人工复核）")


def cmd_style(args):
    chapter_text = _read_chapter(args.name, args.chapter, args.file)
    if not chapter_text:
        print(f"第{args.chapter}章正文不存在。用 --file 指定文件路径。")
        return

    eng = ConsistencyEngine(args.name)
    result = eng.check_style(args.chapter, chapter_text)
    print(result["summary"])


def cmd_snapshot(args):
    eng = ConsistencyEngine(args.name)
    files = eng.snapshot()
    print(f"快照已创建 ({len(files)} 个文件):")
    for f in files:
        print(f"  {f}")


def cmd_detect(args):
    eng = ConsistencyEngine(args.name)
    print("检测设定文档变更...")
    result = eng.detect_changes()

    found = False
    for doc_name in ["world_setting", "book_plan", "volume_plan"]:
        info = result.get(doc_name)
        if info:
            _safe_print(info["summary"])
            found = True

    if not found:
        print("  未检测到变更。所有文档与备份一致。")


def cmd_status(args):
    eng = ConsistencyEngine(args.name)
    eng.print_status()


def main():
    parser = argparse.ArgumentParser(description="一致性辅助工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    p = subparsers.add_parser("init", help="生成初始模板")
    p.add_argument("name"); p.add_argument("premise", nargs="?", default="")

    p = subparsers.add_parser("check", help="一致性扫描")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--file")

    p = subparsers.add_parser("sync", help="事实提取 & 追踪文档更新")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--file")

    p = subparsers.add_parser("style", help="AI句式检测")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--file")

    p = subparsers.add_parser("snapshot", help="创建文档快照")
    p.add_argument("name")

    p = subparsers.add_parser("detect", help="检测设定文档变更")
    p.add_argument("name")

    p = subparsers.add_parser("status", help="查看状态")
    p.add_argument("name")

    args = parser.parse_args()
    if not args.command:
        parser.print_help(); return

    cmds = {"init": cmd_init, "check": cmd_check, "sync": cmd_sync,
            "style": cmd_style, "snapshot": cmd_snapshot, "detect": cmd_detect,
            "status": cmd_status}
    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
