"""小说写作 Agent — CLI 入口（重构版）

用法:
    # 初始化
    python main.py init <小说名> ["前提"]           # Phase 1: 生成创作提案
    python main.py init <小说名> --confirm          # Phase 2: 确认提案 → 生成大纲

    # 规划
    python main.py plan <小说名> --chapter N        # 生成章规划 (Part A + Part B)
    python main.py plan <小说名> --chapter N --outline "..."  # 指定章大纲
    python main.py plan <小说名> --chapter N --instructions "..."  # 额外指示

    # 新卷（Rolling Horizon）
    python main.py new-volume <小说名>              # 当前卷归档 COMPLETED → 生成下一卷 ACTIVE 规划
    python main.py new-volume <小说名> --volume 3 --notes "..."  # 指定卷号 + 补充指示

    # 写作
    python main.py write <小说名> --chapter N       # DeepSeekWriter → ClaudeStylist → StyleChecker

    # 风格修改
    python main.py style <小说名> --chapter N       # 对已写章节做风格编辑
    python main.py style <小说名> --chapter N --feedback "..."  # 带人工反馈

    # 审阅
    python main.py review <小说名> --chapter N      # 章节后复盘 → 更新追踪文档

    # 状态
    python main.py status <小说名>                  # 查看进度

    # 回退
    python main.py snapshot <小说名>                # 创建快照
    python main.py rollback <小说名>                # 回退到快照
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config.settings import get_settings
from src.core.orchestrator import Orchestrator


def _safe_print(text: str, max_len: int = 500):
    suffix = "..." if len(text) > max_len else ""
    safe = text[:max_len].encode('gbk', errors='replace').decode('gbk')
    print(safe + suffix)


def _get_novel_dir(novel_id: str):
    settings = get_settings()
    d = settings.data_dir / "novels" / novel_id
    if not d.exists():
        print(f"小说 '{novel_id}' 不存在。请先运行 init。")
        return None
    return d


# ═══ 命令实现 ════════════════════════════════════════════════

def cmd_init(args):
    settings = get_settings()
    novel_dir = settings.data_dir / "novels" / args.name

    if args.confirm:
        # Phase 2: 确认提案 → 生成大纲
        if not novel_dir.exists():
            print(f"小说 '{args.name}' 尚未创建。先运行: python main.py init {args.name}")
            return
        orch = Orchestrator(args.name)
        proposal = orch.file_store.load_canonical("", "proposal")
        if not proposal:
            proposal = orch.file_store.load_latest("", "proposal")
        if not proposal:
            print("未找到创作提案。先运行: python main.py init <小说名>")
            return
        print("使用已确认的提案生成完整大纲...")
        orch.initialize_novel(proposal)
        print(f"\n下一步: python main.py plan {args.name} --chapter 1")
        return

    # Phase 1: 生成创作提案
    if novel_dir.exists() and not args.force:
        proposal_path = novel_dir / "proposal.md"
        if proposal_path.exists():
            print(f"小说 '{args.name}' 已存在。")
            print(f"  编辑 proposal.md 后: python main.py init {args.name} --confirm")
            print(f"  或 --force 重新生成提案")
            return
    orch = Orchestrator(args.name)
    hint = args.premise if hasattr(args, 'premise') and args.premise else ""
    orch.generate_proposal(hint)


def cmd_status(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    orch.print_status()


def cmd_plan(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    outline = getattr(args, 'outline', "") or ""
    instructions = getattr(args, 'instructions', "") or ""

    if args.interactive:
        _cmd_plan_interactive(orch, args)
        return

    try:
        orch.plan_chapter(args.chapter, outline, instructions)
    except FileNotFoundError as e:
        print(str(e))
        return
    print(f"\n下一步: python main.py write {args.name} --chapter {args.chapter}")


def _cmd_plan_interactive(orch, args):
    """交互式章规划 — 6 轮 Q&A。"""
    from src.utils.cli_helpers import InteractivePlanEngine, INTERACTIVE_QUESTIONS

    engine = InteractivePlanEngine(orch.file_store, args.chapter)
    ctx = engine.load_context()

    print(f"\n{'='*60}")
    print(f"  交互式章规划 — 第 {args.chapter} 章")
    print(f"{'='*60}")
    print(f"\n  已加载:")
    has_vp = bool(ctx.get('volume_event') and not ctx['volume_event'].startswith('（'))
    has_rels = bool(ctx.get('character_relations') and ctx['character_relations'] != '暂无')
    has_items = bool(ctx.get('items_tracking') and ctx['items_tracking'] != '暂无')
    print(f"    卷规划: {'Y' if has_vp else 'N'}  角色关系: {'Y' if has_rels else 'N'}  物品: {'Y' if has_items else 'N'}")
    print(f"\n  下面进行 6 轮问答。直接回车接受默认值，输入 . 跳过本轮。\n")

    for i in range(6):
        title = engine.get_question_title(i)
        question = engine.get_question(i)

        print(f"{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}\n")
        _safe_print(question, 2000)
        print()

        try:
            answer = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消。")
            return

        if answer == ".":
            answer = ""
        engine.record_answer(i, answer)
        print()

    # 组装最终 prompt 并生成规划
    print(f"{'='*60}")
    print(f"  生成完整章规划...")
    print(f"{'='*60}\n")

    final_prompt = engine.build_final_prompt()
    try:
        plan = orch.chapter_planner.plan_from_interactive_answers(
            args.chapter, engine.answers, engine.context)
    except FileNotFoundError as e:
        print(str(e))
        return

    print(f"  Part A: {len(plan.scenes)} 个场景")
    print(f"  已保存: outlines/chapter_plan_ch{args.chapter:04d}.md")
    print(f"\n  下一步: python main.py write {args.name} --chapter {args.chapter}")


def cmd_write(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    result = orch.write_chapter(args.chapter)
    print(f"\n下一步: python main.py review {args.name} --chapter {args.chapter}")


def cmd_style(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    feedback = getattr(args, 'feedback', "") or ""
    orch.style_edit(args.chapter, feedback)
    print(f"\n风格修改完成。如需继续调整: python main.py style {args.name} --chapter {args.chapter} --feedback \"...\"")


def cmd_review(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    orch.review_chapter(args.chapter)
    print(f"\n下一步: python main.py plan {args.name} --chapter {args.chapter + 1}")


def cmd_new_volume(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    notes = getattr(args, 'notes', "") or ""
    try:
        orch.start_new_volume(volume_number=args.volume, notes=notes)
    except (FileNotFoundError, ValueError) as e:
        print(str(e))
        return


def cmd_rag_index(args):
    """补齐/重建 RAG 索引 (E04)。"""
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    orch.rag_index_backfill(rebuild=args.rebuild)


def cmd_snapshot(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    orch.snapshot_all()
    print("快照完成。")


def cmd_rollback(args):
    if not _get_novel_dir(args.name):
        return
    orch = Orchestrator(args.name)
    restored = orch.rollback_all()
    if restored:
        print(f"已回退 {len(restored)} 个文件:")
        for r in restored:
            print(f"  {r}")
    else:
        print("没有找到 .bak 备份文件。")


# ═══ CLI ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="小说写作 Agent（重构版）")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # init
    p = subparsers.add_parser("init", help="Phase1:生成提案 / Phase2:--confirm 生成大纲")
    p.add_argument("name"); p.add_argument("premise", nargs="?", default="")
    p.add_argument("--force", action="store_true")
    p.add_argument("--confirm", action="store_true")

    # status
    p = subparsers.add_parser("status", help="查看进度")
    p.add_argument("name")

    # plan
    p = subparsers.add_parser("plan", help="生成章规划 (Part A + Part B)")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--outline"); p.add_argument("--instructions")
    p.add_argument("--interactive", action="store_true", help="交互式Q&A模式(待实现)")

    # write
    p = subparsers.add_parser("write", help="写一章 (DeepSeekWriter + ClaudeStylist)")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)

    # style
    p = subparsers.add_parser("style", help="Claude风格编辑")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--feedback", help="人工风格反馈")

    # review
    p = subparsers.add_parser("review", help="章节复盘 → 更新追踪文档")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)

    # new-volume
    p = subparsers.add_parser("new-volume", help="当前卷完成后生成下一卷规划 (Rolling Horizon)")
    p.add_argument("name")
    p.add_argument("--volume", type=int, help="新卷号（默认当前卷+1）")
    p.add_argument("--notes", help="给情节设计师的补充指示")

    # rag-index (E04)
    p = subparsers.add_parser("rag-index", help="补齐/重建RAG向量索引 (E04)")
    p.add_argument("name")
    p.add_argument("--rebuild", action="store_true", help="清空当前分支索引后重建")

    # snapshot / rollback
    p = subparsers.add_parser("snapshot", help="创建快照")
    p.add_argument("name")
    p = subparsers.add_parser("rollback", help="回退到快照")
    p.add_argument("name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        print("提示: 创建 .env 文件并设置 DEEPSEEK_API_KEY")

    cmds = {
        "init": cmd_init, "status": cmd_status,
        "plan": cmd_plan, "write": cmd_write,
        "style": cmd_style, "review": cmd_review,
        "snapshot": cmd_snapshot, "rollback": cmd_rollback,
        "new-volume": cmd_new_volume,
        "rag-index": cmd_rag_index,
    }
    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
