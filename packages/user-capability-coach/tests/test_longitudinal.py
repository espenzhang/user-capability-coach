"""Longitudinal integration tests using fixtures/longitudinal_sessions.jsonl.

These tests simulate multi-session pattern accumulation and verify that
retrospective reminder conditions (thresholds, cooldown, observation period)
are enforced correctly.
"""
import json
import pytest
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import memory
from tools.taxonomy import (
    IssueType, Domain, Action, CostSignal, PatternStatus, CoachMode
)
from tools.policy import (
    PolicyInput, PolicyConfig, PatternSummary, should_emit_retrospective,
    DEFAULT_CONFIG,
)
from tools.detectors import DetectionOutput


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "longitudinal_sessions.jsonl"


@pytest.fixture
def profile(tmp_path):
    profile_dir = str(tmp_path)
    memory.init_db(profile=profile_dir)
    return profile_dir


def _no_detection() -> DetectionOutput:
    return DetectionOutput(
        domain=Domain.CODING,
        is_sensitive=False,
        task_complexity=0.3,
        is_urgent=False,
        candidates=[],
    )


def _dict_to_pattern_summary(p: dict) -> PatternSummary:
    """Convert a memory.get_top_pattern() dict into a PolicyInput-compatible PatternSummary."""
    last_notified = None
    if p.get("last_notified_at"):
        last_notified = datetime.fromisoformat(p["last_notified_at"])
    return PatternSummary(
        issue_type=IssueType(p["issue_type"]),
        status=PatternStatus(p["status"]),
        evidence_count=p["evidence_count"],
        distinct_sessions=p["distinct_sessions"],
        cost_count=p["cost_count"],
        score=p["score"],
        last_notified_at=last_notified,
    )


def _load_fixtures_for_user(user_id: str) -> list[dict]:
    rows = []
    with FIXTURES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("user_id") == user_id:
                rows.append(row)
    return rows


def _simulate_sessions(profile: str, fixtures: list[dict]) -> None:
    """Write observations from fixture rows into the test profile."""
    for row in fixtures:
        issue_type = IssueType(row["issue_type"])
        domain = Domain(row.get("domain", "other"))
        action = Action(row.get("action_taken", "post_answer_tip"))
        cost_signal = CostSignal.OUTPUT_FORMAT_MISMATCH if row.get("cost_signal") else CostSignal.NONE
        memory.record_observation(
            session_id=row["session_id"],
            domain=domain,
            issue_type=issue_type,
            action_taken=action,
            severity=0.65,
            confidence=0.80,
            fixability=0.90,
            cost_signal=cost_signal,
            evidence_summary=f"{domain.value} task with {issue_type.value}",
            profile=profile,
        )


class TestPatternAccumulation:
    def test_u001_accumulates_missing_output_contract(self, profile):
        """u001 has 4 observations across 4 sessions → pattern should be active.

        Note: patterns are stored per (issue_type, domain). u001's fixtures span
        coding (1 obs) and writing (3 obs). The top active pattern is 'writing'
        with 3 evidence.
        """
        fixtures = _load_fixtures_for_user("u001")
        assert len(fixtures) == 4
        _simulate_sessions(profile, fixtures)

        # Check all patterns for the issue type
        all_patterns = memory.get_all_patterns(profile=profile)
        issue_types = {p["issue_type"] for p in all_patterns}
        assert IssueType.MISSING_OUTPUT_CONTRACT.value in issue_types

        # Verify total evidence across all domains for this issue type
        total_evidence = sum(
            p["evidence_count"] for p in all_patterns
            if p["issue_type"] == IssueType.MISSING_OUTPUT_CONTRACT.value
        )
        assert total_evidence == 4  # 1 coding + 3 writing

        # Top pattern should be the writing one (highest score = most evidence)
        top = memory.get_top_pattern(profile=profile)
        assert top is not None
        assert top["issue_type"] == IssueType.MISSING_OUTPUT_CONTRACT.value
        assert top["status"] == PatternStatus.ACTIVE.value

    def test_u002_accumulates_overloaded(self, profile):
        """u002 has 4 overloaded_request observations across 4 sessions."""
        fixtures = _load_fixtures_for_user("u002")
        _simulate_sessions(profile, fixtures)

        pattern = memory.get_top_pattern(profile=profile)
        assert pattern is not None
        assert pattern["issue_type"] == IssueType.OVERLOADED_REQUEST.value
        assert pattern["evidence_count"] == 4
        assert pattern["distinct_sessions"] == 4

    def test_u003_accumulates_missing_goal(self, profile):
        """u003 has 4 missing_goal observations across 4 sessions."""
        fixtures = _load_fixtures_for_user("u003")
        _simulate_sessions(profile, fixtures)

        pattern = memory.get_top_pattern(profile=profile)
        assert pattern is not None
        assert pattern["issue_type"] == IssueType.MISSING_GOAL.value

    def test_all_patterns_listed(self, profile):
        """get_all_patterns returns all stored patterns."""
        # Write two different issue types across separate sessions
        for i in range(2):
            memory.record_observation(
                session_id=f"all_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
            memory.record_observation(
                session_id=f"all_sess_over_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.OVERLOADED_REQUEST,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )

        all_patterns = memory.get_all_patterns(profile=profile)
        issue_types = {p["issue_type"] for p in all_patterns}
        assert "missing_output_contract" in issue_types
        assert "overloaded_request" in issue_types

    def test_idempotency_same_session(self, profile):
        """Same session + issue_type should not create duplicate observations."""
        obs1 = memory.record_observation(
            session_id="dup_sess",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        obs2 = memory.record_observation(
            session_id="dup_sess",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        # Same observation ID returned (idempotent write)
        assert obs1 == obs2

        pattern = memory.get_top_pattern(profile=profile)
        if pattern:
            assert pattern["evidence_count"] == 1


class TestObservationPeriodEnforcement:
    def test_no_retro_during_observation_period(self, profile):
        """Retrospective must be suppressed during 14-day observation period."""
        fixtures = _load_fixtures_for_user("u001")
        _simulate_sessions(profile, fixtures)

        pattern_raw = memory.get_top_pattern(profile=profile)
        assert pattern_raw is not None
        pattern = _dict_to_pattern_summary(pattern_raw)

        obs_ends = datetime.now(timezone.utc) + timedelta(days=7)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=_no_detection(),
            memory_enabled=True,
            observation_period_ends_at=obs_ends,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pattern,
        )
        ok, reason = should_emit_retrospective(inp, DEFAULT_CONFIG)
        assert not ok
        assert "observation_period" in reason

    def test_retro_allowed_after_observation_period(self, profile):
        """Retrospective allowed when observation period has passed and conditions met."""
        for i in range(4):
            memory.record_observation(
                session_id=f"sess_retro_{i:03d}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.65,
                confidence=0.80,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                evidence_summary="coding task lacked output format",
                profile=profile,
            )

        pattern_raw = memory.get_top_pattern(profile=profile)
        assert pattern_raw is not None
        pattern = _dict_to_pattern_summary(pattern_raw)

        obs_ended = datetime.now(timezone.utc) - timedelta(days=15)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=_no_detection(),
            memory_enabled=True,
            observation_period_ends_at=obs_ended,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pattern,
        )
        ok, reason = should_emit_retrospective(inp, DEFAULT_CONFIG)
        assert ok, f"Should emit retrospective but got: {reason}"


class TestCooldownEnforcement:
    def _make_pattern(self, last_notified_at=None) -> PatternSummary:
        return PatternSummary(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            status=PatternStatus.ACTIVE,
            evidence_count=6,
            distinct_sessions=4,
            cost_count=3,
            score=3.0,
            last_notified_at=last_notified_at,
        )

    def test_cooldown_blocks_repeat_reminder(self, profile):
        """A pattern reminded < 14 days ago should not trigger again."""
        recent_notify = datetime.now(timezone.utc) - timedelta(days=5)
        pattern = self._make_pattern(last_notified_at=recent_notify)

        obs_ended = datetime.now(timezone.utc) - timedelta(days=20)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=_no_detection(),
            memory_enabled=True,
            observation_period_ends_at=obs_ended,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern="missing_output_contract",
            last_notified_at=recent_notify,
            user_dismissed_recently=False,
            top_pattern=pattern,
        )
        ok, reason = should_emit_retrospective(inp, DEFAULT_CONFIG)
        assert not ok
        assert "cooldown" in reason

    def test_cooldown_clears_after_14_days(self, profile):
        """A pattern reminded > 14 days ago should be eligible again."""
        old_notify = datetime.now(timezone.utc) - timedelta(days=15)
        pattern = self._make_pattern(last_notified_at=old_notify)

        obs_ended = datetime.now(timezone.utc) - timedelta(days=20)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=_no_detection(),
            memory_enabled=True,
            observation_period_ends_at=obs_ended,
            proactive_count_7d=0,
            retrospective_count_7d=0,
            last_notified_pattern="missing_output_contract",
            last_notified_at=old_notify,
            user_dismissed_recently=False,
            top_pattern=pattern,
        )
        ok, reason = should_emit_retrospective(inp, DEFAULT_CONFIG)
        assert ok, f"Should emit after cooldown but got: {reason}"


class TestScoreDecay:
    def test_weekly_decay_reduces_score(self, profile):
        """apply_weekly_decay with old last_seen_at should decay the score."""
        import sqlite3

        memory.record_observation(
            session_id="decay_sess_001",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            severity=0.65,
            confidence=0.80,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            profile=profile,
        )

        # Backdate last_seen_at to 7 days ago so decay actually applies
        db_path = str(memory._db_path(profile=profile))
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with sqlite3.connect(db_path) as con:
            con.execute("UPDATE patterns SET last_seen_at=?", (seven_days_ago,))

        pattern_before = memory.get_top_pattern(profile=profile)
        assert pattern_before is not None
        score_before = pattern_before["score"]

        memory.apply_weekly_decay(profile=profile)

        pattern_after = memory.get_top_pattern(profile=profile)
        assert pattern_after is not None
        score_after = pattern_after["score"]

        assert score_after < score_before
        # ~0.85 × score_before (within tolerance for floating point and timing)
        assert abs(score_after - score_before * 0.85) < 0.05

    def test_pattern_enters_cooling_on_low_score(self, profile):
        """A pattern with score < 0.5 enters cooling after decay."""
        import sqlite3

        memory.record_observation(
            session_id="cooling_sess",
            domain=Domain.WRITING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.SILENT_REWRITE,
            severity=0.3,
            confidence=0.5,
            profile=profile,
        )

        # Directly set score to below cooling threshold and backdate last_seen_at
        db_path = str(memory._db_path(profile=profile))
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        with sqlite3.connect(db_path) as con:
            # Set score very low and last_seen to 8 days ago — decay will push it below 0.5
            con.execute("UPDATE patterns SET score=0.4, last_seen_at=?", (old_ts,))

        memory.apply_weekly_decay(profile=profile)

        all_patterns = memory.get_all_patterns(profile=profile)
        assert all_patterns, "Pattern should still exist (just in cooling)"
        assert all_patterns[0]["status"] in (
            PatternStatus.COOLING.value,
            PatternStatus.RESOLVED.value,
            PatternStatus.ARCHIVED.value,
        )


class TestWeeklyBudget:
    def test_budget_exhausted_blocks_retro(self, profile):
        """When weekly retrospective budget is exhausted, no more retros."""
        pattern = PatternSummary(
            issue_type=IssueType.OVERLOADED_REQUEST,
            status=PatternStatus.ACTIVE,
            evidence_count=5,
            distinct_sessions=4,
            cost_count=3,
            score=3.0,
            last_notified_at=None,
        )

        obs_ended = datetime.now(timezone.utc) - timedelta(days=20)
        inp = PolicyInput(
            mode=CoachMode.LIGHT,
            detection=_no_detection(),
            memory_enabled=True,
            observation_period_ends_at=obs_ended,
            proactive_count_7d=0,
            retrospective_count_7d=1,  # budget=1, already used
            last_notified_pattern=None,
            last_notified_at=None,
            user_dismissed_recently=False,
            top_pattern=pattern,
        )
        ok, reason = should_emit_retrospective(inp, DEFAULT_CONFIG)
        assert not ok
        assert "budget" in reason


class TestStatusReactivation:
    """Verify resolved/archived pattern status handling on new observations."""

    def test_resolved_pattern_reactivates_on_new_observation(self, profile):
        """A resolved pattern returns to active when new observations come in."""
        import sqlite3

        # Create a resolved pattern via direct SQL
        memory.record_observation(
            session_id="original_sess",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        db_path = str(memory._db_path(profile=profile))
        old_ts = (datetime.now(timezone.utc) - timedelta(days=65)).isoformat()
        with sqlite3.connect(db_path) as con:
            con.execute(
                "UPDATE patterns SET status='resolved', resolved_at=?, last_seen_at=?",
                (old_ts, old_ts),
            )

        # New observation should reactivate
        memory.record_observation(
            session_id="new_sess",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )

        patterns = memory.get_all_patterns(profile=profile)
        assert len(patterns) == 1
        assert patterns[0]["status"] == PatternStatus.ACTIVE.value
        assert patterns[0]["resolved_at"] is None

    def test_archived_pattern_stays_archived(self, profile):
        """An archived pattern does NOT reactivate on a single new observation."""
        import sqlite3

        memory.record_observation(
            session_id="arch_sess",
            domain=Domain.WRITING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        db_path = str(memory._db_path(profile=profile))
        ancient = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with sqlite3.connect(db_path) as con:
            con.execute(
                "UPDATE patterns SET status='archived', resolved_at=?, last_seen_at=?",
                (ancient, ancient),
            )

        # New observation — should stay archived
        memory.record_observation(
            session_id="reactivate_attempt",
            domain=Domain.WRITING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )

        patterns = memory.get_all_patterns(profile=profile)
        assert len(patterns) == 1
        assert patterns[0]["status"] == PatternStatus.ARCHIVED.value
