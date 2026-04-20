"""Policy layer: translates detector output + user state → recommended action.

All thresholds live in PolicyConfig so they can be tested and tuned.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

from .taxonomy import (
    Action, CoachMode, Domain, IssueType, V1_VISIBLE_ISSUES,
    PatternStatus,
)
from .detectors import DetectionOutput


@dataclass
class PolicyConfig:
    min_confidence: float = 0.70          # detector confidence threshold
    min_severity: float = 0.50
    # Annoyance budget per 7-day rolling window
    light_max_proactive_per_7d: int = 2
    standard_max_proactive_per_7d: int = 4
    strict_max_proactive_per_7d: int = 10
    light_max_retrospective_per_7d: int = 1
    standard_max_retrospective_per_7d: int = 1
    strict_max_retrospective_per_7d: int = 3
    # Pattern thresholds for retrospective_reminder
    pattern_min_evidence: int = 4
    pattern_min_sessions: int = 3
    pattern_min_cost_count: int = 2
    pattern_cooldown_days: int = 14
    observation_period_days: int = 14
    # Session-level (short-term) pattern thresholds
    session_window: int = 5
    session_min_repeat: int = 3


@dataclass
class PolicyInput:
    mode: CoachMode
    detection: DetectionOutput
    memory_enabled: bool
    observation_period_ends_at: datetime | None
    proactive_count_7d: int            # visible nudges in last 7 days
    retrospective_count_7d: int
    last_notified_pattern: str | None  # issue_type of last reminder
    last_notified_at: datetime | None
    user_dismissed_recently: bool      # user said "don't remind me" recently
    top_pattern: "PatternSummary | None" = None
    # Short-term (within-session) pattern — e.g. "3 of last 5 turns had
    # missing_goal". Distinct from `top_pattern`, which is cross-session.
    # None when no session signal is available or doesn't meet thresholds.
    session_pattern: "SessionPatternSummary | None" = None
    # Whether this (session, session_pattern.issue_type) has already
    # received a session-level nudge in the current session — policy
    # uses this to enforce "once per session per issue".
    session_already_nudged: bool = False


@dataclass
class PatternSummary:
    issue_type: IssueType
    status: PatternStatus
    evidence_count: int
    distinct_sessions: int
    cost_count: int
    score: float
    last_notified_at: datetime | None


@dataclass
class SessionPatternSummary:
    """Short-term per-session pattern: the issue_type most frequent in
    the last N turns of the current session (only when it hits the
    min_repeat threshold)."""
    issue_type: IssueType
    count: int           # turns in window matching this issue_type
    window_size: int     # how many turns were actually examined
    domain: Domain = Domain.OTHER


@dataclass
class PolicyOutput:
    action: Action
    issue_type: IssueType | None
    reason: str
    suppressed_reason: str | None = None


DEFAULT_CONFIG = PolicyConfig()


def select_action(inp: PolicyInput, cfg: PolicyConfig = DEFAULT_CONFIG) -> PolicyOutput:
    """Return the recommended action for the current turn."""
    mode = inp.mode

    if mode == CoachMode.OFF:
        return PolicyOutput(
            action=Action.NONE,
            issue_type=None,
            reason="coaching is off",
            suppressed_reason="mode=off",
        )

    detection = inp.detection

    # Sensitive scene: always none
    if detection.is_sensitive:
        return PolicyOutput(
            action=Action.NONE,
            issue_type=None,
            reason="sensitive domain suppresses coaching",
            suppressed_reason="sensitive_domain",
        )

    # Filter candidates by confidence and severity and v1 visibility
    visible_candidates = [
        c for c in detection.candidates
        if c.issue_type in V1_VISIBLE_ISSUES
        and c.confidence >= cfg.min_confidence
        and c.severity >= cfg.min_severity
    ]

    if not visible_candidates:
        # User dismissal short-circuits ALL pattern-based paths (session +
        # retrospective). If they said "don't remind me", stay silent.
        if inp.user_dismissed_recently:
            return PolicyOutput(
                action=Action.SILENT_REWRITE,
                issue_type=None,
                reason="user recently dismissed coaching; silent_rewrite only",
                suppressed_reason="user_dismissed",
            )
        # Session-level short-term pattern: if the user has shown the same
        # v1 issue in several recent turns of THIS session but the current
        # prompt is clean, surface a once-per-session nudge. This runs
        # before the retrospective check because it's an immediate,
        # in-session signal and doesn't need the 14-day observation period.
        if (
            inp.session_pattern is not None
            and not inp.session_already_nudged
            and not detection.is_urgent
        ):
            return PolicyOutput(
                action=Action.SESSION_PATTERN_NUDGE,
                issue_type=inp.session_pattern.issue_type,
                reason=(
                    f"session_pattern: {inp.session_pattern.issue_type.value} "
                    f"hit {inp.session_pattern.count}/{inp.session_pattern.window_size} "
                    "of recent turns"
                ),
            )
        # No per-turn issue — check if a retrospective reminder is warranted.
        # Only emit retrospective when memory is on and there's a qualifying pattern.
        if inp.memory_enabled and inp.top_pattern is not None:
            ok, retro_reason = should_emit_retrospective(inp, cfg)
            if ok:
                return PolicyOutput(
                    action=Action.RETROSPECTIVE_REMINDER,
                    issue_type=inp.top_pattern.issue_type,
                    reason=f"retrospective: {retro_reason}",
                )
        return PolicyOutput(
            action=Action.SILENT_REWRITE,
            issue_type=None,
            reason="no high-confidence visible issue; apply silent_rewrite if needed",
        )

    # Pick the highest-severity candidate
    best = max(visible_candidates, key=lambda c: c.severity * c.confidence)

    # Check annoyance budget
    if mode == CoachMode.LIGHT:
        budget = cfg.light_max_proactive_per_7d
    elif mode == CoachMode.STRICT:
        budget = cfg.strict_max_proactive_per_7d
    else:
        budget = cfg.standard_max_proactive_per_7d

    # User dismissed recently → downgrade
    if inp.user_dismissed_recently:
        return PolicyOutput(
            action=Action.SILENT_REWRITE,
            issue_type=best.issue_type,
            reason="user recently dismissed coaching; silent_rewrite only",
            suppressed_reason="user_dismissed",
        )

    # Urgent task → post at most, never pre
    if detection.is_urgent:
        if inp.proactive_count_7d >= budget:
            return PolicyOutput(
                action=Action.SILENT_REWRITE,
                issue_type=best.issue_type,
                reason="urgent task + budget exhausted",
                suppressed_reason="budget_exhausted+urgent",
            )
        return PolicyOutput(
            action=Action.POST_ANSWER_TIP,
            issue_type=best.issue_type,
            reason=f"urgent task: tip only after answer (conf={best.confidence:.2f})",
        )

    # Budget exhausted
    if inp.proactive_count_7d >= budget:
        return PolicyOutput(
            action=Action.SILENT_REWRITE,
            issue_type=best.issue_type,
            reason="proactive budget exhausted",
            suppressed_reason="budget_exhausted",
        )

    # Decide pre vs post based on severity and mode
    if mode == CoachMode.STANDARD and best.severity >= 0.75 and best.confidence >= 0.80:
        action = Action.PRE_ANSWER_MICRO_NUDGE
    else:
        action = Action.POST_ANSWER_TIP

    return PolicyOutput(
        action=action,
        issue_type=best.issue_type,
        reason=f"issue={best.issue_type.value} conf={best.confidence:.2f} sev={best.severity:.2f}",
    )


def should_emit_retrospective(
    inp: PolicyInput,
    cfg: PolicyConfig = DEFAULT_CONFIG,
) -> tuple[bool, str]:
    """Return (should_emit, reason). Reason is used in why-reminded chain."""
    if inp.mode == CoachMode.OFF:
        return False, "mode=off"

    if inp.detection.is_sensitive:
        return False, "sensitive_domain"

    if inp.top_pattern is None:
        return False, "no_pattern_available"

    p = inp.top_pattern

    # Only v1 visible issue types can trigger a retrospective reminder.
    # Shadow-only issue types (missing_context, etc.) accumulate stats but
    # never surface to the user in v1.
    if p.issue_type not in V1_VISIBLE_ISSUES:
        return False, f"issue_type_not_v1_visible:{p.issue_type.value}"

    # Observation period check
    now = datetime.now(timezone.utc)
    if inp.observation_period_ends_at and now < inp.observation_period_ends_at:
        remaining = (inp.observation_period_ends_at - now).days
        return False, f"observation_period_active:{remaining}d_remaining"

    # Pattern status check
    if p.status != PatternStatus.ACTIVE:
        return False, f"pattern_status={p.status.value}"

    # Evidence thresholds
    if p.evidence_count < cfg.pattern_min_evidence:
        return False, f"evidence_count={p.evidence_count}<{cfg.pattern_min_evidence}"
    if p.distinct_sessions < cfg.pattern_min_sessions:
        return False, f"distinct_sessions={p.distinct_sessions}<{cfg.pattern_min_sessions}"
    if p.cost_count < cfg.pattern_min_cost_count:
        return False, f"cost_count={p.cost_count}<{cfg.pattern_min_cost_count}"

    # Cooldown check
    if inp.last_notified_at:
        days_since = (now - inp.last_notified_at).days
        if days_since < cfg.pattern_cooldown_days:
            return False, f"cooldown:{days_since}d_since_last_of_{cfg.pattern_cooldown_days}d"

    # Retrospective budget
    if inp.mode == CoachMode.LIGHT:
        retro_budget = cfg.light_max_retrospective_per_7d
    elif inp.mode == CoachMode.STRICT:
        retro_budget = cfg.strict_max_retrospective_per_7d
    else:
        retro_budget = cfg.standard_max_retrospective_per_7d
    if inp.retrospective_count_7d >= retro_budget:
        return False, f"retrospective_budget_exhausted({inp.retrospective_count_7d}/{retro_budget})"

    return True, (
        f"pattern={p.issue_type.value} "
        f"evidence={p.evidence_count} "
        f"sessions={p.distinct_sessions} "
        f"cost_count={p.cost_count} "
        f"last_notified={'never' if not inp.last_notified_at else inp.last_notified_at.isoformat()}"
    )
