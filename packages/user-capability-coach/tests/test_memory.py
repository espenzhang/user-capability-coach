"""Tests for memory.py — SQLite persistence layer."""
import pytest
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta  # noqa: F401 — used in some tests

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import memory
from tools.taxonomy import IssueType, Domain, Action, CostSignal, PatternStatus


@pytest.fixture
def profile(tmp_path):
    """Each test gets a fresh isolated database."""
    profile_dir = str(tmp_path)
    memory.init_db(profile=profile_dir)
    return profile_dir


# ── Observation writes ─────────────────────────────────────────────────────

class TestRecordObservation:
    def test_basic_write(self, profile):
        obs_id = memory.record_observation(
            session_id="sess_001",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            confidence=0.8,
            severity=0.7,
            fixability=0.9,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            evidence_summary="coding task lacked output format",
            profile=profile,
        )
        assert obs_id is not None
        assert len(obs_id) > 0

    def test_idempotent_same_session_same_issue(self, profile):
        """Same session + issue_type should return same id."""
        id1 = memory.record_observation(
            session_id="sess_idempotent",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        id2 = memory.record_observation(
            session_id="sess_idempotent",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.SILENT_REWRITE,
            profile=profile,
        )
        assert id1 == id2

    def test_sensitive_domain_strips_content(self, profile):
        obs_id = memory.record_observation(
            session_id="sess_sensitive",
            domain=Domain.SENSITIVE,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,  # should be stripped
            action_taken=Action.NONE,
            evidence_summary="should not be stored",
            profile=profile,
        )
        # Verify in DB: no issue_type, no evidence_summary
        import sqlite3
        db_path = str(Path(profile) / "coach.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        con.close()
        assert row["issue_type"] is None
        assert row["evidence_summary"] == ""
        assert row["shadow_only"] == 1

    def test_session_turns_accumulate_per_turn(self, profile):
        """Every record_observation call creates a session_turn, even when
        the observation itself is idempotently deduped."""
        for _ in range(3):
            memory.record_observation(
                session_id="sesA",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_GOAL,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.7, confidence=0.8,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                profile=profile,
            )
        # observations table: dedup → 1 row
        import sqlite3
        db = str(Path(profile) / "coach.db")
        con = sqlite3.connect(db)
        obs_n = con.execute(
            "SELECT COUNT(*) FROM observations WHERE session_id='sesA'"
        ).fetchone()[0]
        turn_n = con.execute(
            "SELECT COUNT(*) FROM session_turns WHERE session_id='sesA'"
        ).fetchone()[0]
        max_turn = con.execute(
            "SELECT MAX(turn_index) FROM session_turns WHERE session_id='sesA'"
        ).fetchone()[0]
        con.close()
        assert obs_n == 1, "observations dedup by (session, issue_type)"
        assert turn_n == 3, "session_turns should capture each call"
        assert max_turn == 3

    def test_get_session_pattern_fires_at_threshold(self, profile):
        """3 of 5 turns with same issue_type → pattern detected."""
        for i in range(3):
            memory.record_observation(
                session_id="threshold",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.7, confidence=0.8,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                profile=profile,
            )
        p = memory.get_session_pattern("threshold", profile=profile)
        assert p is not None
        assert p["issue_type"] == IssueType.MISSING_OUTPUT_CONTRACT.value
        assert p["count"] == 3

    def test_get_session_pattern_below_threshold(self, profile):
        """Only 2 turns with same issue → no pattern."""
        for i in range(2):
            memory.record_observation(
                session_id="low",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_GOAL,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
        assert memory.get_session_pattern("low", profile=profile) is None

    def test_get_session_pattern_ignores_other_sessions(self, profile):
        """Pattern in sesA should not leak into sesB's pattern."""
        for _ in range(3):
            memory.record_observation(
                session_id="sesA",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_GOAL,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
        assert memory.get_session_pattern("sesB", profile=profile) is None

    def test_get_session_pattern_ignores_shadow_only(self, profile):
        """Shadow observations should not count toward session pattern."""
        for _ in range(3):
            memory.record_observation(
                session_id="shadow_sess",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_GOAL,
                action_taken=Action.SILENT_REWRITE,
                shadow_only=True,
                profile=profile,
            )
        assert memory.get_session_pattern("shadow_sess", profile=profile) is None

    def test_get_session_pattern_works_for_all_visible_types(self, profile):
        """Post-④: every IssueType is visible, so session pattern should
        fire for formerly-shadow types too (e.g. missing_context)."""
        for _ in range(3):
            memory.record_observation(
                session_id="promoted",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_CONTEXT,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
        p = memory.get_session_pattern("promoted", profile=profile)
        assert p is not None
        assert p["issue_type"] == IssueType.MISSING_CONTEXT.value

    def test_session_nudge_log_is_idempotent(self, profile):
        """mark_session_nudged twice for same pair is a no-op."""
        memory.mark_session_nudged(
            "once", IssueType.MISSING_GOAL, profile=profile,
        )
        assert memory.has_session_been_nudged(
            "once", IssueType.MISSING_GOAL, profile=profile,
        )
        # Marking again should not error
        memory.mark_session_nudged(
            "once", IssueType.MISSING_GOAL, profile=profile,
        )

    def test_prune_old_observations_only_cold_patterns(self, profile):
        """prune_old_observations removes old rows with resolved/archived
        pattern status; keeps active-pattern observations untouched."""
        import sqlite3
        from datetime import datetime, timezone, timedelta

        # Write an old observation with an active pattern
        memory.record_observation(
            session_id="active", domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            profile=profile,
        )
        # Write an old observation whose pattern we'll mark archived
        memory.record_observation(
            session_id="archived", domain=Domain.CODING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )

        db = str(Path(profile) / "coach.db")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        with sqlite3.connect(db) as con:
            con.execute("UPDATE observations SET ts=?", (old_ts,))
            # Only mark the missing_goal pattern archived
            con.execute(
                "UPDATE patterns SET status='archived' WHERE issue_type=?",
                (IssueType.MISSING_GOAL.value,),
            )

        deleted = memory.prune_old_observations(days=180, profile=profile)
        assert deleted >= 1

        # Active-pattern observation preserved
        with sqlite3.connect(db) as con:
            survivors = con.execute(
                "SELECT issue_type FROM observations"
            ).fetchall()
        surviving_types = {r[0] for r in survivors}
        assert IssueType.MISSING_OUTPUT_CONTRACT.value in surviving_types, (
            "active-pattern observation must be preserved"
        )
        assert IssueType.MISSING_GOAL.value not in surviving_types, (
            "archived-pattern observation should be pruned"
        )

    def test_prune_old_session_turns(self, profile):
        """prune_old_session_turns removes rows older than threshold."""
        import sqlite3
        from datetime import datetime, timezone, timedelta
        memory.record_observation(
            session_id="old",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        db = str(Path(profile) / "coach.db")
        # Backdate the turn
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with sqlite3.connect(db) as con:
            con.execute("UPDATE session_turns SET ts=?", (old_ts,))

        deleted = memory.prune_old_session_turns(days=7, profile=profile)
        assert deleted >= 1

    def test_concurrent_writes_no_lost_updates(self, profile):
        """Concurrent record_observation calls must not lose pattern updates
        to read-compute-write races. 10 threads each write a unique session
        → pattern.evidence_count must be 10, not less."""
        import concurrent.futures

        def _write(i: int) -> None:
            memory.record_observation(
                session_id=f"concurrent_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.7, confidence=0.8,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                profile=profile,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_write, i) for i in range(10)]
            # Propagate any exceptions (e.g. UNIQUE-constraint races)
            for f in futures:
                f.result()

        pat = memory.get_top_pattern(profile=profile)
        assert pat is not None
        assert pat["evidence_count"] == 10, (
            f"lost-update race: expected evidence=10, got {pat['evidence_count']}"
        )
        assert pat["distinct_sessions"] == 10

    def test_top_pattern_returns_highest_score(self, profile):
        """Post-④: all IssueTypes are visible, so get_top_pattern simply
        returns the highest-scoring active pattern across all types."""
        # Seed a higher-score missing_context pattern across sessions
        for i in range(6):
            memory.record_observation(
                session_id=f"ctx_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_CONTEXT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.9, confidence=0.9,
                cost_signal=CostSignal.HIGH_RISK_GUESS,
                profile=profile,
            )
        # Lower-score missing_output_contract
        for i in range(3):
            memory.record_observation(
                session_id=f"moc_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.65, confidence=0.80,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                profile=profile,
            )

        top = memory.get_top_pattern(profile=profile)
        assert top is not None
        # Higher-score pattern (missing_context) wins regardless of type —
        # ④ made all types first-class
        assert top["issue_type"] == IssueType.MISSING_CONTEXT.value

    def test_sensitive_domain_keeps_content_when_opted_in(self, profile):
        """sensitive_logging_enabled=True keeps content — user opted in."""
        obs_id = memory.record_observation(
            session_id="sess_sensitive_optin",
            domain=Domain.SENSITIVE,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.SILENT_REWRITE,
            evidence_summary="sensitive topic but user opted into logging",
            profile=profile,
            sensitive_logging_enabled=True,
        )
        import sqlite3
        db_path = str(Path(profile) / "coach.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        con.close()
        # User opted in → issue_type and evidence retained
        assert row["issue_type"] == "missing_output_contract"
        assert "opted into" in row["evidence_summary"]


# ── Pattern accumulation ───────────────────────────────────────────────────

class TestPatternAccumulation:
    def test_pattern_created_after_observation(self, profile):
        memory.record_observation(
            session_id="sess_A",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            profile=profile,
        )
        patterns = memory.get_all_patterns(profile=profile)
        assert len(patterns) >= 1
        p = patterns[0]
        assert p["issue_type"] == IssueType.MISSING_OUTPUT_CONTRACT.value
        assert p["evidence_count"] >= 1

    def test_cost_count_increments(self, profile):
        for i in range(3):
            memory.record_observation(
                session_id=f"sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.OVERLOADED_REQUEST,
                action_taken=Action.POST_ANSWER_TIP,
                cost_signal=CostSignal.REWORK_REQUIRED,
                profile=profile,
            )
        patterns = memory.get_all_patterns(profile=profile)
        p = next(x for x in patterns if x["issue_type"] == IssueType.OVERLOADED_REQUEST.value)
        assert p["cost_count"] == 3
        assert p["distinct_sessions"] == 3

    def test_distinct_sessions_counted_correctly(self, profile):
        # Same session should not double-count sessions
        for _ in range(3):
            memory.record_observation(
                session_id="same_session",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_GOAL,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
        patterns = memory.get_all_patterns(profile=profile)
        p = next(x for x in patterns if x["issue_type"] == IssueType.MISSING_GOAL.value)
        assert p["distinct_sessions"] == 1  # only 1 unique session


# ── Forget operations ──────────────────────────────────────────────────────

class TestForget:
    def test_forget_pattern_also_clears_intervention_events(self, profile):
        """Per plan: forget-pattern must also clear related intervention_events
        so budget counts and why-reminded state stay consistent."""
        memory.record_observation(
            session_id="s1", domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            profile=profile,
        )
        memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="post_answer_tip",
            shown=True,
            profile=profile,
        )
        # Verify event exists
        import sqlite3
        db = str(Path(profile) / "coach.db")
        con = sqlite3.connect(db)
        n_before = con.execute(
            "SELECT COUNT(*) FROM intervention_events WHERE issue_type=?",
            (IssueType.MISSING_OUTPUT_CONTRACT.value,),
        ).fetchone()[0]
        con.close()
        assert n_before >= 1

        memory.forget_pattern(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)

        con = sqlite3.connect(db)
        n_after = con.execute(
            "SELECT COUNT(*) FROM intervention_events WHERE issue_type=?",
            (IssueType.MISSING_OUTPUT_CONTRACT.value,),
        ).fetchone()[0]
        con.close()
        assert n_after == 0, "forget_pattern should clear intervention_events"

    def test_forget_pattern_removes_it(self, profile):
        memory.record_observation(
            session_id="sess_forget",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        n = memory.forget_pattern(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        assert n >= 1
        patterns = memory.get_all_patterns(profile=profile)
        assert all(p["issue_type"] != IssueType.MISSING_OUTPUT_CONTRACT.value for p in patterns)

    def test_forget_pattern_clears_short_term_rows(self, profile):
        memory.record_observation(
            session_id="short_term_forget",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        memory.mark_session_nudged(
            "short_term_forget",
            IssueType.MISSING_OUTPUT_CONTRACT,
            profile=profile,
        )

        memory.forget_pattern(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)

        import sqlite3
        db = str(Path(profile) / "coach.db")
        with sqlite3.connect(db) as con:
            turn_count = con.execute(
                "SELECT COUNT(*) FROM session_turns WHERE issue_type=?",
                (IssueType.MISSING_OUTPUT_CONTRACT.value,),
            ).fetchone()[0]
            nudge_count = con.execute(
                "SELECT COUNT(*) FROM session_nudge_log WHERE issue_type=?",
                (IssueType.MISSING_OUTPUT_CONTRACT.value,),
            ).fetchone()[0]
        assert turn_count == 0
        assert nudge_count == 0

    def test_forget_all_clears_everything(self, profile):
        for issue in [IssueType.MISSING_GOAL, IssueType.OVERLOADED_REQUEST]:
            memory.record_observation(
                session_id="sess_clear",
                domain=Domain.CODING,
                issue_type=issue,
                action_taken=Action.POST_ANSWER_TIP,
                profile=profile,
            )
        memory.forget_all(profile=profile)
        assert memory.get_all_patterns(profile=profile) == []


# ── Intervention events ────────────────────────────────────────────────────

class TestInterventionEvents:
    def test_record_and_retrieve(self, profile):
        chain = {
            "issue_type": "missing_output_contract",
            "evidence_count": 5,
            "distinct_sessions": 3,
            "cost_count": 2,
            "last_notified_at": None,
            "cooldown_days": 14,
        }
        ev_id = memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="retrospective_reminder",
            shown=True,
            explanation_chain=chain,
            profile=profile,
        )
        assert ev_id is not None

        last = memory.get_last_why_reminded(profile=profile)
        assert last is not None
        assert last["explanation_chain"]["evidence_count"] == 5

    def test_no_why_reminded_when_empty(self, profile):
        result = memory.get_last_why_reminded(profile=profile)
        assert result is None


class TestDecisionEvents:
    def test_decision_counts_include_checked_surfaced_and_silent(self, profile):
        memory.record_decision(
            action=Action.POST_ANSWER_TIP,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            mode="strict",
            domain=Domain.CODING,
            detection_source="agent",
            profile=profile,
        )
        memory.record_decision(
            action=Action.SESSION_PATTERN_NUDGE,
            issue_type=IssueType.MISSING_GOAL,
            mode="light",
            domain=Domain.WRITING,
            detection_source="rules",
            profile=profile,
        )
        memory.record_decision(
            action=Action.SILENT_REWRITE,
            issue_type=None,
            mode="light",
            domain=Domain.CODING,
            detection_source="agent",
            suppressed_reason="budget_exhausted",
            profile=profile,
        )

        counts = memory.get_decision_counts_7d(profile=profile)
        assert counts == {"checked": 3, "surfaced": 2, "silent": 1}


# ── Proactive/retrospective counts ────────────────────────────────────────

class TestCounts:
    def test_proactive_count_within_7d(self, profile):
        memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="post_answer_tip",
            shown=True,
            profile=profile,
        )
        count = memory.get_proactive_count_7d(profile=profile)
        assert count == 1

    def test_retrospective_count(self, profile):
        memory.record_intervention(
            issue_type=IssueType.MISSING_GOAL,
            surface="retrospective_reminder",
            shown=True,
            profile=profile,
        )
        count = memory.get_retrospective_count_7d(profile=profile)
        assert count == 1


# ── Export ────────────────────────────────────────────────────────────────

class TestExport:
    def test_export_jsonl(self, profile):
        import json
        memory.record_observation(
            session_id="sess_export",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_GOAL,
            action_taken=Action.POST_ANSWER_TIP,
            profile=profile,
        )
        jsonl = memory.export_jsonl(profile=profile)
        lines = [l for l in jsonl.strip().split("\n") if l]
        assert len(lines) >= 1
        obj = json.loads(lines[0])
        assert "session_id" in obj


class TestReminderObservations:
    def test_reminder_actions_do_not_increase_pattern_evidence_or_session_turns(self, profile):
        memory.record_observation(
            session_id="seed_sess",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
            profile=profile,
        )

        before = memory.get_top_pattern(profile=profile)
        assert before is not None
        assert before["evidence_count"] == 1

        memory.record_observation(
            session_id="retro_notice",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.RETROSPECTIVE_REMINDER,
            evidence_summary="coach surfaced retrospective reminder",
            profile=profile,
        )
        memory.record_observation(
            session_id="session_notice",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.SESSION_PATTERN_NUDGE,
            evidence_summary="coach surfaced session reminder",
            profile=profile,
        )

        after = memory.get_top_pattern(profile=profile)
        assert after is not None
        assert after["evidence_count"] == 1

        import sqlite3
        db = str(Path(profile) / "coach.db")
        with sqlite3.connect(db) as con:
            turns = con.execute("SELECT COUNT(*) FROM session_turns").fetchone()[0]
        assert turns == 1
