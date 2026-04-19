"""Tests for taxonomy constants and enumerations."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.taxonomy import (
    IssueType, Action, CoachMode, Domain, PatternStatus, CostSignal,
    V1_VISIBLE_ISSUES, RULE_DETECTABLE_ISSUES, MICRO_HABITS,
    MICRO_HABITS_EN, SENSITIVE_KEYWORDS,
)


def test_all_issue_types_are_visible():
    """Post-④ all 8 IssueType members are in V1_VISIBLE_ISSUES. Previously
    only 3 were visible and the other 5 were shadow-only."""
    all_issues = set(IssueType)
    assert V1_VISIBLE_ISSUES == all_issues


def test_rule_detectable_subset_of_visible():
    """RULE_DETECTABLE_ISSUES ⊂ V1_VISIBLE_ISSUES. Only
    missing_constraints remains agent-only — its rule-detection
    fights too hard with missing_output_contract."""
    assert RULE_DETECTABLE_ISSUES.issubset(V1_VISIBLE_ISSUES)
    assert IssueType.MISSING_CONSTRAINTS not in RULE_DETECTABLE_ISSUES
    # The other two weak-rule types ARE in the set (may not reach
    # policy thresholds alone, but agent can reinforce them)
    assert IssueType.CONFLICTING_INSTRUCTIONS in RULE_DETECTABLE_ISSUES
    assert IssueType.MISSING_SUCCESS_CRITERIA in RULE_DETECTABLE_ISSUES


def test_micro_habits_coverage():
    """Every visible issue must have ZH + EN micro habits so coach
    can always produce a user-facing suggestion."""
    for issue in V1_VISIBLE_ISSUES:
        assert issue in MICRO_HABITS, f"Missing ZH micro habit for {issue}"
        assert issue in MICRO_HABITS_EN, f"Missing EN micro habit for {issue}"


def test_sensitive_keywords_not_empty():
    assert len(SENSITIVE_KEYWORDS) > 10


def test_action_enum_values():
    assert Action.NONE.value == "none"
    assert Action.SILENT_REWRITE.value == "silent_rewrite"
    assert Action.POST_ANSWER_TIP.value == "post_answer_tip"
    assert Action.PRE_ANSWER_MICRO_NUDGE.value == "pre_answer_micro_nudge"
    assert Action.RETROSPECTIVE_REMINDER.value == "retrospective_reminder"


def test_coach_mode_values():
    assert CoachMode.OFF.value == "off"
    assert CoachMode.LIGHT.value == "light"
    assert CoachMode.STANDARD.value == "standard"
    assert CoachMode.STRICT.value == "strict"


def test_domain_sensitive_exists():
    assert Domain.SENSITIVE in Domain


def test_pattern_status_transitions():
    """All expected status values are enumerated."""
    statuses = {s.value for s in PatternStatus}
    assert "active" in statuses
    assert "cooling" in statuses
    assert "resolved" in statuses
    assert "archived" in statuses
