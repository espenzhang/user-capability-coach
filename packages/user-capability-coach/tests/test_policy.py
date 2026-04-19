"""Tests for policy.py — action selection logic."""
import pytest
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.policy import select_action, should_emit_retrospective, PolicyInput, PatternSummary, PolicyConfig, SessionPatternSummary
from tools.detectors import detect
from tools.taxonomy import Action, CoachMode, IssueType, PatternStatus


def _make_input(
    text: str,
    mode: CoachMode = CoachMode.LIGHT,
    proactive_7d: int = 0,
    user_dismissed: bool = False,
    top_pattern: PatternSummary | None = None,
    obs_ends: datetime | None = None,
    session_pattern: SessionPatternSummary | None = None,
    session_already_nudged: bool = False,
) -> PolicyInput:
    return PolicyInput(
        mode=mode,
        detection=detect(text),
        memory_enabled=True,
        observation_period_ends_at=obs_ends,
        proactive_count_7d=proactive_7d,
        retrospective_count_7d=0,
        last_notified_pattern=None,
        last_notified_at=None,
        user_dismissed_recently=user_dismissed,
        top_pattern=top_pattern,
        session_pattern=session_pattern,
        session_already_nudged=session_already_nudged,
    )


# ── Mode=off suppresses everything ─────────────────────────────────────────

class TestModeOff:
    def test_off_returns_none(self):
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.OFF)
        result = select_action(inp)
        assert result.action == Action.NONE
        assert "off" in result.suppressed_reason

    def test_off_even_for_weak_prompt(self):
        inp = _make_input("Make it better.", mode=CoachMode.OFF)
        result = select_action(inp)
        assert result.action == Action.NONE


# ── Sensitive domain suppression ───────────────────────────────────────────

class TestSensitiveSuppression:
    def test_sensitive_zh(self):
        inp = _make_input("我最近一直在想一些不好的念头")
        result = select_action(inp)
        assert result.action == Action.NONE
        assert "sensitive" in (result.suppressed_reason or "")

    def test_sensitive_en(self):
        inp = _make_input("My doctor said I might have cancer")
        result = select_action(inp)
        assert result.action == Action.NONE


# ── Budget enforcement ──────────────────────────────────────────────────────

class TestStrictMode:
    """Strict mode raises the 7-day budgets so high-volume users aren't
    silenced mid-week, and leaves every other gate (dismissal, sensitive,
    urgent, cooldown, observation period) identical to standard."""

    def test_strict_proactive_budget_higher_than_standard(self):
        """proactive_count_7d=5 exhausts standard (cap=4) but not strict (cap=10)."""
        inp_standard = _make_input("帮我写一个接口文档", mode=CoachMode.STANDARD, proactive_7d=5)
        inp_strict = _make_input("帮我写一个接口文档", mode=CoachMode.STRICT, proactive_7d=5)
        assert select_action(inp_standard).action == Action.SILENT_REWRITE
        assert select_action(inp_strict).action in (
            Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE,
        )

    def test_strict_budget_does_eventually_exhaust(self):
        """strict budget is 10/7d, so proactive_count_7d=10 does silence it."""
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.STRICT, proactive_7d=10)
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert "budget" in (result.suppressed_reason or "")

    def test_strict_still_respects_dismissal(self):
        """coach dismiss overrides strict too — user's explicit request wins."""
        inp = _make_input(
            "帮我写一个接口文档", mode=CoachMode.STRICT, user_dismissed=True,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert result.suppressed_reason == "user_dismissed"

    def test_strict_still_respects_sensitive(self):
        """Sensitive domain suppresses strict too."""
        inp = _make_input("我最近一直在想一些不好的念头", mode=CoachMode.STRICT)
        result = select_action(inp)
        assert result.action == Action.NONE


class TestBudget:
    def test_budget_exhausted_light(self):
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.LIGHT, proactive_7d=2)
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert "budget" in (result.suppressed_reason or "")

    def test_budget_not_exhausted_standard(self):
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.STANDARD, proactive_7d=3)
        result = select_action(inp)
        assert result.action in (Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE)

    def test_budget_exhausted_standard(self):
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.STANDARD, proactive_7d=4)
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE


# ── Urgent task handling ────────────────────────────────────────────────────

class TestUrgentTask:
    def test_urgent_never_preempts_with_pre_nudge(self):
        """Urgent task in standard mode must only get POST tip, never PRE nudge.

        Uses English 'urgent' because Chinese '紧急' is in sensitive keywords
        and short-circuits to Action.NONE via the sensitive branch.
        """
        # Overloaded + urgent English prompt
        text = "Urgent: refactor this messy code, add tests, write docs, then deploy"
        inp = _make_input(text, mode=CoachMode.STANDARD)
        assert inp.detection.is_urgent, f"test setup: expected urgent, got {inp.detection}"
        assert not inp.detection.is_sensitive, "test setup: should not be sensitive"
        result = select_action(inp)
        # Budget not exhausted → should be POST, not PRE
        assert result.action in (Action.POST_ANSWER_TIP, Action.SILENT_REWRITE)
        assert result.action != Action.PRE_ANSWER_MICRO_NUDGE, (
            "urgent task should never pre-empt the answer"
        )

    def test_urgent_budget_exhausted_silent(self):
        text = "Urgent: refactor this messy code, add tests, write docs, then deploy"
        inp = _make_input(
            text, mode=CoachMode.STANDARD, proactive_7d=4,
        )
        assert inp.detection.is_urgent
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE


# ── User dismissed ─────────────────────────────────────────────────────────

class TestUserDismissed:
    def test_dismissed_downgrades_to_silent_rewrite(self):
        inp = _make_input("帮我写一个接口文档", user_dismissed=True)
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert "dismissed" in (result.suppressed_reason or "")

    def test_dismissed_blocks_retrospective_on_clean_prompt(self):
        """Bug regression: when user is dismissed AND prompt is clean,
        policy must NOT emit retrospective_reminder even if pattern qualifies."""
        pat = PatternSummary(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            status=PatternStatus.ACTIVE,
            evidence_count=6, distinct_sessions=4, cost_count=3, score=3.0,
            last_notified_at=None,
        )
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("Convert this JSON to CSV format"),  # clean
            memory_enabled=True,
            observation_period_ends_at=datetime.now(timezone.utc) - timedelta(days=20),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=True,  # dismissed
            top_pattern=pat,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert result.suppressed_reason == "user_dismissed"


# ── Action selection logic ─────────────────────────────────────────────────

class TestActionSelection:
    def test_weak_prompt_gets_post_tip_light(self):
        inp = _make_input("帮我写一个接口文档", mode=CoachMode.LIGHT)
        result = select_action(inp)
        assert result.action == Action.POST_ANSWER_TIP
        assert result.issue_type == IssueType.MISSING_OUTPUT_CONTRACT

    def test_high_severity_standard_gets_pre_nudge(self):
        """In standard mode with high severity, should get pre-answer nudge."""
        inp = _make_input("看看这个", mode=CoachMode.STANDARD)
        result = select_action(inp)
        # missing_goal has severity=0.80, confidence=0.95 → pre_answer_micro_nudge
        assert result.action in (Action.PRE_ANSWER_MICRO_NUDGE, Action.POST_ANSWER_TIP)

    def test_good_prompt_gets_silent_rewrite(self):
        inp = _make_input("请把这段文字翻译成英文：今天天气很好")
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert result.issue_type is None

    def test_overloaded_correctly_identified(self):
        inp = _make_input("分析系统然后给重构方案、代码、测试、上线步骤")
        result = select_action(inp)
        assert result.issue_type == IssueType.OVERLOADED_REQUEST


# ── Retrospective emission ─────────────────────────────────────────────────

class TestRetrospective:
    def _good_pattern(self) -> PatternSummary:
        return PatternSummary(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            status=PatternStatus.ACTIVE,
            evidence_count=5,
            distinct_sessions=4,
            cost_count=3,
            score=4.5,
            last_notified_at=None,
        )

    def test_should_emit_when_all_conditions_met(self):
        pat = self._good_pattern()
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写接口"),
            memory_enabled=True,
            observation_period_ends_at=None,  # period over
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert ok, f"Expected ok=True, got reason={reason}"

    def test_blocked_during_observation_period(self):
        pat = self._good_pattern()
        future = datetime.now(timezone.utc) + timedelta(days=7)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写接口"),
            memory_enabled=True,
            observation_period_ends_at=future,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert not ok
        assert "observation_period" in reason

    def test_blocked_by_cooldown(self):
        pat = self._good_pattern()
        recent_notify = datetime.now(timezone.utc) - timedelta(days=5)  # < 14 days
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写接口"),
            memory_enabled=True,
            observation_period_ends_at=None,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=recent_notify,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert not ok
        assert "cooldown" in reason

    def test_blocked_when_evidence_insufficient(self):
        pat = PatternSummary(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            status=PatternStatus.ACTIVE,
            evidence_count=2,  # < 4
            distinct_sessions=2,  # < 3
            cost_count=1,  # < 2
            score=1.5,
            last_notified_at=None,
        )
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写接口"),
            memory_enabled=True,
            observation_period_ends_at=None,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert not ok

    def test_blocked_when_mode_off(self):
        pat = self._good_pattern()
        inp = PolicyInput(
            mode=CoachMode.OFF,
            detection=detect("帮我写接口"),
            memory_enabled=True,
            observation_period_ends_at=None,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert not ok
        assert reason == "mode=off"

    def test_one_coaching_max_per_turn(self):
        """Policy invariant: select_action returns exactly ONE action per turn.

        Even when BOTH a per-turn issue AND a qualifying pattern exist, we
        must pick only one surface (per-turn takes precedence) — never two
        visible coachings in a single turn.
        """
        # Weak prompt (per-turn missing_output_contract) + qualifying
        # pattern (missing_goal retrospective-ready)
        pat = PatternSummary(
            issue_type=IssueType.MISSING_GOAL,
            status=PatternStatus.ACTIVE,
            evidence_count=8,
            distinct_sessions=5,
            cost_count=4,
            score=5.0,
            last_notified_at=None,
        )
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写一个接口文档"),  # triggers missing_output_contract
            memory_enabled=True,
            observation_period_ends_at=datetime.now(timezone.utc) - timedelta(days=20),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        result = select_action(inp)
        # Per-turn issue wins; retrospective is skipped for this turn.
        assert result.action in (
            Action.POST_ANSWER_TIP,
            Action.PRE_ANSWER_MICRO_NUDGE,
        )
        # And the action must NOT be retrospective_reminder when per-turn candidate exists
        assert result.action != Action.RETROSPECTIVE_REMINDER

    def test_retrospective_fires_for_formerly_shadow_type(self):
        """Post-④: formerly-shadow types (missing_context etc.) are
        first-class visible issues — retrospective reminders fire for
        them when all other conditions are met."""
        pat = PatternSummary(
            issue_type=IssueType.MISSING_CONTEXT,
            status=PatternStatus.ACTIVE,
            evidence_count=8,
            distinct_sessions=5,
            cost_count=4,
            score=5.0,
            last_notified_at=None,
        )
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("Convert this JSON to CSV format"),  # clean
            memory_enabled=True,
            observation_period_ends_at=None,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        ok, reason = should_emit_retrospective(inp)
        assert ok, f"expected retrospective to fire, got reason={reason}"


class TestSessionPatternNudge:
    """Short-term within-session pattern → SESSION_PATTERN_NUDGE action."""

    def _sp(self, issue=IssueType.MISSING_GOAL, count=3, window=3):
        return SessionPatternSummary(
            issue_type=issue, count=count, window_size=window,
        )

    def test_fires_when_per_turn_clean_and_session_pattern_present(self):
        """Clean current prompt + session pattern → SESSION_PATTERN_NUDGE."""
        inp = _make_input(
            "Convert this JSON to CSV format",  # clean
            session_pattern=self._sp(),
        )
        result = select_action(inp)
        assert result.action == Action.SESSION_PATTERN_NUDGE
        assert result.issue_type == IssueType.MISSING_GOAL

    def test_suppressed_if_already_nudged_this_session(self):
        inp = _make_input(
            "Convert this JSON to CSV format",
            session_pattern=self._sp(),
            session_already_nudged=True,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE

    def test_suppressed_on_dismissal(self):
        inp = _make_input(
            "Convert this JSON to CSV format",
            session_pattern=self._sp(),
            user_dismissed=True,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE
        assert result.suppressed_reason == "user_dismissed"

    def test_per_turn_wins_over_session_nudge(self):
        """When the current prompt has its own issue, per-turn coaching
        wins. Session nudge never fires same turn (1-per-turn invariant)."""
        inp = _make_input(
            "帮我写一个接口文档",  # triggers missing_output_contract
            session_pattern=self._sp(),  # also has session pattern
        )
        result = select_action(inp)
        assert result.action == Action.POST_ANSWER_TIP
        assert result.issue_type == IssueType.MISSING_OUTPUT_CONTRACT

    def test_requires_memory_enabled(self):
        """Session nudge requires memory_enabled (session_turns is memory data)."""
        inp = _make_input(
            "Convert this JSON to CSV format",
            session_pattern=self._sp(),
        )
        # Flip memory off on the input
        inp.memory_enabled = False
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE

    def test_urgency_blocks_session_nudge(self):
        """Urgent context should not get session-level reminder interrupts."""
        inp = _make_input(
            "urgent please help",
            session_pattern=self._sp(),
        )
        # This detection might not flag urgency; force it via assertion
        # that action is NOT session_pattern_nudge when is_urgent is True.
        # Construct manually:
        from tools.detectors import detect
        det = detect("urgent please help")
        if det.is_urgent:
            inp2 = PolicyInput(
                mode=CoachMode.LIGHT, detection=det, memory_enabled=True,
                observation_period_ends_at=None,
                proactive_count_7d=0, retrospective_count_7d=0,
                last_notified_pattern=None, last_notified_at=None,
                user_dismissed_recently=False, top_pattern=None,
                session_pattern=self._sp(), session_already_nudged=False,
            )
            result = select_action(inp2)
            assert result.action != Action.SESSION_PATTERN_NUDGE


class TestSelectActionRetrospective:
    """Verify select_action returns RETROSPECTIVE_REMINDER when appropriate."""

    def _good_pattern(self) -> PatternSummary:
        return PatternSummary(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            status=PatternStatus.ACTIVE,
            evidence_count=6,
            distinct_sessions=4,
            cost_count=3,
            score=3.0,
            last_notified_at=None,
        )

    def test_returns_retrospective_when_no_per_turn_issue_but_pattern_qualifies(self):
        """When current prompt is clean but top_pattern qualifies, return RETROSPECTIVE_REMINDER."""
        pat = self._good_pattern()
        # Clean prompt — no per-turn issue detected
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("Convert this JSON to CSV format"),
            memory_enabled=True,
            observation_period_ends_at=datetime.now(timezone.utc) - timedelta(days=20),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        result = select_action(inp)
        assert result.action == Action.RETROSPECTIVE_REMINDER
        assert result.issue_type == IssueType.MISSING_OUTPUT_CONTRACT

    def test_falls_through_to_silent_rewrite_when_retrospective_not_eligible(self):
        """When no per-turn issue and no qualifying pattern, return SILENT_REWRITE."""
        # Pattern present but in observation period → retrospective blocked
        pat = self._good_pattern()
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("Convert this JSON to CSV format"),
            memory_enabled=True,
            observation_period_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE

    def test_memory_off_never_returns_retrospective(self):
        """If memory_enabled=False, retrospective path is skipped."""
        pat = self._good_pattern()
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("Convert this JSON to CSV format"),
            memory_enabled=False,
            observation_period_ends_at=datetime.now(timezone.utc) - timedelta(days=20),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        result = select_action(inp)
        assert result.action == Action.SILENT_REWRITE

    def test_per_turn_issue_takes_precedence_over_retrospective(self):
        """When current prompt has its own issue, return per-turn tip, not retrospective."""
        pat = self._good_pattern()
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=detect("帮我写一个接口文档"),  # has its own issue
            memory_enabled=True,
            observation_period_ends_at=datetime.now(timezone.utc) - timedelta(days=20),
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pat,
        )
        result = select_action(inp)
        assert result.action in (Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE)
        assert result.action != Action.RETROSPECTIVE_REMINDER
