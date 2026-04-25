"""Tests for templates.py — user-visible text generation."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import templates
from tools.taxonomy import IssueType, CoachMode


FORBIDDEN_PHRASES = [
    "你这个 prompt 不好",
    "your prompt is bad",
    "you always",
    "you never",
    "你总是",
    "你从不",
    "poor question",
    "bad prompt",
    "不会提问",
]


def _no_forbidden(text: str) -> bool:
    lower = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase.lower() in lower:
            return False
    return True


# ── Post-answer tip ────────────────────────────────────────────────────────

class TestPostAnswerTip:
    @pytest.mark.parametrize("issue", list(IssueType)[:3])
    @pytest.mark.parametrize("mode", [CoachMode.LIGHT, CoachMode.STANDARD])
    def test_zh_not_empty(self, issue, mode):
        tip = templates.post_answer_tip(issue, mode, "帮我写一个接口文档")
        if issue.value in ("missing_output_contract", "overloaded_request", "missing_goal"):
            assert tip, f"Expected non-empty tip for {issue}/{mode}"

    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_no_forbidden_phrases_zh(self, issue):
        for mode in [CoachMode.LIGHT, CoachMode.STANDARD]:
            tip = templates.post_answer_tip(issue, mode, "帮我写一个接口文档")
            assert _no_forbidden(tip), f"Forbidden phrase in: {tip!r}"

    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_no_forbidden_phrases_en(self, issue):
        for mode in [CoachMode.LIGHT, CoachMode.STANDARD]:
            tip = templates.post_answer_tip(issue, mode, "Write me a report")
            assert _no_forbidden(tip), f"Forbidden phrase in: {tip!r}"

    def test_light_shorter_than_standard(self):
        for issue in [IssueType.MISSING_OUTPUT_CONTRACT, IssueType.OVERLOADED_REQUEST, IssueType.MISSING_GOAL]:
            light = templates.post_answer_tip(issue, CoachMode.LIGHT, "some prompt")
            standard = templates.post_answer_tip(issue, CoachMode.STANDARD, "some prompt")
            assert len(light) <= len(standard), f"light should be <= standard for {issue}"


# ── Pre-answer nudge ───────────────────────────────────────────────────────

class TestPreAnswerNudge:
    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_zh_nudge_not_empty(self, issue):
        nudge = templates.pre_answer_micro_nudge(issue, "看看这个")
        assert nudge

    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_nudge_mentions_continuation(self, issue):
        nudge = templates.pre_answer_micro_nudge(issue, "帮我做这个")
        # Should say it can proceed despite the issue
        assert "继续" in nudge or "先" in nudge


# ── Retrospective reminder ─────────────────────────────────────────────────

class TestRetrospectiveReminder:
    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_zh_reminder_not_empty(self, issue):
        r = templates.retrospective_reminder(issue, "帮我写")
        assert r

    def test_no_forbidden_phrases_in_retro(self):
        for issue in [IssueType.MISSING_OUTPUT_CONTRACT, IssueType.OVERLOADED_REQUEST, IssueType.MISSING_GOAL]:
            r = templates.retrospective_reminder(issue, "帮我写接口文档")
            assert _no_forbidden(r)
            r_en = templates.retrospective_reminder(issue, "Write me docs")
            assert _no_forbidden(r_en)


# ── First-use disclosure ───────────────────────────────────────────────────

class TestFirstUseDisclosure:
    def test_zh_contains_three_points(self):
        disclosure = templates.first_use_disclosure("帮我开启教练")
        assert "会做" in disclosure
        assert "不会做" in disclosure
        assert "关闭" in disclosure
        assert "当前处于 14 天观察期" not in disclosure
        assert "长期记忆仍是关闭" in disclosure

    def test_en_contains_three_points(self):
        disclosure = templates.first_use_disclosure("turn on coach")
        assert "Will do" in disclosure
        assert "Won't do" in disclosure
        assert "off" in disclosure.lower()
        assert "currently in a 14-day observation period" not in disclosure
        assert "Long-term memory is still off" in disclosure


# ── Status and why-reminded ────────────────────────────────────────────────

class TestPostTipModeFallback:
    """post_answer_tip must not return an empty string when given a mode
    that isn't explicitly keyed in the template dict — otherwise adding
    a new CoachMode (STRICT) silently breaks visible coaching text."""

    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
        IssueType.MISSING_CONTEXT,
        IssueType.UNBOUND_REFERENCE,
    ])
    def test_strict_mode_falls_back_to_standard(self, issue):
        # Every visible issue must produce non-empty text in strict.
        assert templates.post_answer_tip(issue, CoachMode.STRICT, "中文 prompt")
        assert templates.post_answer_tip(issue, CoachMode.STRICT, "english prompt")

    def test_strict_matches_standard_text_when_no_override(self):
        # Strict currently has no dedicated copy — it should render
        # exactly what standard would render for the same issue.
        for issue in (IssueType.MISSING_OUTPUT_CONTRACT, IssueType.MISSING_GOAL):
            strict_zh = templates.post_answer_tip(issue, CoachMode.STRICT, "中文")
            standard_zh = templates.post_answer_tip(issue, CoachMode.STANDARD, "中文")
            assert strict_zh == standard_zh


class TestPromotedShadowTemplates:
    """④ promoted all 5 formerly-shadow types; each must have
    non-empty templates in all 4 surfaces, ZH + EN."""

    PROMOTED = [
        IssueType.MISSING_CONTEXT,
        IssueType.MISSING_CONSTRAINTS,
        IssueType.CONFLICTING_INSTRUCTIONS,
        IssueType.UNBOUND_REFERENCE,
        IssueType.MISSING_SUCCESS_CRITERIA,
    ]

    @pytest.mark.parametrize("issue", PROMOTED)
    def test_post_tip_both_modes_both_langs(self, issue):
        for mode in (CoachMode.LIGHT, CoachMode.STANDARD):
            for prompt in ("中文 prompt", "english prompt"):
                t = templates.post_answer_tip(issue, mode, prompt)
                assert t, f"empty POST tip for {issue.value}/{mode.value}"

    @pytest.mark.parametrize("issue", PROMOTED)
    def test_pre_nudge_both_langs(self, issue):
        for prompt in ("中文 prompt", "english prompt"):
            t = templates.pre_answer_micro_nudge(issue, prompt)
            assert t, f"empty PRE nudge for {issue.value}"

    @pytest.mark.parametrize("issue", PROMOTED)
    def test_retrospective_both_langs(self, issue):
        for prompt in ("中文 prompt", "english prompt"):
            t = templates.retrospective_reminder(issue, prompt)
            assert t, f"empty RETRO for {issue.value}"

    @pytest.mark.parametrize("issue", PROMOTED)
    def test_session_nudge_both_langs(self, issue):
        for prompt in ("中文 prompt", "english prompt"):
            t = templates.session_pattern_nudge(issue, prompt)
            assert t, f"empty SESSION nudge for {issue.value}"


class TestSessionPatternNudge:
    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_zh_nudge_not_empty(self, issue):
        txt = templates.session_pattern_nudge(issue, "中文 prompt")
        assert txt
        assert "这个会话" in txt or "最近" in txt

    @pytest.mark.parametrize("issue", [
        IssueType.MISSING_OUTPUT_CONTRACT,
        IssueType.OVERLOADED_REQUEST,
        IssueType.MISSING_GOAL,
    ])
    def test_en_nudge_not_empty(self, issue):
        txt = templates.session_pattern_nudge(issue, "english prompt")
        assert txt
        assert "session" in txt.lower() or "recent" in txt.lower()

    def test_session_nudge_distinct_from_retrospective(self):
        for issue in [IssueType.MISSING_OUTPUT_CONTRACT, IssueType.MISSING_GOAL]:
            retro = templates.retrospective_reminder(issue, "中文")
            session = templates.session_pattern_nudge(issue, "中文")
            assert retro != session


class TestStatusAndWhy:
    def test_status_zh(self):
        status = templates.coach_status(
            mode="light",
            memory_enabled=True,
            observation_ends="2026-05-01",
            checked_7d=3,
            surfaced_7d=1,
            silent_7d=2,
            proactive_7d=1,
            retro_7d=0,
            prompt_text="状态",
        )
        assert "light" in status
        assert "开启" in status
        assert "检查" in status

    def test_why_reminded_zh(self):
        chain = {
            "issue_type": "missing_output_contract",
            "evidence_count": 5,
            "distinct_sessions": 3,
            "cost_count": 2,
            "last_notified_at": None,
            "cooldown_days": 14,
            "allow_reason": "all conditions met",
        }
        text = templates.why_reminded(chain, "为什么提醒我")
        assert "missing_output_contract" in text
        assert "5" in text

    def test_coach_off_ack_zh(self):
        ack = templates.coach_off_ack("关闭教练")
        assert "关闭" in ack or "off" in ack.lower()
