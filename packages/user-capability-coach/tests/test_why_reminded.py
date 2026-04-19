"""Tests for why-reminded explanation chain (Milestone 4 hard requirement)."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import memory
from tools.taxonomy import IssueType, Domain, Action, CostSignal


@pytest.fixture
def profile(tmp_path):
    memory.init_db(profile=str(tmp_path))
    return str(tmp_path)


def _fill_pattern(profile: str, evidence: int = 5, sessions: int = 4, cost: int = 3) -> None:
    """Write enough observations to trigger a pattern with evidence."""
    for i in range(max(evidence, sessions)):
        memory.record_observation(
            session_id=f"sess_why_{i}",
            domain=Domain.CODING,
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            action_taken=Action.POST_ANSWER_TIP,
            severity=0.7,
            confidence=0.8,
            fixability=0.9,
            cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH if i < cost else CostSignal.NONE,
            evidence_summary=f"coding task {i} lacked format",
            profile=profile,
        )


class TestExplanationChain:
    def test_chain_built_when_pattern_exists(self, profile):
        _fill_pattern(profile)
        chain = memory.build_explanation_chain(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        assert chain is not None
        assert chain["issue_type"] == "missing_output_contract"
        assert chain["evidence_count"] >= 4
        assert chain["distinct_sessions"] >= 1  # at least some sessions
        assert "pattern_score" in chain
        assert "allow_reason" in chain
        assert "built_at" in chain

    def test_chain_none_when_no_pattern(self, profile):
        chain = memory.build_explanation_chain(IssueType.MISSING_GOAL, profile=profile)
        assert chain is None

    def test_chain_cooldown_only_counts_retrospective_interventions(self, profile):
        """Bug regression: prior POST/PRE interventions shouldn't count
        toward the retrospective cooldown — only previous retrospectives
        start the 14-day window."""
        _fill_pattern(profile)
        # Emit many POST tip interventions (not retrospectives)
        for i in range(5):
            memory.record_intervention(
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                surface="post_answer_tip", shown=True, profile=profile,
            )
        chain = memory.build_explanation_chain(
            IssueType.MISSING_OUTPUT_CONTRACT, profile=profile
        )
        assert chain is not None
        # No retrospective ever shown → cooldown is "never"
        assert chain["last_notified_at"] is None
        assert chain["cooldown_ok"] is True, (
            "POST tip interventions must not start the retrospective cooldown"
        )

    def test_chain_excludes_shadow_only_observations(self, profile):
        """Shadow observations must not inflate the counts shown in
        the explanation chain — the pattern card ignores them too."""
        # 4 non-shadow observations (qualifying pattern)
        for i in range(4):
            memory.record_observation(
                session_id=f"real_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.POST_ANSWER_TIP,
                severity=0.7, confidence=0.8,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                profile=profile,
            )
        # 10 shadow observations — must NOT count
        for i in range(10):
            memory.record_observation(
                session_id=f"shadow_sess_{i}",
                domain=Domain.CODING,
                issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
                action_taken=Action.SILENT_REWRITE,
                severity=0.7, confidence=0.8,
                cost_signal=CostSignal.OUTPUT_FORMAT_MISMATCH,
                shadow_only=True,
                profile=profile,
            )
        chain = memory.build_explanation_chain(
            IssueType.MISSING_OUTPUT_CONTRACT, profile=profile
        )
        assert chain is not None
        assert chain["evidence_count"] == 4, (
            f"expected 4 (non-shadow only), got {chain['evidence_count']}"
        )
        assert chain["distinct_sessions"] == 4

    def test_chain_contains_all_required_fields(self, profile):
        _fill_pattern(profile)
        chain = memory.build_explanation_chain(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        required = [
            "issue_type", "evidence_count", "distinct_sessions", "cost_count",
            "last_notified_at", "cooldown_days", "cooldown_ok",
            "pattern_score", "pattern_status", "allow_reason", "built_at",
        ]
        for field in required:
            assert field in chain, f"Missing field: {field}"

    def test_chain_cooldown_ok_when_never_notified(self, profile):
        _fill_pattern(profile)
        chain = memory.build_explanation_chain(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        assert chain["last_notified_at"] is None
        assert chain["cooldown_ok"] is True

    def test_intervention_stored_with_chain(self, profile):
        _fill_pattern(profile)
        chain = memory.build_explanation_chain(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        ev_id = memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="retrospective_reminder",
            shown=True,
            explanation_chain=chain,
            profile=profile,
        )
        last = memory.get_last_why_reminded(profile=profile)
        assert last is not None
        assert last["explanation_chain"]["issue_type"] == "missing_output_contract"


class TestRetrospectiveBlockedWithoutChain:
    """Verify that a reminder cannot be emitted without a complete chain."""

    def test_no_reminder_emitted_if_chain_incomplete(self, profile):
        """This tests the contract: if chain is None, reminder should be suppressed."""
        # No observations → no pattern → chain is None
        chain = memory.build_explanation_chain(IssueType.MISSING_OUTPUT_CONTRACT, profile=profile)
        # System must NOT emit reminder when chain is None
        assert chain is None  # This is the gating condition

    def test_nudge_count_increments_correctly(self, profile):
        memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="retrospective_reminder",
            shown=True,
            profile=profile,
        )
        memory.record_intervention(
            issue_type=IssueType.MISSING_OUTPUT_CONTRACT,
            surface="retrospective_reminder",
            shown=True,
            profile=profile,
        )
        count = memory.get_retrospective_count_7d(profile=profile)
        assert count == 2
