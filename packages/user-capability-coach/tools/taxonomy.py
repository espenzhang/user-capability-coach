"""Stable enumerations and constants for the User Capability Coach."""
from __future__ import annotations
from enum import Enum


class IssueType(str, Enum):
    MISSING_OUTPUT_CONTRACT = "missing_output_contract"
    OVERLOADED_REQUEST = "overloaded_request"
    MISSING_GOAL = "missing_goal"
    # v1 shadow-only — detected but never shown to user
    MISSING_CONTEXT = "missing_context"
    MISSING_CONSTRAINTS = "missing_constraints"
    CONFLICTING_INSTRUCTIONS = "conflicting_instructions"
    UNBOUND_REFERENCE = "unbound_reference"
    MISSING_SUCCESS_CRITERIA = "missing_success_criteria"


# Issue types that can produce user-facing coaching. Expanded from the
# original 3-type v1 set to all 8 once agent-first classification (②)
# made open-ended detection viable. The original 3 have full rule-based
# fallbacks; the 5 newer types are primarily agent-driven, with rule
# fallbacks only where a high-precision signal exists (unbound_reference,
# missing_context).
V1_VISIBLE_ISSUES: frozenset[IssueType] = frozenset({
    IssueType.MISSING_OUTPUT_CONTRACT,
    IssueType.OVERLOADED_REQUEST,
    IssueType.MISSING_GOAL,
    IssueType.MISSING_CONTEXT,
    IssueType.MISSING_CONSTRAINTS,
    IssueType.CONFLICTING_INSTRUCTIONS,
    IssueType.UNBOUND_REFERENCE,
    IssueType.MISSING_SUCCESS_CRITERIA,
})

# Types where the rule detector can produce a reliable candidate on its
# own. Missing from this set (and therefore agent-classification-only):
#   - missing_constraints: overlaps too much with missing_output_contract;
#     distinguishing them reliably via regex would fight the latter's 100%
#     weak-prompt hit rate.
# Types WITH weak rule fallbacks (lower confidence, may not reach policy
# thresholds without agent agreement):
#   - missing_success_criteria: improve-verb + no metric heuristic
#   - conflicting_instructions: explicit conflict markers only
RULE_DETECTABLE_ISSUES: frozenset[IssueType] = frozenset({
    IssueType.MISSING_OUTPUT_CONTRACT,
    IssueType.OVERLOADED_REQUEST,
    IssueType.MISSING_GOAL,
    IssueType.UNBOUND_REFERENCE,
    IssueType.MISSING_CONTEXT,
    IssueType.MISSING_SUCCESS_CRITERIA,
    IssueType.CONFLICTING_INSTRUCTIONS,
})


class Action(str, Enum):
    NONE = "none"
    SILENT_REWRITE = "silent_rewrite"
    POST_ANSWER_TIP = "post_answer_tip"
    PRE_ANSWER_MICRO_NUDGE = "pre_answer_micro_nudge"
    RETROSPECTIVE_REMINDER = "retrospective_reminder"
    # Short-term, within-session pattern. E.g. user had missing_goal in 3
    # of the last 5 turns — coach surfaces this even when the CURRENT
    # prompt alone wouldn't trigger a per-turn tip. Distinct from
    # retrospective which is cross-session and gated by the 14-day
    # observation period.
    SESSION_PATTERN_NUDGE = "session_pattern_nudge"


class CoachMode(str, Enum):
    OFF = "off"
    LIGHT = "light"
    STANDARD = "standard"
    # Strict mode: agent is instructed (via CLAUDE.md) to invoke
    # `coach select-action --text ... --session-id ...` at the END of
    # every non-trivial turn (post-hoc), instead of relying on Claude's
    # discretionary skill invocation. Also raises the 7-day budget caps
    # so a high-volume user doesn't hit the limit mid-week.
    STRICT = "strict"


class Domain(str, Enum):
    CODING = "coding"
    WRITING = "writing"
    RESEARCH = "research"
    PLANNING = "planning"
    OPS = "ops"
    SENSITIVE = "sensitive"
    OTHER = "other"


class PatternStatus(str, Enum):
    ACTIVE = "active"
    COOLING = "cooling"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class CostSignal(str, Enum):
    HIGH_RISK_GUESS = "high_risk_guess"
    CLARIFICATION_NEEDED = "clarification_needed"
    OUTPUT_FORMAT_MISMATCH = "output_format_mismatch"
    REWORK_REQUIRED = "rework_required"
    NONE = "none"


# Micro-habit suggestions per issue type
MICRO_HABITS: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "在请求末尾加一行说明期望格式，例如：'输出 JSON / markdown 表格 / 编号列表 / 代码块'"
    ),
    IssueType.OVERLOADED_REQUEST: (
        "把多阶段任务拆开逐步给我，每次只交代一个阶段，结果会更稳"
    ),
    IssueType.MISSING_GOAL: (
        "在请求开头说明你想要什么结果：总结 / 批改 / 重写 / 评审 / 生成"
    ),
    IssueType.MISSING_CONTEXT: (
        "把相关代码/文件贴出来，或用 @路径 告诉我去哪儿读，我就不用猜"
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "加一句边界：长度（3 句 / 200 字内）、受众（内部 / 外部）、或一个成功标准"
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "两条要求冲突时，明确告诉我哪个优先，我不用猜"
    ),
    IssueType.UNBOUND_REFERENCE: (
        "下次用具体名称或路径替代「它/这个/那个」，避免跨轮歧义"
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "给一个可验证的成功标准（跑通测试 / 比 X 快 / 用户点击率提升），我能自己判断是否做到"
    ),
}

MICRO_HABITS_EN: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "Add one line at the end specifying the format you want, e.g. 'output as JSON / markdown table / numbered list / code block'"
    ),
    IssueType.OVERLOADED_REQUEST: (
        "Split multi-phase tasks into separate requests — one phase at a time gives more reliable results"
    ),
    IssueType.MISSING_GOAL: (
        "Start with what you want: summarize / critique / rewrite / review / generate"
    ),
    IssueType.MISSING_CONTEXT: (
        "Paste the relevant code/file or point to a path — I don't have to guess what you mean"
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "Add a constraint: length (3 sentences / under 200 words), audience (internal / external), or a success metric"
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "When two requirements conflict, tell me which wins — saves me from guessing the priority"
    ),
    IssueType.UNBOUND_REFERENCE: (
        "Name the specific thing instead of 'it' / 'this' / 'that' when the referent isn't obvious"
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "Give a verifiable success criterion (tests pass / faster than X / higher click-through) so I can check my own work"
    ),
}

SENSITIVE_KEYWORDS: frozenset[str] = frozenset({
    # health
    "cancer", "tumor", "diagnosis", "病", "癌", "医院", "医生", "住院", "手术",
    "medication", "药", "药物", "治疗",
    # mental health
    "suicide", "自杀", "抑郁", "焦虑", "恐慌", "panic attack", "depression",
    "depressed", "anxiety", "anxious", "mental health", "心理", "心理健康", "不想活",
    # legal
    "lawsuit", "被告", "起诉", "法律", "律师", "诉讼", "遗嘱", "离婚", "divorce",
    # financial distress
    "破产", "bankruptcy", "债务", "负债", "loan shark",
    # emergency (not "emergency"/"紧急" alone — those commonly mark engineering
    # urgency like "紧急线上 bug" and are separately handled by URGENCY_RE.
    # Only keep the terms that indicate personal/life-threatening emergencies.)
    "救命", "事故", "accident",
    # family crisis
    "孩子失踪", "家暴", "domestic violence", "abuse",
    # job loss
    "裁员", "失业", "被开除", "laid off", "fired",
    # bereavement
    "去世", "死了", "passed away", "死亡", "遗产",
    # addiction
    "addiction", "成瘾", "戒毒", "戒酒",
    # suicidal ideation / mental distress (Chinese)
    "不好的念头", "轻生", "了结", "活不下去", "不想活了", "寻死", "死了算了",
    "自我伤害", "割腕", "跳楼", "自残",
    # legal (expanded)
    "sued", "suing", "被起诉", "上诉", "仲裁", "判决", "法院", "律师费",
    # financial distress (expanded)
    "财务压力", "喘不过气", "还不起", "贷款压力", "逾期", "欠债",
    "资金困难", "周转不过来",
    # family crisis / child safety
    # "崩溃了" removed — too broad ("系统崩溃了"/"服务崩溃了" are common tech
    # usage). Emotional breakdowns are still covered by "情绪崩溃".
    "吵架", "家庭矛盾", "情绪很差", "心情很差", "情绪崩溃",
    "孩子失踪", "孩子走失", "child hasn't come home", "can't reach",
    "学习障碍", "发育迟缓", "特殊需要", "孩子的问题",
    # job loss (expanded)
    "裁了", "被裁", "失业了", "工作没了",
    # domestic abuse
    "家暴", "打我", "威胁我", "abusive", "abuser",
})
