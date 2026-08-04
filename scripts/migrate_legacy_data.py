"""
迁移脚本：将旧格式的小说数据转换为新系统的追踪文档格式。

用法:
    python scripts/migrate_legacy_data.py <小说名>           # 迁移指定小说
    python scripts/migrate_legacy_data.py --all              # 迁移所有小说
    python scripts/migrate_legacy_data.py <小说名> --dry-run  # 预览变更不写入
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.document_formats import (
    BookPlan, VolumePlan, ChapterPlan, ContextPackage, SceneSpec,
    CharacterRelationships, RelationshipEntry,
    ItemsEquipment, ItemEntry, ItemLog,
    CultivationSystem, CultivationStage, CharacterCultivation,
    VolumeFramework, VolumeEvent, VolumeCharacter,
)


def migrate_novel(novel_id: str, dry_run: bool = False) -> dict:
    settings = get_settings()
    fs = FileStore(novel_id, settings.data_dir)
    result = {"novel": novel_id, "created": [], "skipped": [], "errors": []}

    # ── 1. book_plan.md ──
    if not fs.has_tracking_doc("book_plan"):
        bp = BookPlan(title=novel_id)
        ps = fs.load_canonical("outlines", "plot_structure") or ""
        if ps:
            # 从 plot_structure 提取卷框架
            import re
            vol_matches = re.findall(
                r'第([一二三四五六七八九\d]+)卷[：:]\s*(.+?)(?=第[一二三四五六七八九\d]+卷|$)',
                ps, re.DOTALL)
            for vn, vcontent in vol_matches[:6]:
                vf = VolumeFramework(title=vcontent.strip().split("\n")[0][:50])
                bp.volumes.append(vf)

            # 提取核心梗概
            premise_m = re.search(r'核心梗概|故事前提|一句话', ps)
            if premise_m:
                bp.premise = ps[premise_m.start():premise_m.start()+200]

        md = bp.to_markdown()
        if not dry_run:
            fs.save_tracking_doc("book_plan", md)
        result["created"].append("tracking/book_plan.md")

    # ── 2. volume_plan.md ──
    if not fs.has_tracking_doc("volume_plan"):
        vp = VolumePlan(volume_number=1, title="第一卷")
        ps = fs.load_canonical("outlines", "plot_structure") or ""
        if ps:
            import re
            # 提取第一卷的事件
            vol1 = re.search(r'第[一1]卷.*?(?=第[二2]卷|$)', ps, re.DOTALL)
            if vol1:
                vol1_text = vol1.group()
                events = re.findall(r'事件\d+[：:]\s*(.+?)(?=事件\d+[：:]|\Z)', vol1_text, re.DOTALL)
                for ev_text in events[:14]:
                    lines = ev_text.strip().split("\n")
                    ev = VolumeEvent(name=lines[0][:40] if lines else "未知事件")
                    for line in lines:
                        line = line.strip()
                        if "触发" in line:
                            ev.trigger = line
                        elif "核心" in line or "内容" in line:
                            ev.content = line
                    vp.events.append(ev)

        md = vp.to_markdown()
        if not dry_run:
            fs.save_tracking_doc("volume_plan", md)
        result["created"].append("tracking/volume_plan.md")

    # ── 3. character_relationships.md ──
    if not fs.has_tracking_doc("character_relationships"):
        cr = CharacterRelationships()
        ws = fs.load_canonical("settings", "world_setting") or ""
        if ws:
            import re
            # 从世界设定提取角色
            char_section = ""
            for hdr in ["角色档案", "主要角色", "角色"]:
                m = re.search(rf'#{1,3}\s*{hdr}\s*\n(.*?)(?=#{{1,3}}\s|\Z)', ws, re.DOTALL)
                if m:
                    char_section = m.group(1)
                    break
            for name_m in re.finditer(r'\*\*([^*]+)\*\*', char_section):
                name = name_m.group(1).strip()
                if 2 <= len(name) <= 8:
                    cr.entries.append(RelationshipEntry(characters=name))

        md = cr.to_markdown()
        if not dry_run:
            fs.save_tracking_doc("character_relationships", md)
        result["created"].append("tracking/character_relationships.md")

    # ── 4. items_equipment.md ──
    if not fs.has_tracking_doc("items_equipment"):
        ie = ItemsEquipment()
        # 从第1章事实摘要提取物品
        fd = fs.load_latest("briefs", "fact_digest_ch0001") or ""
        if fd:
            import re
            items_section = ""
            m = re.search(r'确定的物品\s*\n(.*?)(?=###|\Z)', fd, re.DOTALL)
            if m:
                items_section = m.group(1)
                for item_line in items_section.strip().split("\n"):
                    item_line = item_line.strip("- ").strip()
                    if item_line and len(item_line) > 3:
                        ie.protagonist_items.append(ItemEntry(
                            name=item_line[:40], source="第1章", status="未知"))

        md = ie.to_markdown()
        if not dry_run:
            fs.save_tracking_doc("items_equipment", md)
        result["created"].append("tracking/items_equipment.md")

    # ── 5. cultivation_system.md ──
    if not fs.has_tracking_doc("cultivation_system"):
        cs = CultivationSystem(name="待定义")
        ws = fs.load_canonical("settings", "world_setting") or ""
        if ws:
            import re
            for hdr in ["力量体系", "修炼体系", "能力体系", "力量/修炼体系"]:
                m = re.search(rf'#{1,3}\s*{hdr}\s*\n(.*?)(?=#{{1,3}}\s|\Z)', ws, re.DOTALL)
                if m:
                    cs.overview = m.group(1)[:500]
                    break

        md = cs.to_markdown()
        if not dry_run:
            fs.save_tracking_doc("cultivation_system", md)
        result["created"].append("tracking/cultivation_system.md")

    # ── 6. chapter_plan_ch0001.md (从旧 scene_plan 转换) ──
    sp = fs.load_canonical("outlines", "scene_plan_ch0001")
    if sp and not fs.has_canonical("outlines", "chapter_plan_ch0001"):
        cp = ChapterPlan(chapter_index=1, title="第1章")
        cp.context = ContextPackage(
            character_relations="[待生成 — 运行 review 命令]",
            items_tracking="[待生成 — 运行 review 命令]",
            cultivation_status="[待生成 — 运行 review 命令]",
            foreshadow_nodes="[待生成]",
            emotion_palette="[待生成]",
        )
        # 解析旧场景
        import re
        for sm in re.finditer(r'###\s*场景\s*(\d+)[：:]\s*(.+?)\[状态[：:]\s*(.+?)\].*?\n(.*?)(?=###\s*场景|\Z)',
                               sp, re.DOTALL):
            ss = SceneSpec(scene_number=int(sm.group(1)),
                           name=sm.group(2).strip(), status=sm.group(3).strip())
            body = sm.group(4)
            for key, pat in [("what_happens", "发生什么"), ("dramatic_function", "戏剧功能"),
                             ("dialogue_info_gain", "信息增量"), ("character_micro_moment", "微时刻"),
                             ("characters_involved", "涉及角色"), ("emotion_curve", "情绪曲线"),
                             ("word_estimate", "字数预估"), ("transition", "前后衔接")]:
                m2 = re.search(rf'\*\*{pat}\*\*\s*[:：]\s*(.*?)$', body, re.MULTILINE)
                if m2:
                    setattr(ss, key, m2.group(1).strip())
            cp.scenes.append(ss)

        cp.total_scenes = len(cp.scenes)
        md = cp.to_markdown()
        if not dry_run:
            fs.save_canonical("outlines", "chapter_plan_ch0001", md)
        result["created"].append("outlines/chapter_plan_ch0001.md")

    return result


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        return

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if args[0] == "--all":
        settings = get_settings()
        novels_dir = settings.data_dir / "novels"
        for d in novels_dir.iterdir():
            if d.is_dir():
                result = migrate_novel(d.name, dry_run)
                print(f"\n{d.name}:")
                print(f"  创建: {result['created']}")
                print(f"  跳过: {result['skipped']}")
    else:
        result = migrate_novel(args[0], dry_run)
        mode = "[DRY RUN] " if dry_run else ""
        print(f"\n{mode}{result['novel']}:")
        for f in result["created"]:
            print(f"  + {f}")
        for f in result["skipped"]:
            print(f"  = {f} (已存在)")
        for e in result["errors"]:
            print(f"  ! {e}")

    if dry_run:
        print("\n添加 --confirm 参数以确认写入。")


if __name__ == "__main__":
    main()
