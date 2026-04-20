"""User-visible text generation for the User Capability Coach.

Policy selects the action; templates only handle phrasing.
Rules:
  - Never evaluate personality
  - One issue per turn, one example per tip
  - light = shorter and always post-answer
  - standard = can be slightly more explicit, can be pre-answer
  - All text must be ignorable (no pressure to comply)
"""
from __future__ import annotations
from .taxonomy import IssueType, CoachMode


# ── Helper: detect language ───────────────────────────────────────────────────

def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ── Post-answer tip ───────────────────────────────────────────────────────────

POST_TIP_ZH: dict[IssueType, dict[CoachMode, str]] = {
    IssueType.MISSING_OUTPUT_CONTRACT: {
        CoachMode.LIGHT: (
            "下次加一句输出格式说明会让结果更直接可用，"
            "比如：「输出 JSON」「用 markdown 表格」「给编号列表」。"
        ),
        CoachMode.STANDARD: (
            "下次在末尾补一行格式要求，我就能直接给你可用的结果。\n"
            "比如把「帮我写文档」改成「帮我写 API 文档，输出 markdown，"
            "包含接口名、参数、返回值三列表格」。"
        ),
    },
    IssueType.OVERLOADED_REQUEST: {
        CoachMode.LIGHT: (
            "这类多阶段任务拆开给我会更稳，比如先只要「分析」，"
            "确认方向后再要「方案」。"
        ),
        CoachMode.STANDARD: (
            "把多步骤任务拆成几个独立请求，每次我能更专注、结果更可靠。\n"
            "比如：第一轮「分析现有架构问题」→ 确认后第二轮「设计改造方案」。"
        ),
    },
    IssueType.MISSING_GOAL: {
        CoachMode.LIGHT: (
            "下次在开头说明你想要什么，比如「总结」「批改」「重写」「评审」，"
            "我猜的成本就会低很多。"
        ),
        CoachMode.STANDARD: (
            "在请求开头说明目标，我就能少猜一步。\n"
            "比如把「帮我看看这个」改成「帮我评审这份代码，"
            "重点看逻辑正确性和边界处理」。"
        ),
    },
    IssueType.MISSING_CONTEXT: {
        CoachMode.LIGHT: (
            "下次把相关代码/文件内容贴出来，或用 @路径 告诉我去哪儿读，我就不用猜你指的是哪段。"
        ),
        CoachMode.STANDARD: (
            "把需要处理的上下文一次性给我，结果会更准。\n"
            "比如「这段代码有 bug」→ 「这段代码（@src/utils.py:42-58）运行时抛 IndexError，帮我定位」。"
        ),
    },
    IssueType.MISSING_CONSTRAINTS: {
        CoachMode.LIGHT: (
            "下次加一句边界条件，比如长度（3 句 / 200 字内）、受众（给团队 / 给外部）、或一个成功标准。"
        ),
        CoachMode.STANDARD: (
            "目标清楚但没边界，我只能按最通用的处理，往往过度或不足。\n"
            "比如「写个总结」→ 「给团队写一份 3 句话的总结，强调对 Q2 目标的影响」。"
        ),
    },
    IssueType.CONFLICTING_INSTRUCTIONS: {
        CoachMode.LIGHT: (
            "你提到的两条要求有冲突，我按其中一条处理了。下次明确主次（「优先 X，X 冲突时妥协 Y」）不用我猜。"
        ),
        CoachMode.STANDARD: (
            "两条要求冲突会让我频繁猜你的优先级。\n"
            "下次用「优先 X；不能兼得时牺牲 Y」的句式告诉我，结果会更贴你实际要的。"
        ),
    },
    IssueType.UNBOUND_REFERENCE: {
        CoachMode.LIGHT: (
            "你说的「它/这个」不确定指哪个，下次用具体名称或 @路径会更稳。"
        ),
        CoachMode.STANDARD: (
            "跨轮代词在我这里歧义很大，容易接错上文。\n"
            "下次把「它/这个/那个」换成具体名字或文件路径（@src/api.py 的 `fetch_user`），我第一次就能对上。"
        ),
    },
    IssueType.MISSING_SUCCESS_CRITERIA: {
        CoachMode.LIGHT: (
            "下次给一个成功标准（「跑通测试」「比 X 快 30%」「用户点击率提升」），我能自己判断是否做到。"
        ),
        CoachMode.STANDARD: (
            "没有验收标准我只能按「看起来合理」交付，你难验证、我也难迭代。\n"
            "下次加一句「做到了 X 才算完成」——比如「所有 pytest 通过」「Lighthouse 分 ≥ 90」——双方都省事。"
        ),
    },
}

POST_TIP_EN: dict[IssueType, dict[CoachMode, str]] = {
    IssueType.MISSING_OUTPUT_CONTRACT: {
        CoachMode.LIGHT: (
            "Next time, add one line specifying the format you want — "
            "e.g. 'output as JSON' or 'use a markdown table' — and I'll give you something ready to use."
        ),
        CoachMode.STANDARD: (
            "Adding a format line at the end lets me skip the guessing step.\n"
            "For example, instead of 'write me docs', try: 'write API docs as a markdown table "
            "with columns: endpoint, params, return value'."
        ),
    },
    IssueType.OVERLOADED_REQUEST: {
        CoachMode.LIGHT: (
            "Breaking multi-phase tasks into separate requests gets more reliable results — "
            "e.g. ask for 'analysis' first, then 'design' once that's confirmed."
        ),
        CoachMode.STANDARD: (
            "Multi-step tasks work better as separate requests where I can focus on one phase.\n"
            "Try: round 1 → 'analyze the current issues', round 2 (once confirmed) → 'design the solution'."
        ),
    },
    IssueType.MISSING_GOAL: {
        CoachMode.LIGHT: (
            "Leading with your goal — 'summarize', 'critique', 'rewrite', 'review' — "
            "saves us a guessing round."
        ),
        CoachMode.STANDARD: (
            "Starting with your intent cuts down on my assumptions.\n"
            "E.g. instead of 'look at this', try: "
            "'review this code focusing on edge cases and boundary handling'."
        ),
    },
    IssueType.MISSING_CONTEXT: {
        CoachMode.LIGHT: (
            "Next time, paste the relevant code/file or point to a path (e.g. @src/utils.py) so I don't have to guess what you mean."
        ),
        CoachMode.STANDARD: (
            "Giving me the context upfront gives a more accurate response.\n"
            "E.g. instead of 'this code has a bug', try: 'the function at @src/utils.py:42-58 throws IndexError at runtime, help me locate it'."
        ),
    },
    IssueType.MISSING_CONSTRAINTS: {
        CoachMode.LIGHT: (
            "Adding a constraint next time — length (3 sentences, under 200 words), audience (internal / external), or a success metric — helps me dial in."
        ),
        CoachMode.STANDARD: (
            "With a clear goal but no constraints I default to generic and either over- or under-deliver.\n"
            "E.g. 'write a summary' → 'a 3-sentence summary for the team emphasizing impact on Q2 targets'."
        ),
    },
    IssueType.CONFLICTING_INSTRUCTIONS: {
        CoachMode.LIGHT: (
            "Two of your requirements conflict — I picked one. Next time specify which wins so I don't have to guess."
        ),
        CoachMode.STANDARD: (
            "Conflicting requirements make me repeatedly guess your priority.\n"
            "Phrase it as 'prioritize X; sacrifice Y when they conflict' so I can deliver what you actually want."
        ),
    },
    IssueType.UNBOUND_REFERENCE: {
        CoachMode.LIGHT: (
            "The 'it/this/that' you mentioned isn't unambiguous — naming the thing or using a path (@file) saves me a guess."
        ),
        CoachMode.STANDARD: (
            "Cross-turn pronouns have high ambiguity on my side — I often bind to the wrong antecedent.\n"
            "Replace 'it/this/that' with a name or path (e.g. `fetch_user` in @src/api.py) and I'll lock on first try."
        ),
    },
    IssueType.MISSING_SUCCESS_CRITERIA: {
        CoachMode.LIGHT: (
            "A success criterion next time ('tests pass', '30% faster than X', 'click-through rate up') lets me verify my own work."
        ),
        CoachMode.STANDARD: (
            "Without a verifiable success bar I default to 'looks reasonable' — hard for you to check, hard for me to iterate.\n"
            "Add one line like 'done when all pytest tests pass' or 'Lighthouse score ≥ 90' and we both save time."
        ),
    },
}


def post_answer_tip(issue: IssueType, mode: CoachMode, prompt_text: str) -> str:
    """Return post-answer coaching text for an issue + mode.

    Falls back through mode tiers so adding a new CoachMode (e.g. STRICT)
    doesn't silently return empty strings — strict shares standard's text
    because the two differ only in invocation frequency, not in phrasing.
    Light → light text; standard or strict → standard text (standard fallback
    to light if missing).
    """
    table = POST_TIP_ZH if _is_chinese(prompt_text) else POST_TIP_EN
    inner = table.get(issue, {})
    return (
        inner.get(mode)
        or inner.get(CoachMode.STANDARD)
        or inner.get(CoachMode.LIGHT)
        or ""
    )


# ── Pre-answer micro-nudge (standard only) ────────────────────────────────────

PRE_NUDGE_ZH: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "我可以先继续，但还缺一个格式说明。"
        "如果你不补充，我会按 markdown 文档处理。"
    ),
    IssueType.OVERLOADED_REQUEST: (
        "我可以先继续，但这里有多个阶段。"
        "我会按「先分析再实施」顺序处理，你可以在每步确认后再进行下一步。"
    ),
    IssueType.MISSING_GOAL: (
        "我可以先继续，但还缺一个明确目标。"
        "如果你不补充，我会按「总结」处理。"
    ),
    IssueType.MISSING_CONTEXT: (
        "我可以先继续，但没看到你要处理的具体内容。"
        "如果你不贴代码/文件，我会按对相关路径的合理猜测处理。"
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "我可以先继续，但没有边界条件（长度 / 受众 / 成功标准）。"
        "我会按常规默认值处理，如果尺寸或范围不对请及时告诉我。"
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "我可以先继续，但你的两条要求有冲突。"
        "我会优先 X，牺牲 Y——如果方向反了，纠正我。"
    ),
    IssueType.UNBOUND_REFERENCE: (
        "我可以先继续，但你提到的「它/这个」有几个可能的指代。"
        "我会按最近的上下文匹配处理，不对请明确一下具体是哪个。"
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "我可以先继续，但没有验收标准。"
        "我会按通用最佳实践交付，如果有具体指标请补上我会更对焦。"
    ),
}

PRE_NUDGE_EN: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "I can proceed, but I'm missing a format preference. "
        "I'll default to a markdown document unless you specify otherwise."
    ),
    IssueType.OVERLOADED_REQUEST: (
        "I can proceed, but this request has multiple phases. "
        "I'll handle them in order — 'analyze first, then implement' — and pause for confirmation between steps."
    ),
    IssueType.MISSING_GOAL: (
        "I can proceed, but I'm not sure what outcome you want. "
        "I'll default to a summary — let me know if you meant something else."
    ),
    IssueType.MISSING_CONTEXT: (
        "I can proceed, but I don't see the specific content you're referring to. "
        "I'll make a reasonable guess based on the paths I have — correct me if I pick wrong."
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "I can proceed, but there are no bounds (length / audience / success metric). "
        "I'll use generic defaults — tell me if the size or scope is off."
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "I can proceed, but two of your requirements conflict. "
        "I'll prioritize X and sacrifice Y — tell me if I picked the wrong side."
    ),
    IssueType.UNBOUND_REFERENCE: (
        "I can proceed, but the 'it/this/that' you mentioned has several possible referents. "
        "I'll bind to the nearest context — tell me if I pick wrong."
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "I can proceed, but there's no success criterion. "
        "I'll deliver to general best practices — add a metric if you want me to target something specific."
    ),
}


def pre_answer_micro_nudge(issue: IssueType, prompt_text: str) -> str:
    if _is_chinese(prompt_text):
        return PRE_NUDGE_ZH.get(issue, "")
    return PRE_NUDGE_EN.get(issue, "")


# ── Retrospective reminder ────────────────────────────────────────────────────

RETRO_ZH: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "最近几次任务里，我经常需要猜你想要什么格式。"
        "以后固定在末尾加一行格式要求，通常能让我首轮更准。"
        "比如：「输出 JSON」「用表格」「要代码块」。"
    ),
    IssueType.OVERLOADED_REQUEST: (
        "最近几次我收到了多阶段捆绑的请求。"
        "拆成一步一步给我，每步的结果会更扎实，也更容易调整方向。"
    ),
    IssueType.MISSING_GOAL: (
        "最近几次我需要猜你的意图。"
        "在开头说明目标，比如「总结」「批改」「重写」「评审」，"
        "通常能减少一两轮澄清。"
    ),
    IssueType.MISSING_CONTEXT: (
        "最近几次请求里我都缺少必要的代码/文件上下文。"
        "以后把内容直接贴出来或用 @路径，我能首轮就定位到具体位置。"
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "最近几次请求虽然目标清楚，但都没给边界（长度 / 受众 / 成功标准）。"
        "固定补一句边界，结果会更贴你要的尺寸和深度。"
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "最近几次请求里出现过矛盾的要求，我每次都得猜优先级。"
        "下次直接告诉我「冲突时以 X 为准」，我的产出会稳定得多。"
    ),
    IssueType.UNBOUND_REFERENCE: (
        "最近几次我在跨轮代词（它/这个/那个）上绑错了上下文。"
        "用具体名称或 @路径替代会显著降低出错概率。"
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "最近几次任务我只能按「看起来合理」交付。"
        "加一个可验证的标准（测试通过 / 指标阈值），你能验收、我能自检。"
    ),
}

RETRO_EN: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "In several recent tasks, I've had to guess your preferred output format. "
        "Adding one line at the end — like 'output JSON' or 'use a table' — "
        "usually gets you a usable result on the first try."
    ),
    IssueType.OVERLOADED_REQUEST: (
        "A few recent requests bundled multiple phases together. "
        "Breaking them into separate steps — one phase per message — "
        "lets me give more focused and adjustable results."
    ),
    IssueType.MISSING_GOAL: (
        "In several recent messages, I've had to infer your goal. "
        "Leading with your intent — 'summarize', 'critique', 'rewrite', 'review' — "
        "usually cuts down clarification rounds."
    ),
    IssueType.MISSING_CONTEXT: (
        "A few recent requests referenced code/files I couldn't see. "
        "Pasting the content or using @path lets me lock onto the right place on the first pass."
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "Several recent requests had clear goals but no bounds (length / audience / success metric). "
        "Adding one constraint line matches output to what you actually wanted."
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "A few recent requests had conflicting requirements, and I had to guess priority each time. "
        "Telling me 'when X and Y conflict, pick X' upfront gives much more consistent results."
    ),
    IssueType.UNBOUND_REFERENCE: (
        "In several recent turns I bound 'it/this/that' to the wrong antecedent. "
        "Substituting a specific name or @path dramatically reduces that error."
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "A few recent tasks had no verifiable success criterion, so I delivered to 'looks reasonable'. "
        "Adding one — tests pass, metric threshold — lets both of us check the work."
    ),
}


def retrospective_reminder(issue: IssueType, prompt_text: str) -> str:
    if _is_chinese(prompt_text):
        return RETRO_ZH.get(issue, "")
    return RETRO_EN.get(issue, "")


# ── Session-level nudge (within-session pattern) ─────────────────────────────
#
# Fires when the user has shown the same issue in several recent turns of
# the CURRENT session. Phrasing is more immediate than retrospective_reminder
# (which talks about "recent tasks" across days) and less generic than
# post_answer_tip (which addresses only this turn).

SESSION_NUDGE_ZH: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "这个会话里最近几次你都没指定输出格式。"
        "下次在一开头就加一行「输出 JSON / 用表格 / 要代码块」，我每轮都能直接给你可用的结果，不用来回猜。"
    ),
    IssueType.OVERLOADED_REQUEST: (
        "这个会话里最近几次都是多阶段捆绑的请求。"
        "拆成单阶段给我——每步结果更扎实，方向不对也容易在早期调整，不用整个返工。"
    ),
    IssueType.MISSING_GOAL: (
        "这个会话里最近几次你的请求里都没明确说要什么结果。"
        "下次开头固定一个意图动词（总结/批改/重写/评审），能少一两轮澄清。"
    ),
    IssueType.MISSING_CONTEXT: (
        "这个会话里最近几次你都在引用我看不到的内容。"
        "把相关代码/文件贴过来或用 @路径，我能一次就对上，不用每轮互相猜。"
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "这个会话里最近几次请求都缺边界条件。"
        "每次固定补一句范围（长度 / 受众 / 成功标准），我的交付会更对焦。"
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "这个会话里最近几次出现矛盾要求。"
        "下次直接写「冲突时以 X 为准」，能避免我反复猜你的优先级。"
    ),
    IssueType.UNBOUND_REFERENCE: (
        "这个会话里最近几次跨轮代词有歧义。"
        "把「它/这个/那个」换成具体名字或 @路径，能显著降低误解。"
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "这个会话里最近几次都没给验收标准。"
        "加一个可量化标准（测试通过 / 指标达标），双方都能判断是否做完。"
    ),
}

SESSION_NUDGE_EN: dict[IssueType, str] = {
    IssueType.MISSING_OUTPUT_CONTRACT: (
        "I've noticed a few recent turns in this session didn't specify an output format. "
        "Adding one line upfront — 'as JSON', 'as a table', 'code block' — saves a round of guessing each time."
    ),
    IssueType.OVERLOADED_REQUEST: (
        "A few recent turns in this session bundled multiple phases. "
        "Splitting them one phase at a time gives more focused results and lets you course-correct earlier."
    ),
    IssueType.MISSING_GOAL: (
        "A few recent turns in this session didn't lead with a clear intent. "
        "Starting with a verb like 'summarize', 'critique', 'rewrite' cuts down clarification rounds."
    ),
    IssueType.MISSING_CONTEXT: (
        "A few recent turns in this session referenced content I can't see. "
        "Pasting the code/file or using @path lets me lock on the first try instead of round-tripping."
    ),
    IssueType.MISSING_CONSTRAINTS: (
        "Several recent turns in this session lacked constraints. "
        "A one-line scope (length / audience / success metric) keeps my output focused."
    ),
    IssueType.CONFLICTING_INSTRUCTIONS: (
        "A few recent turns in this session had conflicting requirements. "
        "Specifying 'prioritize X when conflicts arise' prevents me from guessing priority repeatedly."
    ),
    IssueType.UNBOUND_REFERENCE: (
        "A few recent turns in this session used ambiguous cross-turn pronouns. "
        "Replacing 'it/this/that' with a name or @path dramatically reduces misinterpretation."
    ),
    IssueType.MISSING_SUCCESS_CRITERIA: (
        "Several recent turns in this session gave no success criterion. "
        "Adding a verifiable metric (tests pass / threshold met) lets both of us confirm the work."
    ),
}


def session_pattern_nudge(issue: IssueType, prompt_text: str) -> str:
    if _is_chinese(prompt_text):
        return SESSION_NUDGE_ZH.get(issue, "")
    return SESSION_NUDGE_EN.get(issue, "")


# ── First-use disclosure ──────────────────────────────────────────────────────

FIRST_USE_ZH = """\
已开启 Prompt 教练（light 模式）。以下是它会做和不会做的事：

✅ 会做：
• 当你的请求存在明显缺口时，在答案后附一条可忽略的改写建议
• 在你授权后，记录反复出现的提问模式（14 天观察期结束后才开始提醒）
• 响应 /coach 系列指令（/coach off、/coach status、/coach why 等）

🚫 不会做：
• 评判人格或说"你这个 prompt 不好"
• 存储你的原始问题文本
• 在敏感或紧急话题里增加额外负担
• 在你未开启前发出任何主动辅导（你现在才刚开启）

📴 随时可关闭：输入 /coach off 或"关闭教练"即可。

当前处于 14 天观察期，不会出现长期复盘提醒。\
"""

FIRST_USE_EN = """\
Prompt Coach is now on (light mode). Here's what it will and won't do:

✅ Will do:
• When your request has a clear gap, append a short, ignorable suggestion after the answer
• Record recurring prompt patterns (with your permission) and give a recap after a 14-day observation period
• Respond to /coach commands (/coach off, /coach status, /coach why, etc.)

🚫 Won't do:
• Judge your personality or say "your prompt is bad"
• Store your raw prompt text
• Add friction in sensitive or urgent situations
• Produce any proactive coaching unless you've enabled it (you just did)

📴 Turn off anytime: type /coach off or "turn off coach".

You're currently in a 14-day observation period — no long-term pattern reminders yet.\
"""


def first_use_disclosure(prompt_text: str = "") -> str:
    if _is_chinese(prompt_text):
        return FIRST_USE_ZH
    return FIRST_USE_EN


# ── Status reply ──────────────────────────────────────────────────────────────

def coach_status(
    mode: str,
    memory_enabled: bool,
    observation_ends: str | None,
    checked_7d: int,
    surfaced_7d: int,
    silent_7d: int,
    proactive_7d: int,
    retro_7d: int,
    prompt_text: str = "",
    dismissed_until: str | None = None,
) -> str:
    if _is_chinese(prompt_text):
        lines = [f"Prompt 教练状态：{mode}"]
        lines.append(f"长期记忆：{'开启' if memory_enabled else '关闭'}")
        if observation_ends:
            lines.append(f"观察期截止：{observation_ends}")
        lines.append(f"本周检查：{checked_7d} 次")
        lines.append(f"本周可见提醒：{surfaced_7d} 次")
        lines.append(f"本周静默处理：{silent_7d} 次")
        lines.append(f"本周主动提示：{proactive_7d} 次")
        lines.append(f"本周复盘提醒：{retro_7d} 次")
        if dismissed_until:
            lines.append(f"临时静默至：{dismissed_until}")
        return "\n".join(lines)
    else:
        lines = [f"Prompt Coach status: {mode}"]
        lines.append(f"Long-term memory: {'on' if memory_enabled else 'off'}")
        if observation_ends:
            lines.append(f"Observation period ends: {observation_ends}")
        lines.append(f"Checks this week: {checked_7d}")
        lines.append(f"Visible coaching this week: {surfaced_7d}")
        lines.append(f"Silent decisions this week: {silent_7d}")
        lines.append(f"Proactive tips this week: {proactive_7d}")
        lines.append(f"Retrospective reminders this week: {retro_7d}")
        if dismissed_until:
            lines.append(f"Silenced until: {dismissed_until}")
        return "\n".join(lines)


# ── Why-reminded reply ────────────────────────────────────────────────────────

def why_reminded(chain: dict, prompt_text: str = "") -> str:
    """Translate an explanation_chain dict into human-readable text."""
    issue = chain.get("issue_type", "unknown")
    evidence = chain.get("evidence_count", "?")
    sessions = chain.get("distinct_sessions", "?")
    cost = chain.get("cost_count", "?")
    last_notified = chain.get("last_notified_at") or "never"
    cooldown = chain.get("cooldown_days", "?")
    reason = chain.get("allow_reason", "")

    if _is_chinese(prompt_text):
        return (
            f"上次提醒的原因：\n"
            f"• 问题类型：{issue}\n"
            f"• 过去记录次数：{evidence} 次，涉及 {sessions} 个会话\n"
            f"• 其中造成明显成本的次数：{cost} 次\n"
            f"• 上次提醒时间：{last_notified}（冷却期 {cooldown} 天）\n"
            f"• 本次允许原因：{reason}"
        )
    return (
        f"Why you were reminded:\n"
        f"• Issue type: {issue}\n"
        f"• Evidence collected: {evidence} observations across {sessions} sessions\n"
        f"• Observations with cost signal: {cost}\n"
        f"• Last notified: {last_notified} (cooldown: {cooldown} days)\n"
        f"• Why allowed this time: {reason}"
    )


def coach_off_ack(prompt_text: str = "") -> str:
    if _is_chinese(prompt_text):
        return (
            "已关闭 Prompt 教练。本地数据保留，"
            "可用 `coach memory export` 导出或 `coach uninstall` 清理。"
        )
    return (
        "Prompt Coach is off. Local data is retained — "
        "use `coach memory export` to export or `coach uninstall` to remove."
    )
