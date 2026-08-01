"""
自动化风格回归检测器
扫描场景/章节正文，标记AI高频句式，辅助人工修改。

用法:
  python -m src.agents.author.style_checker <file_path>            # 终端报告
  python -m src.agents.author.style_checker <file_path> --json     # JSON输出
  python -m src.agents.author.style_checker <file_path> --annotate # 生成标注文件
  python -m src.agents.author.style_checker <file_path> --quiet    # 仅输出违规数量

集成:
  from src.agents.author.style_checker import StyleChecker, StyleReport
  checker = StyleChecker(text)
  report = checker.check_all()
  print(report.summary())
"""

import re
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class StyleViolation:
    """单条违规"""
    line_number: int
    pattern_code: str
    severity: str          # ERROR | WARN | INFO
    description: str
    context: str           # 匹配到的文本片段（截断至60字）

    def format_terminal(self) -> str:
        tag = {"ERROR": "!E", "WARN": "!W", "INFO": "!I"}[self.severity]
        return f"  [{tag} {self.pattern_code}] L{self.line_number}: {self.description}\n      → {self.context}"


@dataclass
class StyleReport:
    """检测报告"""
    file_path: str = ""
    total_lines: int = 0
    total_chars: int = 0
    violations: List[StyleViolation] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for v in self.violations if v.severity == "ERROR")

    @property
    def warnings(self) -> int:
        return sum(1 for v in self.violations if v.severity == "WARN")

    @property
    def infos(self) -> int:
        return sum(1 for v in self.violations if v.severity == "INFO")

    def summary(self) -> str:
        lines = []
        lines.append("═" * 60)
        lines.append(f"  风格回归检测 — {self.file_path}")
        lines.append("═" * 60)
        lines.append(f"")
        total = len(self.violations)
        if total == 0:
            lines.append("  未检测到AI高频句式，通过。")
            return "\n".join(lines)
        lines.append(f"  ERROR: {self.errors}    WARN: {self.warnings}    INFO: {self.infos}    (共 {total} 处)")
        lines.append("")
        lines.append("── 违规明细 ───────────────────────────────────────────")
        lines.append("")
        for v in self.violations:
            lines.append(v.format_terminal())
        lines.append("")
        lines.append("── 分类统计 ───────────────────────────────────────────")
        lines.append("")
        counts: dict = {}
        for v in self.violations:
            key = (v.pattern_code, v.description, v.severity)
            counts[key] = counts.get(key, 0) + 1
        for (code, desc, sev), n in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            tag = {"ERROR": "!E", "WARN": "!W", "INFO": "!I"}[sev]
            bar = "█" * min(n, 20)
            lines.append(f"  [{tag} {code}] {desc:　<20s}  {n:>2d}  {bar}")
        return "\n".join(lines)

    def annotate_text(self, text: str) -> str:
        """生成标注版正文——在违规行尾追加标记"""
        lines = text.split("\n")
        # 按行号分组违规
        by_line: dict = {}
        for v in self.violations:
            by_line.setdefault(v.line_number, []).append(v)

        header = (
            f"# 风格检测标注 — {self.file_path}\n"
            f"# ERROR: {self.errors}  WARN: {self.warnings}  INFO: {self.infos}\n"
            f"# 标记: [!E:CODE] 错误  [!W:CODE] 警告  [!I:CODE] 提示\n"
            f"# ─────────────────────────────────────────────────\n\n"
        )
        result = [header]
        for i, line in enumerate(lines, start=1):
            result.append(line)
            if i in by_line:
                for v in by_line[i]:
                    tag = {"ERROR": "!E", "WARN": "!W", "INFO": "!I"}[v.severity]
                    result.append(f"  ← [{tag}:{v.pattern_code}] {v.description}")
        return "\n".join(result)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "total_lines": self.total_lines,
            "total_chars": self.total_chars,
            "errors": self.errors,
            "warnings": self.warnings,
            "infos": self.infos,
            "violations": [
                {
                    "line": v.line_number,
                    "code": v.pattern_code,
                    "severity": v.severity,
                    "description": v.description,
                    "context": v.context,
                }
                for v in self.violations
            ],
        }


# ── 检测规则 ──────────────────────────────────────────────

# AI高频句式骨架（正则匹配）
AI_SKELETONS: List[Tuple[str, str]] = [
    # 与其说…不如说…
    (r'与其说.{1,40}，?不如说.{1,40}', '与其说…不如说…'),
    # X的意义不在于Y，而在于Z
    (r'的意义不在于.{1,40}，?而在于', 'X的意义不在于Y，而在于Z'),
    # 如果说X，那么Y
    (r'如果说.{1,40}，?那么', '如果说…，那么…'),
    # 诚然X，但是Y
    (r'诚然.{1,40}，?但是', '诚然…，但是…'),
    # 不知(道)过了多久
    (r'不知(?:道)?过了多久', '不知过了多久'),
    # 这一刻/那一刻…仿佛
    (r'[这那]一刻.{1,30}仿佛', '这一刻/那一刻…仿佛'),
    # 在X的映衬/衬托/作用下
    (r'在.{1,40}的(?:映衬|衬托|作用下|驱动下)', '在…的…下'),
    # 这让X感到/想起/觉得/意识到
    (r'这让.{1,20}(?:感到|想起|觉得|意识到|明白|知道)', '这让X感到/想起…'),
    # 似乎X，但又Y
    (r'似乎.{1,30}，但又', '似乎…，但又…'),
    # 伴随着X
    (r'^伴随着.{1,30}[，,]', '伴随着…开头'),
    # 放眼望去
    (r'放眼望去', '放眼望去'),
    # 在X中逐渐
    (r'在.{1,30}中逐渐', '在…中逐渐'),
    # X不由得Y
    (r'.{1,10}不由得.{1,20}', 'X不由得Y'),
    # 曾几何时
    (r'曾几何时', '曾几何时'),
    # 当X的时候
    (r'当.{1,40}的时候', '当…的时候'),
    # 不是X，而是Y，而是Z（嵌套否定衬托）
    (r'不是.{1,30}，而是.{1,30}，而是', '嵌套否定衬托'),
    # 可以说
    (r'可以说.{1,20}', '可以说…'),
    # X并非Y，而是Z
    (r'并非.{1,30}，而是', '并非…，而是…'),
]

# 段尾泄力短语
TAIL_LEAK_WORDS = [
    '无论如何', '至少现在', '也许吧', '谁知道呢', '管他呢',
    '随便吧', '由它去吧', '就这样吧', '谁说得准', '天知道',
    '算了', '无所谓了', '就这样', '走着瞧',
]


# ── 检测器 ────────────────────────────────────────────────

class StyleChecker:
    """AI高频句式检测器"""

    def __init__(self, text: str):
        self.text = text
        self.lines = text.split("\n")
        self.paragraphs = text.split("\n\n")

    # ── 入口 ────────────────────────────────────────────

    def check_all(self, file_path: str = "") -> StyleReport:
        report = StyleReport(
            file_path=file_path,
            total_lines=len(self.lines),
            total_chars=len(self.text),
        )
        self._check_neg_frame(report)
        self._check_num_steps(report)
        self._check_num_seconds(report)
        self._check_tail_leak(report)
        self._check_time_clause(report)
        self._check_meta_words(report)
        self._check_narrator_self(report)
        self._check_ai_openers(report)
        self._check_seem_flood(report)
        self._check_metaphor_flood(report)
        self._check_long_inner(report)
        self._check_long_env(report)
        self._check_ai_skeletons(report)
        return report

    # ── 辅助方法 ──────────────────────────────────────

    def _find_line(self, pos: int) -> int:
        """字符位置 → 行号（1-based）"""
        return self.text[:pos].count("\n") + 1

    def _context(self, match: re.Match, window: int = 60) -> str:
        """提取匹配周围的文本片段"""
        start = max(0, match.start() - 10)
        end = min(len(self.text), match.end() + 10)
        snippet = self.text[start:end].replace("\n", " ").strip()
        if len(snippet) > window:
            snippet = snippet[:window] + "…"
        return snippet

    def _add(self, report: StyleReport, line: int, code: str,
             severity: str, description: str, context: str):
        report.violations.append(StyleViolation(
            line_number=line,
            pattern_code=code,
            severity=severity,
            description=description,
            context=context,
        ))

    # ── Tier 1: 高精度规则 ───────────────────────────

    def _check_neg_frame(self, report: StyleReport):
        """否定衬托：「不是A，是B」/「不是A。是B」/「不是A——是B」/「是B，不是A」"""
        # 紧凑型：不是X，是Y / 并非X，而是Y
        for m in re.finditer(r'(?:不是|并非是|并非)[^。！？\n]{1,50}(?:[，,——]|而是)[^。！？\n]{0,25}(?:是|而是)', self.text):
            line = self._find_line(m.start())
            self._add(report, line, "NEG_FRAME", "WARN",
                      "否定衬托句式（全场景应≤1次）", self._context(m))
        # 分句型：不是X。是Y
        for m in re.finditer(r'不是[^。！？\n]{1,50}[。！？]\s*是', self.text):
            line = self._find_line(m.start())
            self._add(report, line, "NEG_FRAME", "WARN",
                      "否定衬托句式-分句变体（全场景应≤1次）", self._context(m))
        # 反向变体：是B，不是A / 是B。不是A
        for m in re.finditer(r'是[^。！？\n]{1,50}[，,。；;]\s*不是', self.text):
            line = self._find_line(m.start())
            self._add(report, line, "NEG_FRAME", "WARN",
                      "否定衬托句式-反向变体", self._context(m))

    def _check_num_steps(self, report: StyleReport):
        """数步子：第X步"""
        for m in re.finditer(r'第[一二三四五六七八九\d]+[步]', self.text):
            line = self._find_line(m.start())
            self._add(report, line, "NUM_STEPS", "ERROR",
                      "数步子——网文不是操作手册", self._context(m))

    def _check_meta_words(self, report: StyleReport):
        """写作结构词汇"""
        patterns = [
            r'上一章', r'下一章', r'本章', r'这一章',
            r'上一节', r'下一节', r'本节',
            r'前文提到', r'如前所述', r'上文所述', r'后文将会',
            r'在这个场景中?', r'下一个场景',
            r'第[一二三四五六七八九\d]+章',
        ]
        # 获取所有 markdown 标题行号（跳过这些行中的匹配）
        heading_lines = set()
        for m in re.finditer(r'^#{1,6}\s', self.text, re.MULTILINE):
            heading_lines.add(self._find_line(m.start()))

        for pat in patterns:
            for m in re.finditer(pat, self.text):
                line = self._find_line(m.start())
                if line in heading_lines:
                    continue
                self._add(report, line, "META_WORD", "ERROR",
                          f"写作结构词汇「{m.group()}」——正文中不应出现", self._context(m))

    def _check_narrator_self(self, report: StyleReport):
        """叙事者自称"""
        for word in ['笔者', '各位读者', '各位看官', '诸位读者', '诸君', '看官们']:
            for m in re.finditer(re.escape(word), self.text):
                line = self._find_line(m.start())
                self._add(report, line, "NARRATOR", "ERROR",
                          f"叙事者自称「{word}」", self._context(m))

    def _check_ai_openers(self, report: StyleReport):
        """AI万能开头"""
        openers = [
            (r'曾几何时', '曾几何时'),
            (r'放眼望去', '放眼望去'),
            (r'不知(?:道)?过了多久', '不知过了多久'),
            (r'^[^。！？\n]{0,10}伴随着', '伴随着…开头'),
            (r'^[^。！？\n]{0,10}诚然', '诚然…开头'),
        ]
        for pat, desc in openers:
            for m in re.finditer(pat, self.text, re.MULTILINE):
                line = self._find_line(m.start())
                self._add(report, line, "AI_OPENER", "WARN",
                          f"AI万能开头「{desc}」", self._context(m))

    # ── Tier 2: 中等精度规则 ────────────────────────

    def _check_num_seconds(self, report: StyleReport):
        """数秒数：在短文本范围内频繁出现「X秒」"""
        # 按段落检查，一段内出现 ≥3 个 X秒 即标记
        for pi, para in enumerate(self.paragraphs):
            matches = re.findall(r'[一二三四五六七八九十\d]+秒', para)
            if len(matches) >= 3:
                line = self._find_line(self.text.find(para))
                self._add(report, line, "NUM_SEC", "WARN",
                          f"数秒数——本段出现 {len(matches)} 次「X秒」({', '.join(matches[:5])}…)",
                          para[:60].replace("\n", " "))

    def _check_tail_leak(self, report: StyleReport):
        """段尾泄力：段尾出现自我消解短语"""
        for word in TAIL_LEAK_WORDS:
            # 匹配出现在段落末尾（后跟换行或文本结束）的泄力词
            for m in re.finditer(re.escape(word) + r'[。.！!？?\s]*(?:\n\n|\n$|\Z)', self.text):
                line = self._find_line(m.start())
                self._add(report, line, "TAIL_LEAK", "WARN",
                          f"段尾泄力「{word}」——用行动收尾，不要自我消解", self._context(m))

    def _check_time_clause(self, report: StyleReport):
        """时间从句开头：段落以「在…的时候/同时/瞬间」开头"""
        for para in self.paragraphs:
            stripped = para.strip()
            if not stripped:
                continue
            m = re.match(r'在.{1,40}(?:的时候|的同时|的瞬间)', stripped)
            if m:
                line = self._find_line(self.text.find(para))
                self._add(report, line, "TIME_CLAUSE", "INFO",
                          "时间从句开头——直接进入动作", stripped[:60])

    def _check_seem_flood(self, report: StyleReport):
        """模糊词泛滥：「似乎/仿佛/好像」在500字窗口内超过3次"""
        window = 500
        seem_words = ['似乎', '仿佛', '好像']
        pattern = '|'.join(re.escape(w) for w in seem_words)
        # 找到所有出现位置
        positions = [(m.start(), m.group()) for m in re.finditer(pattern, self.text)]
        if len(positions) < 4:
            return
        # 滑动窗口检查
        for i in range(len(positions) - 3):
            if positions[i + 3][0] - positions[i][0] <= window:
                line = self._find_line(positions[i][0])
                words_in_window = [p[1] for p in positions[i:i + 4]]
                self._add(report, line, "SEEM", "INFO",
                          f"模糊词密集——{window}字内出现 {', '.join(words_in_window[:5])}",
                          self.text[positions[i][0]:positions[i][0] + 80].replace("\n", " "))

    def _check_metaphor_flood(self, report: StyleReport):
        """比喻泛滥：相邻三段内出现两个以上「像」字比喻"""
        # 统计每段中「像」的数量（排除「好像」「就像」「像…一样」等口语化用法不太好区分）
        # 实用策略：每段统计「像」的总出现次数，相邻三段合计≥4则标记
        para_like_counts = []
        for para in self.paragraphs:
            cnt = len(re.findall(r'像', para))
            para_like_counts.append(cnt)

        for i in range(len(para_like_counts) - 2):
            window_sum = sum(para_like_counts[i:i + 3])
            if window_sum >= 4:
                para_start = self.text.find(self.paragraphs[i])
                line = self._find_line(max(0, para_start))
                self._add(report, line, "METAPHOR", "INFO",
                          f"比喻密集——相邻3段共 {window_sum} 个「像」(L{i+1}-{i+3}段)",
                          self.paragraphs[i][:60].replace("\n", " "))

    # ── Tier 3: 启发式规则 ──────────────────────────

    def _check_long_inner(self, report: StyleReport):
        """疑似过长内心独白段落：>200字且无对话标记"""
        for pi, para in enumerate(self.paragraphs):
            stripped = para.strip()
            if len(stripped) < 200:
                continue
            # 没有对话引号
            has_quote = bool(re.search(r'[「」""''""]', stripped))
            # 没有明确动作的段落 → 可能是纯内心独白或描写
            has_dialogue_verb = bool(re.search(r'[说问道答叫喊骂吼嚷]', stripped))
            if not has_quote and not has_dialogue_verb:
                para_start = max(0, self.text.find(para))
                line = self._find_line(para_start)
                self._add(report, line, "LONG_INNER", "INFO",
                          f"疑似过长段落（{len(stripped)}字，无对话/动作标记）——检查是否为内心分析",
                          stripped[:60])

    def _check_long_env(self, report: StyleReport):
        """疑似过长环境描写：>150字且含环境关键词"""
        env_keywords = ['墙壁', '天花板', '地板', '灯光', '材质', '纹理',
                        '天花板', '地面', '屋顶', '窗户', '门框', '颜色',
                        '气味', '光芒', '光辉', '光线', '阴影', '倒影']
        kw_pattern = '|'.join(re.escape(w) for w in env_keywords)
        for pi, para in enumerate(self.paragraphs):
            stripped = para.strip()
            if len(stripped) < 150:
                continue
            if re.search(kw_pattern, stripped):
                para_start = max(0, self.text.find(para))
                line = self._find_line(para_start)
                self._add(report, line, "LONG_ENV", "INFO",
                          f"疑似过长环境描写（{len(stripped)}字）——网文环境一句话钩子即可",
                          stripped[:60])

    def _check_ai_skeletons(self, report: StyleReport):
        """AI高频句式骨架匹配"""
        seen_skeletons: dict = {}  # skeleton_name → [(line, context)]
        for pat, name in AI_SKELETONS:
            for m in re.finditer(pat, self.text):
                line = self._find_line(m.start())
                ctx = self._context(m)
                # 一个句式骨架全篇出现≥2次才报告（单次不算问题）
                if name not in seen_skeletons:
                    seen_skeletons[name] = []
                seen_skeletons[name].append((line, ctx))

        for name, occurrences in seen_skeletons.items():
            if len(occurrences) >= 2:
                for line, ctx in occurrences:
                    self._add(report, line, "SKELETON", "WARN",
                              f"AI句式骨架「{name}」（全篇出现{len(occurrences)}次）", ctx)


# ── CLI ────────────────────────────────────────────────────

def _parse_args(args: List[str]):
    """简易参数解析，不依赖 argparse（保持轻量）"""
    opts = {"json": False, "annotate": False, "quiet": False}
    positional = []
    for a in args:
        if a == "--json":
            opts["json"] = True
        elif a == "--annotate":
            opts["annotate"] = True
        elif a == "--quiet":
            opts["quiet"] = True
        elif not a.startswith("-"):
            positional.append(a)
    opts["files"] = positional
    return opts


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["--help"]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    opts = _parse_args(args)
    if not opts["files"]:
        print("错误：请指定文件路径", file=sys.stderr)
        sys.exit(1)

    file_path = opts["files"][0]
    text = Path(file_path).read_text(encoding="utf-8")

    checker = StyleChecker(text)
    report = checker.check_all(file_path=file_path)

    if opts["json"]:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif opts["quiet"]:
        print(len(report.violations))
    else:
        print(report.summary())

    if opts["annotate"]:
        annotated = report.annotate_text(text)
        out_path = Path(file_path).with_suffix(".annotated.md")
        out_path.write_text(annotated, encoding="utf-8")
        print(f"\n  标注文件已保存: {out_path}")

    # 返回码：有 ERROR 则非零
    if report.errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
