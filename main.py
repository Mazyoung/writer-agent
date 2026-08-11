"""小说写作 Agent — CLI 入口（重构版）

用法:
    # 初始化
    python main.py init <小说名> ["前提"]           # Phase 1: 生成创作提案
    python main.py init <小说名> --confirm          # Phase 2: 确认提案 → 生成大纲

    # standalone / 调试规划（不属于正式 ChapterWorkflow，write 不会接续其结果）
    python main.py plan <小说名> --chapter N        # 独立生成 Chapter Plan
    python main.py plan <小说名> --chapter N --outline "..."  # 指定章大纲
    python main.py plan <小说名> --chapter N --instructions "..."  # 额外指示

    # 新卷（Rolling Horizon）
    python main.py new-volume <小说名>              # 人工 close 后生成下一卷 DRAFT，直接审阅编辑
    python main.py new-volume <小说名> --volume 3 --notes "..."  # 指定卷号 + 补充指示

    # 写作
    python main.py write <小说名> --chapter N       # DeepSeekWriter → ClaudeStylist → Prose Review

    # 风格修改
    python main.py style <小说名> --chapter N       # 对已写章节做风格编辑
    python main.py style <小说名> --chapter N --feedback "..."  # 带人工反馈

    # write 已包含审阅、canonical commit、Fact Digest 与 RAG

    # 状态
    python main.py status <小说名>                  # 查看进度

"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config.settings import get_settings
from src.core.novel_status import NovelStatusService
from src.planning.chapter_planning_service import ChapterPlanningService
from src.planning.novel_lifecycle import NovelLifecycleService
from src.storage.file_store import FileStore
from src.storage.embedding_config import (
    NovelEmbeddingConfigStore,
    NovelEmbeddingRuntime,
    probe_new_embedding,
)
from src.storage.rag_maintenance_v2 import RAGMaintenanceService
from src.storage.story_savepoint import SavepointError, StorySavepointManager
from src.workflows.chapter_editing import ChapterEditingService
from src.workflows.chapter_runner import (
    repair_chapter_derivation,
    restart_chapter_workflow,
    resume_chapter_workflow,
    run_chapter_workflow,
)
from src.workflows.continuation import NovelContinuationService


def _safe_print(text: str, max_len: int = 500):
    suffix = "..." if len(text) > max_len else ""
    safe = text[:max_len].encode('gbk', errors='replace').decode('gbk')
    print(safe + suffix)


def _get_novel_dir(novel_id: str):
    settings = get_settings()
    novel_dir = settings.data_dir / "novels" / novel_id
    if not novel_dir.exists():
        print(f"小说 '{novel_id}' 不存在。请先运行 init。")
        return None
    try:
        NovelEmbeddingConfigStore(settings.data_dir).load(novel_id)
    except ValueError as exc:
        print(f"错误：{exc}")
        return None
    file_store = FileStore(novel_id, settings.data_dir)
    migrated = file_store.migrate_legacy_canonical_if_needed()
    if migrated:
        print(f"  [迁移] 已创建 Canonical 副本：{list(migrated.keys())}")
    return novel_dir


def _confirm_and_bind_embedding(novel_id: str) -> bool:
    settings = get_settings()
    store = NovelEmbeddingConfigStore(settings.data_dir)
    try:
        candidate = probe_new_embedding(novel_id, settings)
    except ValueError as exc:
        print(str(exc))
        return False
    print("\nEmbedding 配置\n")
    if candidate.embedding_mode == "local":
        print("当前 Embedding 方式：")
        print("  Chroma 内置 Embedding（本地）\n")
    else:
        print("当前 Embedding 方式：")
        print("  OpenAI-compatible Embedding API\n")
    print("模型：")
    print(f"  {candidate.embedding_model}\n")
    print("向量维度：")
    print(f"  {candidate.embedding_dimensions}\n")
    print("该 Embedding 模型及向量维度将在小说初始化后永久绑定，")
    print("之后无法修改。\n")
    if candidate.embedding_mode == "local":
        print("如果希望使用更好的网络 API Embedding，")
        print("请在初始化前按照 README 修改配置。\n")
    else:
        print("API Key 和 API 地址属于运行时连接配置，")
        print("以后可以修改，但必须继续访问相同的 Embedding 模型。\n")
    prompt = "是否使用当前 Embedding 配置继续初始化？[y/N]: "
    try:
        confirmed = input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        confirmed = False
    if not confirmed:
        print("已取消初始化；未创建小说或内部 Embedding 配置。")
        return False
    store.create(candidate)
    print(
        f"已固定 Embedding 配置：{candidate.embedding_mode} / "
        f"{candidate.embedding_model} / {candidate.embedding_dimensions}"
    )
    return True


def _validate_existing_embedding(novel_id: str) -> bool:
    settings = get_settings()
    store = NovelEmbeddingConfigStore(settings.data_dir)
    try:
        config = store.load(novel_id)
        NovelEmbeddingRuntime(config, settings)
    except ValueError as exc:
        print(f"错误：{exc}")
        return False
    return True


# ═══ 命令实现 ════════════════════════════════════════════════

def cmd_init(args):
    settings = get_settings()
    novel_dir = settings.data_dir / "novels" / args.name

    if args.confirm:
        # Phase 2: 确认提案 → 生成大纲
        if not novel_dir.exists():
            print(f"小说 '{args.name}' 尚未创建。先运行: python main.py init {args.name}")
            return
        if not _validate_existing_embedding(args.name):
            return
        lifecycle = NovelLifecycleService(args.name)
        novel_dir = lifecycle.file_store.root
        canonical_path = novel_dir / "proposal.md"

        if canonical_path.exists():
            proposal = canonical_path.read_text(encoding="utf-8")
            print("使用当前 proposal.md")
        else:
            print("未找到 proposal.md。请先运行: python main.py init <小说名>")
            return
        lifecycle.initialize_novel(proposal)
        return

    # Phase 1: 生成创作提案
    if not novel_dir.exists() and not _confirm_and_bind_embedding(args.name):
        return
    if novel_dir.exists() and not _validate_existing_embedding(args.name):
        return
    if novel_dir.exists() and not args.force:
        proposal_path = novel_dir / "proposal.md"
        if proposal_path.exists():
            print(f"小说 '{args.name}' 已存在。")
            print(f"  请直接编辑 proposal.md，然后运行: python main.py init {args.name} --confirm")
            print(f"  或 --force 重新生成提案")
            return
    lifecycle = NovelLifecycleService(args.name)
    hint = args.premise if hasattr(args, 'premise') and args.premise else ""
    lifecycle.generate_proposal(hint)


def cmd_status(args):
    if not _get_novel_dir(args.name):
        return
    NovelStatusService(args.name).print_status()


def cmd_plan(args):
    if not _get_novel_dir(args.name):
        return
    print("\n[standalone/debug] plan 不属于正式 ChapterWorkflow；其结果不会被 write 接续或采用。")
    planning = ChapterPlanningService(args.name)
    outline = getattr(args, 'outline', "") or ""
    instructions = getattr(args, 'instructions', "") or ""

    if args.interactive:
        _cmd_plan_interactive(planning, args)
        return

    try:
        planning.plan_chapter(args.chapter, outline, instructions)
    except FileNotFoundError as e:
        print(str(e))
        return
    print("\nstandalone/debug plan 已完成；正式章节请直接使用 write，其 Workflow 会重新生成本次章节规划。")


def _cmd_plan_interactive(planning, args):
    """交互式章规划 — 6 轮 Q&A。"""
    from src.utils.cli_helpers import InteractivePlanEngine, INTERACTIVE_QUESTIONS

    engine = InteractivePlanEngine(planning.file_store, args.chapter)
    ctx = engine.load_context()

    print(f"\n{'='*60}")
    print(f"  交互式章规划 — 第 {args.chapter} 章")
    print(f"{'='*60}")
    print(f"\n  已加载:")
    has_vp = bool(ctx.get('volume_plan') and not ctx['volume_plan'].startswith('（'))
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

    try:
        plan = planning.chapter_planner.plan_from_interactive_answers(
            args.chapter, engine.answers, engine.context)
    except FileNotFoundError as e:
        print(str(e))
        return

    if not plan.strip():
        print("  Chapter Plan 生成失败：返回内容为空。")
        return
    print(f"  已保存: outlines/chapter_plan_ch{args.chapter:04d}.md")
    print("\n  standalone/debug plan 已完成；正式 write 会重新生成本次章节规划，不接续此结果。")


def _print_chapter_result(name: str, chapter: int, result: dict) -> None:
    status = result.get("workflow_status", "error")
    if status == "DERIVED_READY":
        print(f"\n第 {chapter} 章已完整完成（DERIVED_READY）。")
        return
    if status == "DERIVATION_ERROR":
        stage = result.get("failed_derivation_stage", "UNKNOWN")
        error = result.get("derivation_error", "未知派生错误")
        print("\nDERIVATION_ERROR")
        print(f"Chapter: {chapter}")
        print("Canonical Commit: YES")
        print(f"Failed Stage: {stage}")
        print(f"Error: {error}")
        print("Recovery State Saved: YES")
        print(
            "再次运行 write 或 continue 将从该派生阶段继续；"
            "repair-derivation 仅保留作 debug/maintenance。"
        )
        return
    if status == "RESTARTED":
        print(f"\n第 {chapter} 章的 Pre-Canonical 内容已放弃，Chapter Intent 已保留。")
        return
    if status == "BLOCKED":
        print(f"\n{result.get('error', '当前没有合法的下一步。')}")
        return
    if status != "WAITING_HUMAN":
        print(f"\n章节工作流已中止：{result.get('error', status)}")
        return

    pending = result.get("interrupts", [])
    payload = pending[0].get("value", {}) if pending else {}
    kind = payload.get("type", "unknown")
    if kind == "human_writing":
        print("\n【人工创作模式】")
        print(f"\n第 {chapter} 章正在等待作者提交正文。")
        print(f"Writing Context：{payload.get('writing_context_path', '')}")
        print(
            f"python main.py write {name} --chapter {chapter} "
            "--action submit --file <正文文件>"
        )
        return

    labels = {
        "plan_review": "Plan Review",
        "chapter_review": "Prose Review",
        "final_author_approval": "Prose Review",
        "human_final_approval": "Consistency Review",
        "review_override_confirmation": "Review Override",
    }
    print(f"\n第 {chapter} 章正在等待人工处理。")
    print(f"\n{labels.get(kind, kind)}：{payload.get('verdict', 'UNKNOWN')}")
    issues = []
    for item in [
        *payload.get("t1_issues", []),
        *payload.get("reasons", []),
    ]:
        if item and item not in issues:
            issues.append(item)
    if issues:
        print("\n具体问题：")
        for index, issue in enumerate(issues, 1):
            print(f"{index}. {issue}")
    print("\n可选操作：")
    display = {
        "agent_edit": "agent_edit",
        "human_edit": "human_edit",
        "regenerate_prose": "regenerate_prose",
        "restart": "restart",
        "approve": "approve",
        "confirm_override": "confirm_override",
        "back": "back",
    }
    for action in payload.get("allowed_actions", []):
        print(f"  {display.get(action, action)}")
    if payload.get("edit_path"):
        print(f"\n编辑文件：{payload['edit_path']}")


def _waiting_payload(result: dict) -> dict:
    pending = result.get("interrupts", [])
    return pending[0].get("value", {}) if pending else {}


def _print_review_context(chapter: int, payload: dict) -> None:
    labels = {
        "plan_review": "Plan Review",
        "chapter_review": "Prose Review",
        "final_author_approval": "Prose Review",
        "human_final_approval": "Consistency Review",
        "review_override_confirmation": "Review Override",
    }
    kind = payload.get("type", "unknown")
    print(f"\n第 {chapter} 章 {labels.get(kind, kind)}：{payload.get('verdict', 'UNKNOWN')}")
    issues = []
    for item in [*payload.get("t1_issues", []), *payload.get("reasons", [])]:
        if item and item not in issues:
            issues.append(item)
    if issues:
        print("\n具体问题：")
        for index, issue in enumerate(issues, 1):
            print(f"{index}. {issue}")


def _interactive_resume_value(
    novel_id: str, chapter: int, payload: dict
) -> dict | None:
    kind = payload.get("type", "unknown")
    allowed = set(payload.get("allowed_actions", []))
    if kind == "human_writing":
        print("\n【人工创作模式】")
        print(f"Writing Context：{payload.get('writing_context_path', '')}")
        print(
            f"兼容命令：python main.py write {novel_id} --chapter {chapter} "
            "--action submit --file <正文文件>"
        )
        entries = [("submit", "提交人工正文文件"), ("restart", "重启本章")]
    elif kind == "plan_review":
        _print_review_context(chapter, payload)
        entries = [
            ("agent_edit", "Agent 自动修改"),
            ("human_edit", "人工修改"),
            ("restart", "重启本章"),
            ("approve", "批准并继续"),
        ]
    elif kind in {"chapter_review", "final_author_approval"}:
        _print_review_context(chapter, payload)
        entries = [
            ("agent_edit", "Agent 自动修改"),
            ("human_edit", "人工修改"),
            ("regenerate_prose", "重新生成正文"),
            ("restart", "重启本章"),
            ("approve", "批准并继续"),
        ]
    elif kind == "human_final_approval":
        _print_review_context(chapter, payload)
        entries = [
            ("human_edit", "人工修改"),
            ("restart", "重启本章"),
            ("approve", "批准并继续"),
        ]
    elif kind == "review_override_confirmation":
        _print_review_context(chapter, payload)
        print(f"\n{payload.get('message', '')}")
        entries = [
            ("confirm_override", "确认 Override 并继续"),
            ("back", "返回上一步"),
        ]
    else:
        _print_review_context(chapter, payload)
        labels = {
            "agent_edit": "Agent 自动修改",
            "human_edit": "人工修改",
            "regenerate_prose": "重新生成正文",
            "restart": "重启本章",
            "approve": "批准并继续",
            "confirm_override": "确认 Override 并继续",
            "back": "返回上一步",
            "submit": "提交",
        }
        entries = [(action, labels.get(action, action)) for action in allowed]

    entries = [(action, label) for action, label in entries if action in allowed]
    print("\n请选择操作：\n")
    for index, (_action, label) in enumerate(entries, 1):
        print(f"[{index}] {label}")
    print("[0] 暂停并退出，稍后继续")

    while True:
        try:
            selected = input("\n请输入选择：").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            print("\n已暂停，当前 WAITING_HUMAN checkpoint 保持不变。")
            return None
        if selected == "0":
            print("\n已暂停，当前 WAITING_HUMAN checkpoint 保持不变。")
            return None
        if not selected.isdigit() or not 1 <= int(selected) <= len(entries):
            print("无效选择，请重新输入。")
            continue
        action = entries[int(selected) - 1][0]
        break

    if action == "agent_edit":
        try:
            feedback = input(
                "\n请输入给 Agent 的补充修改意见：\n"
                "（直接回车则仅使用 Reviewer 已给出的修改问题）\n\n> "
            ).strip()
        except (EOFError, KeyboardInterrupt, OSError):
            print("\n已暂停，当前 WAITING_HUMAN checkpoint 保持不变。")
            return None
        if not feedback and payload.get("verdict") == "PASS":
            feedback = "请在保持已通过内容的前提下，根据当前作者选择进行局部优化。"
        return {"action": action, "feedback": feedback}

    if action == "human_edit":
        edit_path = payload.get("edit_path", "")
        print(f"\n请编辑：\n{edit_path}\n")
        while True:
            try:
                answer = input("编辑完成后按 Enter 继续。输入 q 可暂时退出。\n\n> ").strip()
            except (EOFError, KeyboardInterrupt, OSError):
                answer = "q"
            if answer.lower() == "q":
                print("\n已暂停，当前 WAITING_HUMAN checkpoint 保持不变。")
                return None
            path = Path(edit_path)
            if not path.is_absolute():
                path = FileStore(
                    novel_id, get_settings().data_dir
                ).root / edit_path
            if not path.is_file() or not path.read_text(encoding="utf-8").strip():
                print("目标编辑文件不存在或为空，请完成编辑后按 Enter 继续。")
                continue
            return {"action": action, "feedback": ""}

    if action == "submit":
        try:
            candidate_file = input("\n请输入人工正文文件路径：\n\n> ").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            candidate_file = ""
        if not candidate_file:
            print("未提供正文文件，继续等待。")
            return {}
        return {"action": action, "candidate_file": candidate_file}

    if action == "restart":
        print(
            f"\n确认重启第 {chapter} 章？\n"
            "这会清除本章当前 Plan / Prose / Review / Context / RAG / checkpoint，\n"
            "保留 Human Intent。\nCanonical 章节不得 restart。"
        )
        try:
            confirmed = input("\n[y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt, OSError):
            confirmed = ""
        if confirmed != "y":
            print("已取消重启，继续等待。")
            return {}
    return {"action": action, "feedback": ""}


def _run_interactive_chapter(
    novel_id: str,
    chapter: int,
    result: dict,
    chapter_intent: str = "",
) -> None:
    while result.get("workflow_status") == "WAITING_HUMAN":
        resume_value = _interactive_resume_value(
            novel_id, chapter, _waiting_payload(result)
        )
        if resume_value is None:
            return
        if not resume_value:
            continue
        try:
            result = resume_chapter_workflow(
                novel_id, chapter, resume_value
            )
        except ValueError as exc:
            print(f"\n  章节工作流恢复请求被拒绝：{exc}")
            print("当前 checkpoint 未被消费，请修正后继续。")
            continue
        if result.get("workflow_status") == "RESTARTED":
            result = run_chapter_workflow(
                novel_id,
                chapter,
                chapter_intent=chapter_intent,
            )
    _print_chapter_result(novel_id, chapter, result)


def cmd_write(args):
    if not _get_novel_dir(args.name):
        return

    feedback = getattr(args, "feedback", "") or ""
    requested_action = getattr(args, "action", None)
    try:
        if not requested_action:
            from src.storage.chapter_completion import is_derived_ready
            fs = FileStore(args.name, get_settings().data_dir)
            if (
                fs.canonical_chapter_path(args.chapter).exists()
                and not is_derived_ready(fs, args.chapter)
            ):
                result = repair_chapter_derivation(args.name, args.chapter)
                _print_chapter_result(args.name, args.chapter, result)
                return
        if requested_action:
            result = resume_chapter_workflow(
                args.name,
                args.chapter,
                {
                    "action": requested_action,
                    "feedback": feedback,
                    "candidate_file": getattr(args, "candidate_file", "") or "",
                },
            )
        else:
            result = run_chapter_workflow(
                args.name,
                args.chapter,
                chapter_outline=getattr(args, "outline", "") or "",
                chapter_intent=getattr(args, "chapter_intent", "") or "",
            )
    except ValueError as exc:
        print(f"\n  章节工作流恢复请求被拒绝：{exc}")
        return

    if result.get("workflow_status") == "RESTARTED":
        result = run_chapter_workflow(
            args.name,
            args.chapter,
            chapter_intent=getattr(args, "chapter_intent", "") or "",
        )
    if result.get("workflow_status") == "WAITING_HUMAN":
        _run_interactive_chapter(
            args.name, args.chapter, result,
            getattr(args, "chapter_intent", "") or "",
        )
        return
    _print_chapter_result(args.name, args.chapter, result)


def cmd_style(args):
    if not _get_novel_dir(args.name):
        return
    feedback = getattr(args, 'feedback', "") or ""
    ChapterEditingService(args.name).style_edit(args.chapter, feedback)
    print(f"\n风格修改完成。如需继续调整: python main.py style {args.name} --chapter {args.chapter} --feedback \"...\"")


def cmd_repair_derivation(args):
    if not _get_novel_dir(args.name):
        return
    try:
        result = repair_chapter_derivation(args.name, args.chapter)
    except ValueError as exc:
        print(f"Derivation 修复请求被拒绝：{exc}")
        return
    print(f"Derivation 修复结果：{result.get('workflow_status', 'ERROR')}")


def cmd_restart(args):
    if not _get_novel_dir(args.name):
        return
    try:
        restart_chapter_workflow(args.name, args.chapter)
        result = run_chapter_workflow(args.name, args.chapter)
    except ValueError as exc:
        print(f"restart 被拒绝：{exc}")
        return
    if result.get("workflow_status") == "WAITING_HUMAN":
        _run_interactive_chapter(args.name, args.chapter, result)
        return
    _print_chapter_result(args.name, args.chapter, result)


def cmd_continue(args):
    if not _get_novel_dir(args.name):
        return
    try:
        result = NovelContinuationService(args.name).continue_once()
    except ValueError as exc:
        print(f"continue 被拒绝：{exc}")
        return
    chapter = int(result.get("chapter_index", 0) or 0)
    if result.get("workflow_status") == "WAITING_HUMAN":
        _run_interactive_chapter(args.name, chapter, result)
        return
    _print_chapter_result(args.name, chapter, result)


def cmd_run(args):
    if not _get_novel_dir(args.name):
        return
    try:
        result = NovelContinuationService(args.name).run_to_chapter(
            args.to_chapter
        )
    except ValueError as exc:
        print(f"run 被拒绝：{exc}")
        return
    _print_chapter_result(
        args.name, int(result.get("chapter_index", args.to_chapter)), result
    )


def cmd_close_volume(args):
    if not _get_novel_dir(args.name):
        return
    try:
        NovelLifecycleService(args.name).close_volume()
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))


def cmd_new_volume(args):
    if not _get_novel_dir(args.name):
        return
    lifecycle = NovelLifecycleService(args.name)
    notes = getattr(args, 'notes', "") or ""
    try:
        lifecycle.start_new_volume(volume_number=args.volume, notes=notes)
    except (FileNotFoundError, ValueError) as e:
        print(str(e))
        return


def cmd_rag_index(args):
    """补齐/重建 RAG 索引 (E04 / E06.2.1)。"""
    if not _get_novel_dir(args.name):
        return
    result = RAGMaintenanceService(args.name).run(rebuild=args.rebuild)
    if result.get("rebuild_aborted"):
        print(f"\n  RAG 重建已中止。未修改索引。")


def cmd_savepoint(args):
    novel_dir = get_settings().data_dir / "novels" / args.name
    if not novel_dir.is_dir():
        print(f"小说 '{args.name}' 不存在。请先运行 init。")
        return
    manager = StorySavepointManager(args.name)
    try:
        if args.savepoint_action == "create":
            manifest = manager.create()
            print(
                f"Story Savepoint {manifest['savepoint_id']} 已创建并达到 READY "
                f"（Chapter {manifest['chapter_index']}）。"
            )
            return
        if args.savepoint_action == "list":
            savepoints = manager.list()
            if not savepoints:
                print("当前没有 READY Story Savepoint。")
                return
            print("READY Story Savepoint：")
            for item in savepoints:
                print(
                    f"  {item['savepoint_id']}  Chapter {item['chapter_index']}  "
                    f"{item['created_at']}"
                )
            return
        if args.savepoint_action == "verify":
            manifest = manager.verify(args.savepoint_id)
            print(
                f"Story Savepoint {manifest['savepoint_id']} 完整性验证通过（READY）。"
            )
            return
        if args.savepoint_action == "load":
            savepoint_id = args.savepoint_id.upper()
            print(f"\n即将加载 Savepoint {savepoint_id}。\n")
            print("此操作会将当前小说恢复到该 Savepoint 对应的完整状态。")
            print("请确认需要保留的当前内容已经另行保存。")
            print("其他 READY Story Savepoint 不受影响，之后仍可重新加载。")
            novel_confirmation = input("\n请输入小说名称进行确认：").strip()
            if novel_confirmation != args.name:
                print("小说名不匹配，已取消 Load。")
                return
            exact = input(f"请再次输入 LOAD {savepoint_id}：").strip()
            if exact != f"LOAD {savepoint_id}":
                print("确认文本不匹配，已取消 Load。")
                return
            manifest = manager.load(savepoint_id)
            print(
                f"Story Savepoint {manifest['savepoint_id']} 已加载；"
                f"当前创作世界恢复到 Chapter {manifest['chapter_index']}。"
            )
    except SavepointError as exc:
        print(f"Story Savepoint 操作失败：{exc}")




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
    p = subparsers.add_parser(
        "plan", help="standalone/debug：独立生成章规划（不属于正式 Workflow）"
    )
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--outline"); p.add_argument("--instructions")
    p.add_argument("--interactive", action="store_true", help="交互式Q&A模式(待实现)")

    # write
    p = subparsers.add_parser("write", help="运行或恢复完整章节 LangGraph workflow")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--outline", help="兼容入口：指定本章事件概要")
    p.add_argument(
        "--intent", "--instructions", dest="chapter_intent",
        help="可选 Chapter Intent（--instructions 为兼容别名）",
    )
    p.add_argument("--feedback", help="提供给 agent edit 的可选人工反馈")
    p.add_argument("--file", dest="candidate_file", help="人工正文 Candidate 文件路径")
    p.add_argument(
        "--action",
        choices=[
            "submit", "approve", "confirm_override", "back",
            "agent_edit", "human_edit", "regenerate_prose", "restart",
        ],
        help="处理当前章节工作流人工检查点",
    )

    # continue / autonomous run / restart
    p = subparsers.add_parser("continue", help="从小说当前唯一合法状态继续")
    p.add_argument("name")
    p = subparsers.add_parser("run", help="自主模式连续创作到明确目标章节")
    p.add_argument("name")
    p.add_argument("--to-chapter", type=int, required=True)
    p = subparsers.add_parser("restart", help="放弃本章 Pre-Canonical 内容并重新规划")
    p.add_argument("name")
    p.add_argument("--chapter", type=int, required=True)

    # style
    p = subparsers.add_parser("style", help="Claude风格编辑")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--feedback", help="人工风格反馈")

    # repair-derivation
    p = subparsers.add_parser("repair-derivation", help="恢复 canonical 后未完成的 derivation")
    p.add_argument("name"); p.add_argument("--chapter", type=int, required=True)

    # volume lifecycle
    p = subparsers.add_parser("close-volume", help="人工关闭当前 ACTIVE 卷")
    p.add_argument("name")
    # new-volume
    p = subparsers.add_parser("new-volume", help="已人工关闭当前卷后生成下一卷 DRAFT")
    p.add_argument("name")
    p.add_argument("--volume", type=int, help="新卷号（默认当前卷+1）")
    p.add_argument("--notes", help="给情节设计师的补充指示")

    # rag-index (E04)
    p = subparsers.add_parser("rag-index", help="补齐/重建RAG向量索引 (E04)")
    p.add_argument("name")
    p.add_argument("--rebuild", action="store_true", help="清空当前分支索引后重建")

    # story savepoint (E07 closure)
    p = subparsers.add_parser("savepoint", help="Story Savepoint 创建、校验与加载")
    savepoint_subparsers = p.add_subparsers(
        dest="savepoint_action", required=True, help="Savepoint 操作"
    )
    for action, help_text in (
        ("create", "为当前 DERIVED_READY 世界创建 Savepoint"),
        ("list", "列出 READY Savepoint"),
    ):
        child = savepoint_subparsers.add_parser(action, help=help_text)
        child.add_argument("name")
    for action, help_text in (
        ("verify", "验证 Savepoint 完整性"),
        ("load", "覆盖当前创作世界并加载 Savepoint"),
    ):
        child = savepoint_subparsers.add_parser(action, help=help_text)
        child.add_argument("name")
        child.add_argument("savepoint_id", metavar="ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if not _require_project_env():
        return

    cmds = {
        "init": cmd_init, "status": cmd_status,
        "plan": cmd_plan, "write": cmd_write,
        "style": cmd_style,
        "new-volume": cmd_new_volume,
        "close-volume": cmd_close_volume,
        "repair-derivation": cmd_repair_derivation,
        "restart": cmd_restart,
        "continue": cmd_continue,
        "run": cmd_run,
        "rag-index": cmd_rag_index,
        "savepoint": cmd_savepoint,
    }
    if args.command in cmds:
        try:
            cmds[args.command](args)
        except ValueError as exc:
            print(f"错误：{exc}")
    else:
        parser.print_help()


PROJECT_ROOT = Path(__file__).resolve().parent


def _require_project_env(project_root: Path | None = None) -> bool:
    root = project_root or PROJECT_ROOT
    if (root / ".env").is_file():
        return True
    print(
        "错误：未找到项目配置文件 .env。\n\n"
        "请在项目根目录创建 .env。\n"
        "可以复制 .env.example 后填写所需配置：\n\n"
        "  copy .env.example .env\n\n"
        "具体配置说明请参阅 README。"
    )
    return False


if __name__ == "__main__":
    main()
