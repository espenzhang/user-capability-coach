"""Rule-based signal detectors for prompt issue types.

These are heuristic aids for the agent's semantic judgment — not final verdicts.
Confidence values are preliminary; the policy layer makes the final call.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from .taxonomy import IssueType, Domain, CostSignal, SENSITIVE_KEYWORDS


@dataclass
class DetectorResult:
    issue_type: IssueType
    confidence: float  # 0.0–1.0
    severity: float    # 0.0–1.0
    fixability: float  # 0.0–1.0; how easy for user to fix
    cost_signal: CostSignal
    evidence: str      # short non-verbatim description


@dataclass
class DetectionOutput:
    domain: Domain
    is_sensitive: bool
    task_complexity: float   # 0.0–1.0
    is_urgent: bool
    candidates: list[DetectorResult] = field(default_factory=list)


# ── Output contract signals ─────────────────────────────────────────────────

# These must be SPECIFIC format words — generic nouns like "文档/document/报告/report"
# do NOT count because they don't specify structure (markdown? table? OpenAPI?).
OUTPUT_FORMAT_WORDS_ZH = frozenset({
    "json", "表格", "列表", "清单", "步骤", "markdown", "邮件模板",
    "提纲", "outline", "diff", "csv", "yaml", "xml", "html",
    "类型注释", "注释", "编号列表", "bullet", "总结摘要",
    "测试用例", "测试报告", "changelog", "swagger", "openapi",
    "代码块", "伪代码", "流程图", "erd", "序列图",
    # Specific output types (not generic "文档")
    "查询", "sql查询", "正则表达式", "函数", "脚本", "类", "接口定义",
    "dockerfile", "配置文件", "shell脚本",
})

OUTPUT_FORMAT_WORDS_EN = frozenset({
    "json", "table", "bullet", "markdown", "template",
    "outline", "diff", "csv", "yaml", "xml", "html",
    "numbered list", "checklist", "flowchart", "diagram",
    "test case", "pr description", "changelog", "swagger", "openapi",
    "pseudocode", "erd", "sequence diagram", "gantt",
    # Specific output types
    "query", "sql query", "regex", "function", "script", "class",
    "dockerfile", "config file", "shell script", "bash script",
})

# Translation tasks: presence of a target language is implicit output contract.
# Chinese language names don't play well with \b (word boundary only works on
# ASCII word chars), so the Chinese alternatives match without \b; English
# keeps \b to avoid accidental matches inside longer words.
TRANSLATION_LANG_RE = re.compile(
    r"(英文|中文|日文|法文|德文|西班牙文|韩文|俄文|葡萄牙文|意大利文|"
    r"\b(?:english|chinese|japanese|french|german|spanish|korean|russian|"
    r"portuguese|italian|arabic|dutch|swedish|turkish|hindi)\b)",
    re.IGNORECASE,
)

LENGTH_CONSTRAINT_RE = re.compile(
    r"(\d+\s*(字|words?|lines?|行|sentences?|sentences|句|条|items?|points?))|"
    r"(no more than|不超过|最多|at most|within\s+\d+)",
    re.IGNORECASE,
)

EXPLICIT_OUTPUT_VERB_RE = re.compile(
    r"(输出|生成|给我|返回|格式化|"
    r"\b(?:format|output|return|generate|produce|render|"
    r"provide|write|create|make|build|deliver)\b)",
    re.IGNORECASE,
)


LENGTH_PHRASE_RE = re.compile(
    r"\b(one.line|one.sentence|one.paragraph|one.word|in one|"
    r"in a (sentence|line|word|paragraph)|single (line|sentence|word)|"
    r"briefly|concisely|in brief|tldr)\b",
    re.IGNORECASE,
)

# Simple factual/arithmetic questions where the output format is self-evident
FACTUAL_QUESTION_RE = re.compile(
    r"^(what (is|are|does|was|were|will)|how (many|much|does|do|is)|"
    r"who (is|was|are)|where is|when (is|was|did)|why (is|does)|"
    r"which |是什么|有多少|多少|几个|怎么)\b",
    re.IGNORECASE,
)

# "Rewrite X to be Y" — output IS the rewritten version, format implicit.
# Note: Python's `\b` anchors don't match Chinese characters (word-boundary
# is defined on ASCII word chars), so we keep \b only for English terms
# and match Chinese terms without word boundaries.
TRANSFORMATION_RE = re.compile(
    r"(\b(?:rewrite|rephrase|paraphrase|reword|simplify|clarify)\b|"
    r"改写|改成|改为|重写|简化|润色)",
    re.IGNORECASE,
)


def _has_output_contract(text: str) -> bool:
    lower = text.lower()
    if any(w in lower for w in OUTPUT_FORMAT_WORDS_EN | OUTPUT_FORMAT_WORDS_ZH):
        return True
    if LENGTH_CONSTRAINT_RE.search(text):
        return True
    if LENGTH_PHRASE_RE.search(text):
        return True
    # Translation tasks with target language are self-specifying
    if TRANSLATION_LANG_RE.search(text):
        return True
    # Transformation tasks: "rewrite X to be Y" — output format is implicit
    if TRANSFORMATION_RE.search(text):
        return True
    # Simple factual questions: "What is X?" "How many X?" — answer format is obvious
    if FACTUAL_QUESTION_RE.match(text.strip()) and "?" in text:
        return True
    # Question mark alone (short question) implies self-evident format
    if text.strip().endswith("?") and len(text.split()) <= 8:
        return True
    if EXPLICIT_OUTPUT_VERB_RE.search(text) and len(text) > 30:
        return True
    return False


def _effective_length(text: str) -> int:
    """Returns a length estimate that works for both Chinese and English."""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if chinese_chars > len(text) * 0.3:
        return len(text.strip())  # character count for Chinese
    return len(text.split())  # word count for English


# ── Overloaded request signals ───────────────────────────────────────────────

ACTION_VERBS_ZH = [
    "分析", "设计", "开发", "编写", "测试", "部署", "发布", "重构",
    "实现", "优化", "审查", "评审", "调试", "修复", "规划", "策划",
    "研究", "调研", "文档", "记录", "培训", "汇报",
]

ACTION_VERBS_EN = [
    "analyze", "analyse", "design", "develop", "build", "code", "write",
    "test", "deploy", "release", "refactor", "implement", "optimize",
    "review", "audit", "debug", "fix", "plan", "research", "document",
    "create", "make", "generate", "prepare", "present", "pitch",
    "recruit", "hire", "launch", "add", "update", "remove", "delete",
    "migrate", "bump", "tag", "merge", "rebase", "push", "pull",
    "configure", "setup", "integrate", "monitor", "automate",
]

# Task nouns in enumerated lists also signal overloaded requests
# "Write the spec, code, tests, and deployment guide" → 4 task nouns = overloaded
TASK_NOUNS_EN = frozenset({
    "spec", "specs", "specifications", "requirements",
    "code", "implementation",
    "tests", "test cases", "unit tests", "test suite",
    "documentation", "docs", "readme",
    "deployment", "deploy plan", "runbook",
    "architecture", "design",
    "plan", "roadmap", "timeline",
    "presentation", "slides", "deck",
    "report", "summary", "writeup", "executive summary",
    "migration", "changelog",
    "prd", "brief", "proposal",
})

TASK_NOUNS_ZH = frozenset({
    "方案", "计划", "报告", "文档", "代码", "测试", "设计",
    "架构", "规划", "策略", "战略", "产品", "需求", "prd",
    "重构", "迁移", "部署", "上线", "发布",
})

CONJUNCTION_RE = re.compile(
    r"(\bthen\b|\band then\b|然后|，再|、再|;|；|\bthen\s+\w|\bafter that\b|之后再|接着)",
    re.IGNORECASE,
)

COMMA_VERB_RE = re.compile(
    r"(?:^|,|，|、|；|;)\s*(?:" + "|".join(ACTION_VERBS_EN + ACTION_VERBS_ZH) + r")\b",
    re.IGNORECASE,
)


def _count_action_verbs(text: str) -> int:
    lower = text.lower()
    count = 0
    for v in ACTION_VERBS_EN:
        if re.search(r"\b" + v + r"\b", lower):
            count += 1
    for v in ACTION_VERBS_ZH:
        if v in lower:
            count += 1
    return count


def _count_task_nouns(text: str) -> int:
    """Count distinct task nouns. Longer matches mask shorter ones to avoid
    double-counting (e.g., 'executive summary' should count as 1, not match
    both 'summary' and 'executive summary')."""
    lower = text.lower()
    # Sort by length descending so multi-word matches are consumed first
    sorted_en = sorted(TASK_NOUNS_EN, key=len, reverse=True)
    consumed = lower
    count = 0
    for n in sorted_en:
        # Count each occurrence then remove it from the consumed string
        # so shorter substrings don't match again
        pattern = r"\b" + re.escape(n) + r"\b"
        matches = list(re.finditer(pattern, consumed))
        if matches:
            count += len(matches)
            consumed = re.sub(pattern, " " * max(len(n), 1), consumed)
    count += sum(1 for n in TASK_NOUNS_ZH if n in text)
    return count


def _is_comma_enumeration(text: str) -> bool:
    """Detect X, Y, Z, ..., and W pattern — strong overloaded signal regardless
    of whether items are in TASK_NOUNS. E.g., 'Create a logo, website,
    brand guide, ad copy, and launch campaign.'"""
    # At least 4 comma-separated items ending with 'and' or '，并'
    # Heuristic: 4+ commas + 'and' before the last item (English),
    # or 4+ commas + '和|以及' before the last item (Chinese)
    comma_count = text.count(",") + text.count("，") + text.count("、")
    if comma_count < 3:
        return False
    if re.search(r",\s*and\s+\w", text, re.IGNORECASE):
        return True
    if re.search(r"[，、]\s*(?:和|以及|还有)", text):
        return True
    return False


def _is_overloaded(text: str) -> tuple[bool, float]:
    verb_count = _count_action_verbs(text)
    conjunction_count = len(CONJUNCTION_RE.findall(text))
    comma_count = text.count(",") + text.count("，") + text.count("、")
    task_noun_count = _count_task_nouns(text)

    score = 0.0
    if verb_count >= 4:
        score += 0.5
    elif verb_count == 3:
        score += 0.3
    elif verb_count == 2 and task_noun_count >= 3:
        score += 0.25
    if conjunction_count >= 2:
        score += 0.3
    elif conjunction_count == 1 and (verb_count >= 3 or task_noun_count >= 3):
        score += 0.15
    if comma_count >= 3 and (verb_count >= 2 or task_noun_count >= 3):
        score += 0.2
    # Enumerated task nouns (even with single verb like "write X, Y, Z, and W")
    if task_noun_count >= 4:
        score += 0.4
    elif task_noun_count == 3:
        score += 0.35  # 3 distinct deliverables = definitely overloaded
    elif task_noun_count == 2 and comma_count >= 1:
        score += 0.1

    # Multi-item comma enumeration: "Create X, Y, Z, W, V, and Q" — strong
    # overloaded signal even when items are domain-specific (logo, website, etc.)
    # that aren't in TASK_NOUNS.
    if _is_comma_enumeration(text) and verb_count >= 1:
        # Enumeration signal: at least 4 commas + conjunction = 5+ items
        if comma_count >= 4:
            score += 0.35
        elif comma_count >= 3:
            score += 0.2

    # "X, Y, and Z" with 3 task nouns — short enumeration of deliverables
    # e.g., "Prepare a report, presentation, and executive summary." (comma=2)
    if task_noun_count >= 3 and re.search(r",\s*and\s+\w", text, re.IGNORECASE):
        score += 0.2

    return score >= 0.5, min(score, 1.0)


# ── Missing goal signals ─────────────────────────────────────────────────────

GOAL_VERBS_ZH = frozenset({
    "翻译", "总结", "分析", "改写", "批改", "评审", "生成", "写", "创建",
    "解释", "比较", "对比", "列出", "查找", "转换", "优化", "修复", "检查",
    "设计", "推荐", "计算", "预测", "提取", "摘要", "查", "转", "给我",
    "告诉我", "帮我写", "帮我找", "帮我改", "帮我翻", "帮我分析", "帮我生成",
    "调试", "测试", "评估", "转成", "改成", "变成",
})

GOAL_VERBS_EN = frozenset({
    "translate", "summarize", "analyze", "rewrite", "critique", "review",
    "generate", "write", "create", "explain", "compare", "list", "find",
    "convert", "optimize", "fix", "check", "design", "recommend",
    "calculate", "predict", "extract", "debug", "test", "evaluate",
    "provide", "deliver",
    "give me", "show me", "tell me",
})

# Unbound references: user says "this/that/它/这个" without providing content
UNBOUND_REF_RE = re.compile(
    r"^[^:\n\r]{0,60}(这个|这份|这段|这些|那个|它|此|the following|this one|that one|it)\b",
    re.IGNORECASE,
)

VAGUE_ONLY_RE = re.compile(
    r"^(help|帮|帮我|看看|看一下|处理|处理一下|看|do something|check this|"
    r"have a look|take a look|看这个|帮忙|assist me|help me|please help"
    r"|弄一下|弄弄|改一下|修一下|做一下|弄弄|搞一下)\s*[\.\!\?。！？]?$",
    re.IGNORECASE,
)

# Social/greeting phrases that are not task requests — skip detection entirely
SOCIAL_PHRASE_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|got it|sounds good|"
    r"great|perfect|nice|cool|alright|nevermind|never mind|"
    r"你好|谢谢|好的|行|没问题|明白|收到|好|嗯|哦|哈|嗯嗯|哦哦"
    r")\s*[\.\!\?。！？,，]?$",
    re.IGNORECASE,
)

# Prompts where the "goal verb" is present but object/content is missing or fully unresolved
UNRESOLVED_OBJECT_RE = re.compile(
    r"^(帮我|请帮我|请|help me|can you|could you|please)?\s*"
    r"(优化|改进|改善|提升|完善|refine|improve|enhance|upgrade|"
    r"fix|check|review|look at|tell me about|help with)\s*"
    r"(这个|这份|这段|那个|它|this|that|this one|my project|it|this code)?\s*"
    r"(系统|代码|项目|方案|文章|这个|这份)?\s*[\.\!\?。！？]?$",
    re.IGNORECASE,
)

# Generic "tell me / show me / help me" with an unbound "this/that" but no content
GENERIC_VERB_UNBOUND_RE = re.compile(
    r"^(tell me about|check out|check\s+\w+\s+out|look at|do something with|"
    r"what should I do|can you help me with|please assist|"
    r"help me with my|can you help)\s*(this|that|these|those|it|my \w+)?[\.\!\?]?$",
    re.IGNORECASE,
)

# Chinese bare-verb without any object or content ("请分析", "请帮我写")
ZH_BARE_VERB_RE = re.compile(
    r"^(请|帮我|请帮我)?\s*"
    r"(分析|研究|调研|优化|改进|改善|检查|评审|审查|总结|汇报|梳理|整理)\s*[\.\!\?。！？]?$",
    re.IGNORECASE,
)

# Generic intent at the start of a multi-word prompt, even when content
# follows — e.g. "Do something with this data: 1,2,3". The "do something"
# remains vague about the goal even though data is provided.
VAGUE_START_RE = re.compile(
    r"^(do something|figure out|handle|deal with|work on|look into|"
    r"处理一下|帮忙处理|看一下)\b",
    re.IGNORECASE,
)

# "Give/Show/Tell me the {generic noun}" — the user asks for "the X"
# without specifying which X. E.g. "Give me the answer." — answer to what?
# Treated as missing_goal (unbound reference) even though an intent verb
# is present.
GIVE_ME_THE_X_RE = re.compile(
    r"^(give me|show me|tell me)\s+(the|an|a)\s+"
    r"(answer|result|response|solution|output|value|thing|stuff|info|information|data)\s*[\.\!\?]?$",
    re.IGNORECASE,
)


def _text_length(text: str) -> int:
    """Length heuristic that works for both English (word count) and Chinese (char count)."""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if chinese_chars > len(text) * 0.3:
        # Chinese-dominant: use character count / 2 as approximate word count
        return len(text.strip()) // 2
    return len(text.split())


def _has_clear_goal(text: str) -> bool:
    lower = text.lower()
    for v in GOAL_VERBS_EN:
        if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", lower):
            return True
    for v in GOAL_VERBS_ZH:
        if v in lower:
            return True
    # Long enough text with a question mark likely has intent
    if "?" in text or "？" in text:
        return True
    return False


WRITE_NO_SPEC_RE = re.compile(
    r"^(请帮我|帮我|请)?\s*"
    r"(写)\s*(一个|一份)?\s*"
    r"(代码|功能|东西)\s*[\.\!\?。！？]?$",
    re.IGNORECASE,
)


def _is_missing_goal(text: str) -> tuple[bool, float]:
    stripped = text.strip()
    # Trivially vague single-expression
    if VAGUE_ONLY_RE.match(stripped):
        return True, 0.95

    # Vague start even with content — "Do something with this data: ..."
    if VAGUE_START_RE.match(stripped):
        return True, 0.85

    # "Give me the answer." / "Show me the result." — intent verb present
    # but referent ("the answer") is unbound.
    if GIVE_ME_THE_X_RE.match(stripped):
        return True, 0.80

    # "Optimize this system" type — verb present but object is vague/unresolved and no content follows
    if UNRESOLVED_OBJECT_RE.match(stripped):
        return True, 0.80

    # "Tell me about this." / "Check this out." — generic verb + unbound pronoun
    if GENERIC_VERB_UNBOUND_RE.match(stripped):
        return True, 0.85

    # "请分析" / "请总结" — bare Chinese verb with no object
    if ZH_BARE_VERB_RE.match(stripped):
        return True, 0.80

    # Narrow Chinese match: "请写代码"/"请帮我写一个功能" — the object
    # ("代码"/"功能"/"东西") is generic enough that WHAT to build is unknown,
    # so labelers treat this as missing_goal. (We intentionally exclude
    # "生成内容" and the English "write something" — those are handled as
    # missing_output_contract by the other detector.)
    if WRITE_NO_SPEC_RE.match(stripped):
        return True, 0.80

    if _has_clear_goal(text):
        return False, 0.0

    # Short text without any recognizable intent verb
    approx_len = _text_length(stripped)
    if approx_len <= 6:
        return True, 0.75

    return False, 0.0


# ── Domain classifier ─────────────────────────────────────────────────────────

DOMAIN_SIGNALS: dict[Domain, list[str]] = {
    Domain.CODING: [
        "code", "function", "class", "bug", "error", "api", "sql", "python",
        "javascript", "typescript", "test", "deploy", "database", "schema",
        "代码", "函数", "接口", "数据库", "测试", "部署", "脚本", "调试",
        "编程", "算法", "git", "docker", "kubernetes",
    ],
    Domain.WRITING: [
        "write", "email", "report", "article", "essay", "blog", "document",
        "translate", "summary", "邮件", "文章", "报告", "翻译", "文档",
        "写作", "总结", "文案", "提纲",
    ],
    Domain.RESEARCH: [
        "research", "compare", "explain", "what is", "how does", "why",
        "history", "overview", "研究", "比较", "解释", "什么是", "为什么",
        "调研", "分析市场",
    ],
    Domain.PLANNING: [
        "plan", "strategy", "roadmap", "schedule", "timeline", "prd",
        "requirement", "计划", "策略", "路线图", "排期", "需求", "方案",
        "规划", "产品", "project",
    ],
    Domain.OPS: [
        "deploy", "monitor", "ci", "cd", "pipeline", "backup", "security",
        "infrastructure", "部署", "监控", "运维", "服务器", "配置", "环境",
        "devops", "bash", "shell", "dockerfile", "nginx",
    ],
}


SENSITIVE_PATTERN_RE = re.compile(
    r"(不好的念头|活不下去|不想活|了结生命|轻生|自杀|自残|跳楼|割腕|"
    r"情绪很差|情绪崩溃|心情很差|崩溃了|吵架|家庭矛盾|"
    r"孩子失踪|孩子走失|学习障碍|发育迟缓|特殊需要|"
    r"被裁|裁了|失业了|裁员|工作没了|"
    r"suicidal|self.harm|overdose|domestic.violen|mental breakdown|"
    r"child.*(?:missing|home|reach)|can.t reach.*child|"
    r"(?:my child|my kid).*(?:hasn.t|hasn't|didn.t|didn't))",
    re.IGNORECASE | re.DOTALL,
)


def _classify_domain(text: str) -> Domain:
    lower = text.lower()
    # Sensitive check first — keywords and patterns
    if SENSITIVE_PATTERN_RE.search(text):
        return Domain.SENSITIVE
    for kw in SENSITIVE_KEYWORDS:
        if kw.lower() in lower:
            return Domain.SENSITIVE

    scores: dict[Domain, int] = {d: 0 for d in DOMAIN_SIGNALS}
    for domain, signals in DOMAIN_SIGNALS.items():
        for sig in signals:
            if sig in lower:
                scores[domain] += 1

    best = max(scores, key=lambda d: scores[d])
    if scores[best] == 0:
        return Domain.OTHER
    return best


# ── Urgency heuristic ─────────────────────────────────────────────────────────

URGENCY_RE = re.compile(
    r"(紧急|马上|立刻|现在就|救命|快|"
    r"\b(?:urgent|asap|emergency|right now|immediately)\b)",
    re.IGNORECASE,
)


def _is_urgent(text: str) -> bool:
    return bool(URGENCY_RE.search(text))


# ── Rule fallbacks for formerly-shadow types ─────────────────────────────────
#
# These produce candidates for two of the five newly-promoted types when
# high-precision signals exist. The other three types (missing_constraints,
# conflicting_instructions, missing_success_criteria) are left to
# agent_classification — they're too subjective for reliable regex.

# Two-step unbound-reference detection: prompt must START with a bare
# pronoun AND its trailing text must contain a problem-stating word. The
# earlier single-regex version fired on "This is great!" because
# pronoun+copula alone was too broad — any positive-valence statement
# would match. Splitting into pronoun-start + problem-word gives the
# semantic precision we need without an unmanageable regex.

_PRONOUN_START_RE = re.compile(
    # English pronouns keep \b boundary; Chinese pronouns must match
    # without \b since Python regex \b only considers ASCII word chars.
    r"^\s*(\b(?:it|this|that|these|those)\b|这个|那个|它|此)",
    re.IGNORECASE,
)
_PROBLEM_WORDS_RE = re.compile(
    r"(\b(broken|wrong|failing|failed|crashed|crash|bug|buggy|"
    r"not\s+(working|right|good|correct)|doesn't\s+work|don't\s+work|"
    r"isn't\s+working|incorrect|messed\s+up|fix\s+it)\b"
    r"|不对|有问题|坏了|崩了|报错|没用|不行|需要(修|改|调))",
    re.IGNORECASE,
)

# "This code/function/bug/file ..." with no code block / substantial content
# in the same prompt → the referent was probably expected to be attached but
# isn't.
REFERENCES_CONTENT_RE = re.compile(
    r"\b(this|these|the following|the above|the attached|下面(的|这)?|以上的?|"
    r"上面(的|这)?|附上?的?|这段|这份|这里的?)\s*"
    r"(code|function|file|bug|error|snippet|script|program|config|data|"
    r"代码|函数|文件|错误|问题|脚本|程序|配置|数据|段落|内容)",
    re.IGNORECASE,
)


_CODE_TOKEN_RE = re.compile(
    r"\b(def |function |class |import |return |async |await |let |const |var |"
    r"=>|::|->|{\s|\[\s|\breturn\b)",
    re.IGNORECASE,
)
_ERROR_MESSAGE_RE = re.compile(
    r"\b(error|exception|traceback|stack|TypeError|ValueError|NameError|"
    r"AttributeError|IndexError|KeyError|RuntimeError|SyntaxError)\b"
    r"[:.\s]",
    re.IGNORECASE,
)
_LINE_REF_RE = re.compile(
    # Accept various forms of location references:
    #   - "at line 42" / "line 42" / "line:42"
    #   - "in app.py" / "in src/utils.py"
    #   - bare file path "src/foo.py:15" or "utils.py:42"
    #   - @path mentions ("@src/api.py")
    r"(\bat line|\bline \d+|\bline:\d|\bin [\w./]+\.\w+"
    r"|@[\w./]+"
    r"|\b[\w./]+\.(py|js|ts|jsx|tsx|go|rs|rb|java|c|cpp|h|css|html|json|yml|yaml|md|sh)"
    r"(:\d+)?)",
    re.IGNORECASE,
)


def _has_code_block(text: str) -> bool:
    """Does the prompt contain enough structural content for the reference
    ('this code', 'the following') to be satisfied?

    Returns True when any of the following is true:
      - fenced code block (```)
      - ≥ 1 indented line (code-ish content pasted inline)
      - any code syntax token (def, function, class, =>, etc.)
      - an error/traceback message inline
      - a line/file reference ('at line 42', 'in app.py', 'src/foo.py:15')
        — tells the agent where to look, so the referent is resolvable
        via its file-reading tools even if no code is inline
    """
    if "```" in text:
        return True
    lines = text.splitlines()
    if any(line.startswith(("    ", "\t")) for line in lines):
        return True
    if _CODE_TOKEN_RE.search(text):
        return True
    if _ERROR_MESSAGE_RE.search(text):
        return True
    if _LINE_REF_RE.search(text):
        return True
    return False


def _is_unbound_reference(text: str) -> tuple[bool, float]:
    """Detect bare-pronoun-with-no-referent problem statements.

    Three conditions must hold:
      1. Prompt starts with a bare pronoun (it / this / that / 这个 / ...)
      2. Prompt does NOT contain a question mark (asking something specific
         means the user has a concrete intent, not just an unbound ref)
      3. Prompt contains a problem-stating word (broken / 不对 / doesn't
         work / ...) — distinguishes "It's broken" from "This is great"
    """
    stripped = text.strip()
    if not _PRONOUN_START_RE.match(stripped):
        return False, 0.0
    if "?" in stripped or "？" in stripped:
        return False, 0.0
    if not _PROBLEM_WORDS_RE.search(stripped):
        return False, 0.0
    if len(stripped) < 40:
        return True, 0.85
    return True, 0.75


def _is_missing_context(text: str) -> tuple[bool, float]:
    """Detect references to specific content that isn't provided. E.g.
    'fix this bug' with no code block. Returns (is_missing, conf)."""
    if REFERENCES_CONTENT_RE.search(text) and not _has_code_block(text):
        # Length-gated: long prompts might describe the code verbally
        if len(text) < 200:
            return True, 0.80
        return True, 0.65
    return False, 0.0


# ── missing_success_criteria (weak rule) ─────────────────────────────────────
#
# "Optimize / improve / make it better" requests with no verifiable metric.
# The rule is intentionally conservative — it fires only when an improvement
# verb is present AND no metric-like phrase is anywhere in the prompt. Precision
# takes priority over recall because false positives are user-visible.

IMPROVE_VERB_RE = re.compile(
    r"(\b(optimi[sz]e|improve|enhance|speed up|make\s+\w+\s+better|"
    r"refactor for (performance|readability|speed)|"
    r"make it (faster|better|cleaner|more \w+))\b"
    r"|优化|提升|改善|让.{1,15}更(快|好|清晰|高效)|加速|提速)",
    re.IGNORECASE,
)
SUCCESS_METRIC_RE = re.compile(
    # Numbers w/ units, comparatives, targets, tests, thresholds
    r"(\b\d+\s*(%|ms|s|sec|seconds?|minutes?|x|times|fold|倍|秒|毫秒|分钟)\b"
    r"|\b(faster|slower|bigger|smaller|cheaper|more efficient)\s+than\b"
    r"|\b(under|below|above|within|at most|at least)\s+\d"
    r"|\b(pass(es)?|passing)\s+(tests?|ci|lint|benchmark)\b"
    r"|\b(tests?|ci|lint|benchmark|build)\s+(pass(es)?|passing|succeed|green)\b"
    r"|\buntil\s+(all\s+)?(tests?|ci|lint)\s+pass"
    r"|\b(target|goal|threshold|benchmark|metric|kpi|sla)\b"
    r"|\b(p50|p90|p95|p99|qps|rps|rtt|throughput)\b"
    r"|\d+\s*(倍|秒|毫秒|分钟|次|条)|目标|基准|指标|标准|通过(所有)?测试|达到)",
    re.IGNORECASE,
)


def _is_missing_success_criteria(text: str) -> tuple[bool, float]:
    """Detect 'improve / optimize' requests with no verifiable success metric.

    Gated by prompt length (< 150 chars) so long prompts that describe
    success verbally without hitting our metric regex don't get flagged."""
    if not IMPROVE_VERB_RE.search(text):
        return False, 0.0
    if SUCCESS_METRIC_RE.search(text):
        return False, 0.0
    # Short improve-verb prompts with no metric → conservative flag.
    # Confidence kept below policy.min_severity's 0.50 * 0.70 product
    # for the low-signal long case so it won't surface visible coaching
    # without agent confirmation.
    if len(text) < 80:
        return True, 0.72
    if len(text) < 150:
        return True, 0.60
    return False, 0.0


# ── conflicting_instructions (weak rule) ─────────────────────────────────────
#
# Only fires on explicit conflict markers + known antagonist pairs. Generic
# "but also" is too noisy (most uses aren't contradictions), so we require
# both a contrast conjunction and a second demand that contradicts the first.
#
# Sub-patterns are named for readability and independent testability.

# "but/however/yet ... also/without/no ..." — general contrast + negation
_CONFLICT_CONTRAST_CONJ = (
    r"\b(but|however|yet|at the same time|while also|同时|而且|但要)\b.{0,60}"
    r"\b(also|all|every|without|no|don't|不能|不要|同时要|仍然|还要|都要)\b"
)
# "shorter/concise ... detailed/comprehensive" — length vs depth
_CONFLICT_LENGTH_VS_DETAIL = (
    r"\b(shorter|shortest|concise|brief)\b.{0,40}"
    r"\b(detailed|comprehensive|thorough|all\s+\w+)\b"
)
# "no dependencies ... use/import" — zero-deps vs library use
_CONFLICT_NO_DEPS_VS_USE = (
    r"\b(no dependenc|without .*library|no external)\b.{0,60}"
    r"\b(use|import|with)\s+\w+"
)
# "faster/cheaper/simpler ... and/but/while ... more/still" — perf vs quality
_CONFLICT_PERF_VS_QUALITY = (
    r"\b(faster|cheaper|simpler)\b.{0,40}"
    r"\b(and|but|while)\b.{0,20}"
    r"\b(more|still|still\s+keep)\b"
)
# Chinese: "要快…又要" / "既…还/又" — classic 既…又 contradiction pattern
_CONFLICT_ZH = r"要快.{0,15}又要|既.{0,10}(还|又).{0,10}"

CONFLICT_MARKER_RE = re.compile(
    r"(" + "|".join([
        _CONFLICT_CONTRAST_CONJ,
        _CONFLICT_LENGTH_VS_DETAIL,
        _CONFLICT_NO_DEPS_VS_USE,
        _CONFLICT_PERF_VS_QUALITY,
        _CONFLICT_ZH,
    ]) + r")",
    re.IGNORECASE,
)


def _is_conflicting_instructions(text: str) -> tuple[bool, float]:
    """Detect explicit conflicting requirements. Very conservative —
    this is the hardest class to regex-detect, and agents with context
    should do most of the work here via agent_classification."""
    if CONFLICT_MARKER_RE.search(text):
        if len(text) < 200:
            return True, 0.70
        return True, 0.60
    return False, 0.0


# ── Complexity heuristic ───────────────────────────────────────────────────────

def _estimate_complexity(text: str) -> float:
    """0.0 = trivially simple, 1.0 = very complex."""
    word_count = len(text.split())
    verb_count = _count_action_verbs(text)
    conjunction_count = len(CONJUNCTION_RE.findall(text))

    score = 0.0
    if word_count > 50:
        score += 0.3
    elif word_count > 20:
        score += 0.15
    score += min(verb_count * 0.1, 0.4)
    score += min(conjunction_count * 0.1, 0.2)
    return min(score, 1.0)


# ── Public API ────────────────────────────────────────────────────────────────

# Common domain synonyms agents reach for → canonical enum value. Keeping
# the Domain enum fixed for pattern-aggregation stability; aliases here
# let agents use natural vocabulary without triggering a rejection.
_DOMAIN_ALIASES: dict[str, Domain] = {
    "docs": Domain.WRITING,
    "documentation": Domain.WRITING,
    "doc": Domain.WRITING,
    "email": Domain.WRITING,
    "article": Domain.WRITING,
    "api": Domain.CODING,
    "programming": Domain.CODING,
    "debug": Domain.CODING,
    "debugging": Domain.CODING,
    "test": Domain.CODING,
    "testing": Domain.CODING,
    "infra": Domain.OPS,
    "infrastructure": Domain.OPS,
    "devops": Domain.OPS,
    "deploy": Domain.OPS,
    "deployment": Domain.OPS,
    "security": Domain.OPS,
    "design": Domain.PLANNING,
    "product": Domain.PLANNING,
    "strategy": Domain.PLANNING,
    "analysis": Domain.RESEARCH,
    "compare": Domain.RESEARCH,
}


def _resolve_domain(raw: str | None, text: str) -> Domain:
    """Lenient domain parse. Tries: exact enum → lower-cased alias → rule
    classifier on text → OTHER. Always returns a Domain, never raises."""
    if not raw:
        return _classify_domain(text)
    try:
        return Domain(raw)
    except ValueError:
        pass
    alias = _DOMAIN_ALIASES.get(raw.lower())
    if alias is not None:
        return alias
    # Unknown value → don't reject the whole classification; map to OTHER.
    return Domain.OTHER


def _resolve_cost_signal(raw: str | None) -> CostSignal:
    """Lenient cost_signal parse. Unknown → NONE rather than rejecting."""
    if not raw:
        return CostSignal.NONE
    try:
        return CostSignal(raw)
    except ValueError:
        return CostSignal.NONE


def build_detection_from_agent(
    agent_cls: dict[str, Any],
    text: str,
) -> "DetectionOutput | None":
    """Construct a DetectionOutput from an agent-supplied classification.

    Strict fields (reject-on-invalid):
      - issue_type: must be a valid IssueType enum or null

    Lenient fields (soft-map on invalid):
      - domain: unknown values try aliases (docs → writing etc.) then
        fall back to _classify_domain(text), finally OTHER. Never rejects.
      - cost_signal: unknown → NONE, never rejects.

    Only issue_type strictness is preserved because it drives pattern
    aggregation and visible coaching — getting it wrong is a real policy
    bug. Domain and cost_signal are classification metadata where a soft
    fallback preserves the rest of the agent's judgment.
    """
    if not isinstance(agent_cls, dict):
        return None
    try:
        raw_issue = agent_cls.get("issue_type")
        issue_type: IssueType | None = (
            IssueType(raw_issue) if raw_issue else None
        )
    except ValueError:
        return None
    domain = _resolve_domain(agent_cls.get("domain"), text)
    cost = _resolve_cost_signal(agent_cls.get("cost_signal"))

    # Agent-supplied flags override; else fall back to rule classification.
    if "is_sensitive" in agent_cls:
        is_sensitive = bool(agent_cls["is_sensitive"])
    else:
        is_sensitive = domain == Domain.SENSITIVE
    is_urgent = bool(agent_cls.get("is_urgent", _is_urgent(text)))
    complexity = float(agent_cls.get("task_complexity", _estimate_complexity(text)))

    def _clamp(x: Any) -> float:
        try:
            return max(0.0, min(1.0, float(x)))
        except (TypeError, ValueError):
            return 0.0

    candidates: list[DetectorResult] = []
    if issue_type is not None and not is_sensitive:
        candidates.append(DetectorResult(
            issue_type=issue_type,
            confidence=_clamp(agent_cls.get("confidence", 0.0)),
            severity=_clamp(agent_cls.get("severity", 0.0)),
            fixability=_clamp(agent_cls.get("fixability", 0.0)),
            cost_signal=cost,
            evidence=str(agent_cls.get("evidence_summary", "")),
        ))

    return DetectionOutput(
        domain=domain,
        is_sensitive=is_sensitive,
        task_complexity=complexity,
        is_urgent=is_urgent,
        candidates=candidates,
    )


def detect(text: str) -> DetectionOutput:
    """Run all heuristic detectors on a prompt text.

    Returns a DetectionOutput with domain, sensitivity flag, and candidate issues.
    The policy layer uses this as one input alongside semantic judgment.
    """
    # Empty / whitespace-only input is never a coaching candidate.
    # An agent shouldn't normally call detect() in this case, but if it
    # does (e.g. blank continuation message), we must not flag it.
    if not text or not text.strip():
        return DetectionOutput(
            domain=Domain.OTHER, is_sensitive=False,
            task_complexity=0.0, is_urgent=False, candidates=[],
        )

    domain = _classify_domain(text)
    is_sensitive = domain == Domain.SENSITIVE
    is_urgent = _is_urgent(text)
    complexity = _estimate_complexity(text)

    candidates: list[DetectorResult] = []

    # Social phrases and greetings are not task requests
    if SOCIAL_PHRASE_RE.match(text.strip()):
        return DetectionOutput(
            domain=domain, is_sensitive=False,
            task_complexity=0.0, is_urgent=False, candidates=[],
        )

    if not is_sensitive:
        # Check missing_output_contract
        if not _has_output_contract(text):
            chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            is_chinese = chinese_chars > len(text) * 0.3
            eff_len = _effective_length(text)
            # Chinese: need at least 5 chars (~2-3 meaningful words)
            # English: need at least 3 words, OR 2 words when an explicit
            # output verb is present (e.g. "Generate output.",
            # "Produce result.") — those short prompts still lack format.
            has_output_verb = bool(EXPLICIT_OUTPUT_VERB_RE.search(text))
            min_len = 5 if is_chinese else (2 if has_output_verb else 3)
            if eff_len >= min_len:
                candidates.append(DetectorResult(
                    issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                    confidence=0.70,
                    severity=0.65,
                    fixability=0.90,
                    cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                    evidence="prompt lacks any output format indicator",
                ))

        # Check overloaded_request
        is_over, over_conf = _is_overloaded(text)
        if is_over:
            # When over_conf >= 0.5 (threshold), boost confidence to at least 0.70
            # so the candidate isn't filtered out by policy's min_confidence gate.
            candidates.append(DetectorResult(
                issue_type=IssueType.OVERLOADED_REQUEST,
                confidence=min(max(over_conf + 0.2, 0.70), 1.0),
                severity=0.75,
                fixability=0.85,
                cost_signal=CostSignal.REWORK_REQUIRED,
                evidence="multiple action verbs + conjunctions suggest bundled phases",
            ))

        # Check missing_goal
        is_mg, mg_conf = _is_missing_goal(text)
        if is_mg:
            candidates.append(DetectorResult(
                issue_type=IssueType.MISSING_GOAL,
                confidence=mg_conf,
                severity=0.80,
                fixability=0.95,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                evidence="no recognizable goal verb or intent found",
            ))

        # Check unbound_reference — bare pronoun at start, no antecedent.
        # Severity kept at 0.82 so its score * conf beats missing_goal's
        # generic short-text fallback for prompts like "It's broken." where
        # both detectors fire and the referent issue is the more specific
        # diagnosis.
        is_ur, ur_conf = _is_unbound_reference(text)
        if is_ur:
            candidates.append(DetectorResult(
                issue_type=IssueType.UNBOUND_REFERENCE,
                confidence=ur_conf,
                severity=0.82,
                fixability=0.90,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                evidence="bare pronoun with no clear referent in prompt text",
            ))

        # Check missing_context — refers to "this code/file/bug" but no
        # code block / content is attached
        is_mc, mc_conf = _is_missing_context(text)
        if is_mc:
            candidates.append(DetectorResult(
                issue_type=IssueType.MISSING_CONTEXT,
                confidence=mc_conf,
                severity=0.75,
                fixability=0.95,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                evidence="prompt refers to specific content but none is attached",
            ))

        # Check missing_success_criteria — improve verb with no metric
        is_msc, msc_conf = _is_missing_success_criteria(text)
        if is_msc:
            candidates.append(DetectorResult(
                issue_type=IssueType.MISSING_SUCCESS_CRITERIA,
                confidence=msc_conf,
                severity=0.60,  # lower than others — hard to rule-detect
                fixability=0.85,
                cost_signal=CostSignal.REWORK_REQUIRED,
                evidence="improvement request without a verifiable success metric",
            ))

        # Check conflicting_instructions — explicit conflict markers
        is_ci, ci_conf = _is_conflicting_instructions(text)
        if is_ci:
            candidates.append(DetectorResult(
                issue_type=IssueType.CONFLICTING_INSTRUCTIONS,
                confidence=ci_conf,
                severity=0.70,
                fixability=0.85,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                evidence="prompt contains explicit conflicting requirements",
            ))

    return DetectionOutput(
        domain=domain,
        is_sensitive=is_sensitive,
        task_complexity=complexity,
        is_urgent=is_urgent,
        candidates=candidates,
    )
