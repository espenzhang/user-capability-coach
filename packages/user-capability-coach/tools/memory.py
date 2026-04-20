"""SQLite-backed memory for observations, patterns, and intervention events.

Design principles:
  - Idempotent writes (same session + issue_type + domain = upsert)
  - No raw prompt text stored
  - Sensitive domain: only write domain=sensitive, shadow_only=1, no content
  - Pattern scoring: time-decayed, weekly * 0.85
  - Cooldown and observation period enforced at read time
"""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Generator

from .platform import Platform, ensure_data_dir
from .taxonomy import IssueType, Domain, Action, PatternStatus, CostSignal
from .time_utils import parse_iso_datetime_utc


NON_EVIDENCE_ACTIONS = frozenset({
    Action.RETROSPECTIVE_REMINDER.value,
    Action.SESSION_PATTERN_NUDGE.value,
})
VISIBLE_DECISION_ACTIONS = frozenset({
    Action.POST_ANSWER_TIP.value,
    Action.PRE_ANSWER_MICRO_NUDGE.value,
    Action.RETROSPECTIVE_REMINDER.value,
    Action.SESSION_PATTERN_NUDGE.value,
})
SILENT_DECISION_ACTIONS = frozenset({
    Action.SILENT_REWRITE.value,
    Action.NONE.value,
})


SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    platform TEXT,
    domain TEXT NOT NULL,
    task_type TEXT,
    issue_type TEXT,
    severity REAL,
    confidence REAL,
    fixability REAL,
    cost_signal TEXT,
    action_taken TEXT,
    user_feedback TEXT,
    evidence_summary TEXT,
    shadow_only INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS patterns (
    issue_type TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'other',
    score REAL NOT NULL DEFAULT 0.0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    distinct_sessions INTEGER NOT NULL DEFAULT 0,
    cost_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    micro_habit TEXT,
    last_seen_at TEXT,
    last_notified_at TEXT,
    resolved_at TEXT,
    PRIMARY KEY (issue_type, domain)
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS intervention_events (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    issue_type TEXT,
    surface TEXT,
    shown INTEGER NOT NULL DEFAULT 0,
    suppressed_reason TEXT,
    accepted INTEGER,
    dismissed INTEGER,
    ignored INTEGER,
    followup_improvement_detected INTEGER,
    explanation_chain TEXT
);

CREATE TABLE IF NOT EXISTS decision_events (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    issue_type TEXT,
    mode TEXT NOT NULL,
    domain TEXT NOT NULL,
    detection_source TEXT NOT NULL,
    suppressed_reason TEXT
);

-- Per-session short-term memory. Unlike `patterns` (cross-session
-- longitudinal), `session_turns` accumulates per-turn classifications
-- within a single session so the policy can detect "3 out of the last 5
-- turns had the same issue" — something single-turn detectors cannot
-- see. No raw text stored.
CREATE TABLE IF NOT EXISTS session_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    issue_type TEXT,
    domain TEXT,
    severity REAL,
    confidence REAL,
    shadow_only INTEGER NOT NULL DEFAULT 0
);

-- Which (session_id, issue_type) have already received a session-level
-- nudge — prevents repeating within the same session.
CREATE TABLE IF NOT EXISTS session_nudge_log (
    session_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (session_id, issue_type)
);

CREATE INDEX IF NOT EXISTS idx_observations_session ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_observations_issue ON observations(issue_type);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
-- Composite index accelerating build_explanation_chain's 3 COUNT queries
-- which all filter (issue_type, shadow_only). Without this SQLite falls
-- back to scanning observations; with it the chain builds in O(log n).
CREATE INDEX IF NOT EXISTS idx_observations_issue_shadow ON observations(issue_type, shadow_only);
CREATE INDEX IF NOT EXISTS idx_patterns_status ON patterns(status);
CREATE INDEX IF NOT EXISTS idx_session_turns_session ON session_turns(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_session_turns_ts ON session_turns(ts);
-- Intervention events are looked up by (issue_type, surface, shown) in
-- build_explanation_chain's retrospective-cooldown query and by surface+ts
-- in get_{proactive,retrospective}_count_7d.
CREATE INDEX IF NOT EXISTS idx_intervention_issue_surface ON intervention_events(issue_type, surface, shown);
CREATE INDEX IF NOT EXISTS idx_intervention_ts ON intervention_events(ts);
CREATE INDEX IF NOT EXISTS idx_decision_events_ts ON decision_events(ts);
"""


def _db_path(platform: Platform | None = None, profile: str | None = None) -> Path:
    return ensure_data_dir(platform, profile) / "coach.db"


_INITIALIZED_DBS: set[str] = set()


def _get_schema_version_locked(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_schema_version_locked(con: sqlite3.Connection, version: int) -> None:
    con.execute(
        """INSERT INTO schema_meta (key, value) VALUES ('version', ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (str(version),),
    )


def _replay_pattern_score(rows: list[sqlite3.Row]) -> float:
    score = 0.0
    last_seen: datetime | None = None
    for row in rows:
        ts = parse_iso_datetime_utc(row["ts"])
        if ts is None:
            continue
        if last_seen is None:
            score = 1.0
        else:
            weeks_elapsed = max(
                0.0,
                (ts - last_seen).total_seconds() / (7 * 86400),
            )
            score = score * (0.85 ** weeks_elapsed) + 1.0
        last_seen = ts
    return score


def _apply_pattern_decay_locked(
    con: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    rows = con.execute(
        "SELECT issue_type, domain, score, last_seen_at FROM patterns"
    ).fetchall()
    for row in rows:
        if not row["last_seen_at"]:
            continue
        last_seen = parse_iso_datetime_utc(row["last_seen_at"])
        if last_seen is None:
            continue
        weeks = max(
            0.0,
            (now - last_seen).total_seconds() / (7 * 86400),
        )
        decayed = row["score"] * (0.85 ** weeks)
        days_since = (now - last_seen).days
        if days_since >= 90:
            status = PatternStatus.ARCHIVED.value
        elif days_since >= 60:
            status = PatternStatus.RESOLVED.value
        elif days_since >= 30 or decayed < 0.5:
            status = PatternStatus.COOLING.value
        else:
            status = PatternStatus.ACTIVE.value
        resolved_at = row["last_seen_at"] if status in (
            PatternStatus.RESOLVED.value,
            PatternStatus.ARCHIVED.value,
        ) else None
        con.execute(
            """UPDATE patterns
               SET score=?, status=?, resolved_at=?
               WHERE issue_type=? AND domain=?""",
            (
                decayed,
                status,
                resolved_at,
                row["issue_type"],
                row["domain"],
            ),
        )


def _rebuild_pattern_state_locked(con: sqlite3.Connection) -> None:
    existing_rows = con.execute(
        "SELECT issue_type, domain, micro_habit, last_notified_at FROM patterns"
    ).fetchall()
    existing_meta = {
        (row["issue_type"], row["domain"]): {
            "micro_habit": row["micro_habit"],
            "last_notified_at": row["last_notified_at"],
        }
        for row in existing_rows
    }

    evidence_rows = con.execute(
        "SELECT issue_type, domain, session_id, ts, cost_signal "
        "FROM observations "
        "WHERE issue_type IS NOT NULL AND shadow_only=0 "
        "AND action_taken NOT IN (?, ?) "
        "ORDER BY issue_type, domain, ts",
        tuple(NON_EVIDENCE_ACTIONS),
    ).fetchall()

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in evidence_rows:
        grouped.setdefault((row["issue_type"], row["domain"]), []).append(row)

    con.execute("DELETE FROM patterns")
    for (issue_type, domain), rows in grouped.items():
        sessions = {row["session_id"] for row in rows}
        cost_count = sum(
            1 for row in rows
            if row["cost_signal"] and row["cost_signal"] != CostSignal.NONE.value
        )
        meta = existing_meta.get((issue_type, domain), {})
        con.execute(
            """INSERT INTO patterns
               (issue_type, domain, score, evidence_count, distinct_sessions,
                cost_count, status, micro_habit, last_seen_at, last_notified_at, resolved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                issue_type,
                domain,
                _replay_pattern_score(rows),
                len(rows),
                len(sessions),
                cost_count,
                PatternStatus.ACTIVE.value,
                meta.get("micro_habit"),
                rows[-1]["ts"],
                meta.get("last_notified_at"),
                None,
            ),
        )

    _apply_pattern_decay_locked(con)


def _maybe_upgrade_db_locked(con: sqlite3.Connection) -> None:
    version = _get_schema_version_locked(con)
    if version < 2:
        _rebuild_pattern_state_locked(con)
    _set_schema_version_locked(con, SCHEMA_VERSION)


@contextmanager
def _conn(
    platform: Platform | None = None,
    profile: str | None = None,
    *,
    immediate: bool = False,
) -> Generator[sqlite3.Connection, None, None]:
    path = _db_path(platform, profile)
    # Auto-run schema once per process per DB path. Safe — SCHEMA_SQL uses
    # IF NOT EXISTS everywhere, so this is idempotent. Prevents callers from
    # having to remember to init_db() before query functions.
    path_key = str(path)
    if path_key not in _INITIALIZED_DBS:
        con_init = sqlite3.connect(path_key)
        con_init.row_factory = sqlite3.Row
        try:
            con_init.executescript(SCHEMA_SQL)
            _maybe_upgrade_db_locked(con_init)
            con_init.commit()
        finally:
            con_init.close()
        _INITIALIZED_DBS.add(path_key)

    con = sqlite3.connect(path_key, timeout=10.0)
    con.row_factory = sqlite3.Row
    if immediate:
        # Acquire write lock up front so read-compute-write cycles don't
        # race (classic lost-update problem). Without this, two concurrent
        # `record_observation` calls can both SELECT the same pattern row,
        # each compute `evidence+1`, and one UPDATE clobbers the other.
        con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db(platform: Platform | None = None, profile: str | None = None) -> Path:
    """Initialize schema and return db path. Safe to call repeatedly."""
    path = _db_path(platform, profile)
    with _conn(platform, profile) as con:
        row = con.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO schema_meta VALUES ('version', ?)",
                (str(SCHEMA_VERSION),),
            )
    return path


def record_observation(
    session_id: str,
    domain: Domain,
    issue_type: IssueType | None,
    action_taken: Action,
    *,
    severity: float = 0.0,
    confidence: float = 0.0,
    fixability: float = 0.0,
    cost_signal: CostSignal = CostSignal.NONE,
    evidence_summary: str = "",
    task_type: str | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
    shadow_only: bool = False,
    sensitive_logging_enabled: bool = False,
) -> str:
    """Write one observation. Returns the observation id.

    `sensitive_logging_enabled` is an explicit opt-in: when the user has
    asked to be trained on sensitive prompts, the issue_type + evidence
    summary are still recorded under domain=sensitive. By default this is
    False and the sensitive branch strips all content.
    """
    obs_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Sensitive domain: default strip. Only keep content if user opted in.
    if domain == Domain.SENSITIVE and not sensitive_logging_enabled:
        issue_type = None
        evidence_summary = ""
        shadow_only = True

    counts_as_evidence = action_taken.value not in NON_EVIDENCE_ACTIONS

    # Session-level short-term turn record. Written on EVERY call — even
    # when observation-layer idempotency below dedupes the current call —
    # because session_turns tracks per-turn granularity ("3 vague turns
    # in a row") whereas observations/patterns track per-session dedupd
    # evidence. Recording outside the `with _conn` block below keeps the
    # two writes in separate transactions, which is fine: if a crash
    # loses the session_turn, the observation still stands.
    if counts_as_evidence:
        record_session_turn(
            session_id=session_id,
            issue_type=issue_type,
            domain=domain,
            severity=severity,
            confidence=confidence,
            shadow_only=shadow_only,
            platform=platform,
            profile=profile,
        )

    # Use IMMEDIATE so the idempotency SELECT + INSERT pair is atomic
    # against other writers. Otherwise two concurrent writes of the same
    # (session, issue) pair can both miss the existing row and each INSERT.
    with _conn(platform, profile, immediate=True) as con:
        # Idempotent: if same session+issue_type already recorded, skip
        if issue_type is not None:
            existing = con.execute(
                "SELECT id FROM observations WHERE session_id=? AND issue_type=? AND domain=?",
                (session_id, issue_type.value, domain.value),
            ).fetchone()
            if existing:
                return existing["id"]

        con.execute(
            """INSERT INTO observations
               (id, ts, session_id, platform, domain, task_type, issue_type,
                severity, confidence, fixability, cost_signal, action_taken,
                evidence_summary, shadow_only)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                obs_id, now, session_id,
                platform.value if platform else None,
                domain.value,
                task_type,
                issue_type.value if issue_type else None,
                severity, confidence, fixability,
                cost_signal.value,
                action_taken.value,
                evidence_summary,
                1 if shadow_only else 0,
            ),
        )

    # Update pattern score if we have an issue_type
    if issue_type and not shadow_only and counts_as_evidence:
        _update_pattern(
            issue_type=issue_type,
            domain=domain,
            session_id=session_id,
            cost_signal=cost_signal,
            evidence_summary=evidence_summary,
            platform=platform,
            profile=profile,
        )

    return obs_id


def _update_pattern(
    issue_type: IssueType,
    domain: Domain,
    session_id: str,
    cost_signal: CostSignal,
    evidence_summary: str,
    platform: Platform | None,
    profile: str | None,
) -> None:
    """Upsert pattern card with time-decay scoring.

    Uses BEGIN IMMEDIATE to serialize concurrent updates to the same pattern
    row — otherwise two threads observing the same issue can both read
    `evidence_count=5`, each write `6`, and one update is silently lost.
    """
    now = datetime.now(timezone.utc).isoformat()
    has_cost = cost_signal != CostSignal.NONE

    with _conn(platform, profile, immediate=True) as con:
        row = con.execute(
            "SELECT * FROM patterns WHERE issue_type=? AND domain=?",
            (issue_type.value, domain.value),
        ).fetchone()

        if row is None:
            con.execute(
                """INSERT INTO patterns
                   (issue_type, domain, score, evidence_count, distinct_sessions,
                    cost_count, status, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    issue_type.value, domain.value,
                    1.0, 1, 1,
                    1 if has_cost else 0,
                    PatternStatus.ACTIVE.value,
                    now,
                ),
            )
        else:
            # Count distinct sessions EXCLUDING the current one (already committed)
            # by counting sessions with an earlier ID or comparing against stored count
            prior_sessions = con.execute(
                "SELECT COUNT(DISTINCT session_id) as c FROM observations "
                "WHERE issue_type=? AND domain=? AND session_id != ? "
                "AND shadow_only=0 AND action_taken NOT IN (?, ?)",
                (issue_type.value, domain.value, session_id, *NON_EVIDENCE_ACTIONS),
            ).fetchone()["c"]
            stored_distinct = row["distinct_sessions"]
            is_new_session = prior_sessions >= stored_distinct

            old_score = row["score"]
            # Apply weekly decay: if last_seen_at > 7 days ago, decay.
            # Clamp to >= 0 in case of clock skew (last_seen in the future) —
            # otherwise 0.85 ** negative > 1 would inflate the score.
            last_seen = parse_iso_datetime_utc(row["last_seen_at"])
            if last_seen:
                weeks_elapsed = max(
                    0.0,
                    (datetime.now(timezone.utc) - last_seen).total_seconds() / (7 * 86400),
                )
                decayed = old_score * (0.85 ** weeks_elapsed)
            else:
                decayed = old_score

            new_score = decayed + 1.0
            new_distinct = row["distinct_sessions"] + (1 if is_new_session else 0)
            new_cost = row["cost_count"] + (1 if has_cost else 0)
            new_evidence = row["evidence_count"] + 1

            # Determine new status
            current_status = row["status"]
            if current_status == PatternStatus.ARCHIVED.value:
                # Archived patterns stay archived — a single new observation
                # doesn't reactivate them (prevents noise from very old patterns)
                status = PatternStatus.ARCHIVED.value
                resolved_at = row["resolved_at"]  # preserve
            elif new_score < 0.5:
                status = PatternStatus.COOLING.value
                resolved_at = row["resolved_at"]
            else:
                status = PatternStatus.ACTIVE.value
                # Clear resolved_at if pattern was previously resolved/cooling and
                # is coming back to active — new observations mean it's recurring
                resolved_at = None

            con.execute(
                """UPDATE patterns SET
                   score=?, evidence_count=?, distinct_sessions=?, cost_count=?,
                   status=?, last_seen_at=?, resolved_at=?
                   WHERE issue_type=? AND domain=?""",
                (
                    new_score, new_evidence, new_distinct, new_cost,
                    status, now, resolved_at,
                    issue_type.value, domain.value,
                ),
            )


def _get_distinct_sessions(
    issue_type: IssueType,
    domain: Domain,
    platform: Platform | None,
    profile: str | None,
) -> set[str]:
    with _conn(platform, profile) as con:
        rows = con.execute(
            "SELECT DISTINCT session_id FROM observations "
            "WHERE issue_type=? AND domain=? "
            "AND shadow_only=0 AND action_taken NOT IN (?, ?)",
            (issue_type.value, domain.value, *NON_EVIDENCE_ACTIONS),
        ).fetchall()
    return {r["session_id"] for r in rows}


def apply_weekly_decay(platform: Platform | None = None, profile: str | None = None) -> None:
    """Decay all pattern scores by 0.85 per week since last_seen_at.
    Also prunes stale session_turns (7-day TTL) and observations whose
    patterns have moved to resolved/archived (180-day TTL) as part of
    the same maintenance pass — keeps the DB bounded without a separate
    cron."""
    # Housekeeping first so decay sees a compact state
    prune_old_session_turns(platform=platform, profile=profile)
    prune_old_observations(platform=platform, profile=profile)

    with _conn(platform, profile, immediate=True) as con:
        _apply_pattern_decay_locked(con)


def get_top_pattern(
    platform: Platform | None = None,
    profile: str | None = None,
    v1_visible_only: bool = True,
) -> dict[str, Any] | None:
    """Return the highest-scoring active pattern, or None.

    Defaults to v1_visible_only=True so that shadow-only issue types
    (missing_context, etc.) do not hide a qualifying v1 pattern from the
    retrospective-reminder path. Callers who want the truly top active
    pattern regardless of visibility can pass v1_visible_only=False.
    """
    from .taxonomy import V1_VISIBLE_ISSUES
    with _conn(platform, profile) as con:
        if v1_visible_only:
            # Parameterize the IN clause to avoid SQL-injection code smell,
            # even though V1_VISIBLE_ISSUES is a hardcoded frozenset of
            # enum values and can't contain user input.
            values = tuple(i.value for i in V1_VISIBLE_ISSUES)
            placeholders = ",".join("?" * len(values))
            row = con.execute(
                f"SELECT * FROM patterns WHERE status='active' "
                f"AND issue_type IN ({placeholders}) "
                "ORDER BY score DESC LIMIT 1",
                values,
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM patterns WHERE status='active' "
                "ORDER BY score DESC LIMIT 1"
            ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_all_patterns(
    platform: Platform | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    with _conn(platform, profile) as con:
        rows = con.execute(
            "SELECT * FROM patterns ORDER BY score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def forget_pattern(
    issue_type: IssueType,
    domain: Domain | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
) -> int:
    """Delete pattern(s) and related observations and intervention events.

    Per plan.md: "forget-pattern 删除某条模式后，相关模式卡片和可追溯
    observation 都要一起处理" — also clears intervention_events so budget
    counts and why-reminded state stay consistent. Intervention events
    have no domain column (pattern-level), so deletion uses issue_type
    regardless of whether a domain was specified.
    """
    with _conn(platform, profile) as con:
        if domain:
            c = con.execute(
                "DELETE FROM patterns WHERE issue_type=? AND domain=?",
                (issue_type.value, domain.value),
            )
            con.execute(
                "DELETE FROM observations WHERE issue_type=? AND domain=?",
                (issue_type.value, domain.value),
            )
            con.execute(
                "DELETE FROM session_turns WHERE issue_type=? AND domain=?",
                (issue_type.value, domain.value),
            )
            con.execute(
                "DELETE FROM decision_events WHERE issue_type=? AND domain=?",
                (issue_type.value, domain.value),
            )
        else:
            c = con.execute(
                "DELETE FROM patterns WHERE issue_type=?",
                (issue_type.value,),
            )
            con.execute(
                "DELETE FROM observations WHERE issue_type=?",
                (issue_type.value,),
            )
            con.execute(
                "DELETE FROM session_turns WHERE issue_type=?",
                (issue_type.value,),
            )
            con.execute(
                "DELETE FROM decision_events WHERE issue_type=?",
                (issue_type.value,),
            )
        con.execute(
            "DELETE FROM session_nudge_log WHERE issue_type=?",
            (issue_type.value,),
        )
        # Intervention events are keyed by issue_type only (no domain column).
        # Delete any event for this issue_type when the caller asks to
        # forget that issue entirely. When a specific domain was given,
        # we still clear all events for that issue_type — the plan treats
        # "forget pattern" as a unified privacy action per issue.
        con.execute(
            "DELETE FROM intervention_events WHERE issue_type=?",
            (issue_type.value,),
        )
    return c.rowcount


def forget_all(platform: Platform | None = None, profile: str | None = None) -> None:
    """Delete all observations and patterns."""
    with _conn(platform, profile) as con:
        con.execute("DELETE FROM patterns")
        con.execute("DELETE FROM observations")
        con.execute("DELETE FROM intervention_events")
        con.execute("DELETE FROM decision_events")
        con.execute("DELETE FROM session_turns")
        con.execute("DELETE FROM session_nudge_log")


def record_decision(
    action: Action,
    *,
    issue_type: IssueType | None,
    mode: str,
    domain: Domain,
    detection_source: str,
    suppressed_reason: str | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
) -> str:
    """Record the result of one select-action evaluation."""
    ev_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn(platform, profile) as con:
        con.execute(
            """INSERT INTO decision_events
               (id, ts, action, issue_type, mode, domain, detection_source, suppressed_reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ev_id,
                now,
                action.value,
                issue_type.value if issue_type else None,
                mode,
                domain.value,
                detection_source,
                suppressed_reason,
            ),
        )
    return ev_id


def get_decision_counts_7d(
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    placeholders_visible = ",".join("?" * len(VISIBLE_DECISION_ACTIONS))
    placeholders_silent = ",".join("?" * len(SILENT_DECISION_ACTIONS))
    with _conn(platform, profile) as con:
        checked = con.execute(
            "SELECT COUNT(*) as c FROM decision_events WHERE ts > ?",
            (cutoff,),
        ).fetchone()["c"]
        surfaced = con.execute(
            f"""SELECT COUNT(*) as c FROM decision_events
                WHERE ts > ? AND action IN ({placeholders_visible})""",
            (cutoff, *VISIBLE_DECISION_ACTIONS),
        ).fetchone()["c"]
        silent = con.execute(
            f"""SELECT COUNT(*) as c FROM decision_events
                WHERE ts > ? AND action IN ({placeholders_silent})""",
            (cutoff, *SILENT_DECISION_ACTIONS),
        ).fetchone()["c"]
    return {
        "checked": checked,
        "surfaced": surfaced,
        "silent": silent,
    }


def record_intervention(
    issue_type: IssueType | None,
    surface: str,
    shown: bool,
    suppressed_reason: str | None = None,
    explanation_chain: dict | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
) -> str:
    """Record that a coaching action was considered/shown."""
    ev_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn(platform, profile) as con:
        con.execute(
            """INSERT INTO intervention_events
               (id, ts, issue_type, surface, shown, suppressed_reason, explanation_chain)
               VALUES (?,?,?,?,?,?,?)""",
            (
                ev_id, now,
                issue_type.value if issue_type else None,
                surface,
                1 if shown else 0,
                suppressed_reason,
                json.dumps(explanation_chain) if explanation_chain else None,
            ),
        )
    return ev_id


def build_explanation_chain(
    issue_type: IssueType,
    domain: Domain | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Build a full explanation chain for why-reminded. Returns None if insufficient data."""
    with _conn(platform, profile) as con:
        if domain is not None:
            pattern = con.execute(
                "SELECT * FROM patterns WHERE issue_type=? AND domain=? LIMIT 1",
                (issue_type.value, domain.value),
            ).fetchone()
        else:
            pattern = con.execute(
                "SELECT * FROM patterns WHERE issue_type=? ORDER BY score DESC LIMIT 1",
                (issue_type.value,),
            ).fetchone()
        if pattern is None:
            return None

        pattern_domain = pattern["domain"]

        # Counts here must mirror what the pattern card sees: shadow
        # observations don't update patterns, so they must not appear in
        # the explanation chain either.
        evidence_count = con.execute(
            "SELECT COUNT(*) as c FROM observations "
            "WHERE issue_type=? AND domain=? AND shadow_only=0 "
            "AND action_taken NOT IN (?, ?)",
            (issue_type.value, pattern_domain, *NON_EVIDENCE_ACTIONS),
        ).fetchone()["c"]

        cost_count = con.execute(
            "SELECT COUNT(*) as c FROM observations "
            "WHERE issue_type=? AND domain=? "
            "AND cost_signal != 'none' AND shadow_only=0 "
            "AND action_taken NOT IN (?, ?)",
            (issue_type.value, pattern_domain, *NON_EVIDENCE_ACTIONS),
        ).fetchone()["c"]

        sessions_count = con.execute(
            "SELECT COUNT(DISTINCT session_id) as c FROM observations "
            "WHERE issue_type=? AND domain=? AND shadow_only=0 "
            "AND action_taken NOT IN (?, ?)",
            (issue_type.value, pattern_domain, *NON_EVIDENCE_ACTIONS),
        ).fetchone()["c"]

    last_dt = parse_iso_datetime_utc(pattern["last_notified_at"])
    last_notified_at = last_dt.isoformat() if last_dt else None
    cooldown_days = 14

    if last_dt:
        days_since = (datetime.now(timezone.utc) - last_dt).days
        cooldown_ok = days_since >= cooldown_days
    else:
        days_since = None
        cooldown_ok = True

    chain = {
        "issue_type": issue_type.value,
        "domain": pattern_domain,
        "evidence_count": evidence_count,
        "distinct_sessions": sessions_count,
        "cost_count": cost_count,
        "last_notified_at": last_notified_at,
        "cooldown_days": cooldown_days,
        "days_since_last_notification": days_since,
        "cooldown_ok": cooldown_ok,
        "pattern_score": float(dict(pattern)["score"]),
        "pattern_status": dict(pattern)["status"],
        "allow_reason": (
            f"evidence={evidence_count}>={4}, "
            f"sessions={sessions_count}>={3}, "
            f"cost={cost_count}>={2}, "
            f"cooldown={'ok' if cooldown_ok else 'blocked'}"
        ),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }

    # Validate completeness — if any critical field is None, return None
    required = ["evidence_count", "distinct_sessions", "cost_count", "pattern_score"]
    if any(chain.get(k) is None for k in required):
        return None

    return chain


def mark_pattern_notified(
    issue_type: IssueType,
    domain: Domain | None = None,
    platform: Platform | None = None,
    profile: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn(platform, profile) as con:
        if domain:
            con.execute(
                "UPDATE patterns SET last_notified_at=? WHERE issue_type=? AND domain=?",
                (now, issue_type.value, domain.value),
            )
        else:
            con.execute(
                "UPDATE patterns SET last_notified_at=? WHERE issue_type=?",
                (now, issue_type.value),
            )


def get_proactive_count_7d(
    platform: Platform | None = None,
    profile: str | None = None,
) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with _conn(platform, profile) as con:
        row = con.execute(
            """SELECT COUNT(*) as c FROM intervention_events
               WHERE ts > ? AND shown=1 AND surface IN ('post_answer_tip', 'pre_answer_micro_nudge')""",
            (cutoff,),
        ).fetchone()
    return row["c"] if row else 0


def get_retrospective_count_7d(
    platform: Platform | None = None,
    profile: str | None = None,
) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with _conn(platform, profile) as con:
        row = con.execute(
            """SELECT COUNT(*) as c FROM intervention_events
               WHERE ts > ? AND shown=1 AND surface='retrospective_reminder'""",
            (cutoff,),
        ).fetchone()
    return row["c"] if row else 0


def get_last_why_reminded(
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    with _conn(platform, profile) as con:
        row = con.execute(
            """SELECT * FROM intervention_events
               WHERE shown=1 AND explanation_chain IS NOT NULL
               ORDER BY ts DESC LIMIT 1""",
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("explanation_chain"):
        d["explanation_chain"] = json.loads(d["explanation_chain"])
    return d


def export_jsonl(
    platform: Platform | None = None,
    profile: str | None = None,
) -> str:
    """Export observations, patterns, and state tables as JSONL string."""
    import io
    buf = io.StringIO()
    with _conn(platform, profile) as con:
        for row in con.execute("SELECT * FROM observations"):
            buf.write(json.dumps(dict(row)) + "\n")
        for row in con.execute("SELECT * FROM patterns"):
            buf.write(json.dumps({"_type": "pattern", **dict(row)}) + "\n")
        for row in con.execute("SELECT * FROM intervention_events"):
            buf.write(json.dumps({"_type": "intervention_event", **dict(row)}) + "\n")
        for row in con.execute("SELECT * FROM decision_events"):
            buf.write(json.dumps({"_type": "decision_event", **dict(row)}) + "\n")
        for row in con.execute("SELECT * FROM session_turns"):
            buf.write(json.dumps({"_type": "session_turn", **dict(row)}) + "\n")
        for row in con.execute("SELECT * FROM session_nudge_log"):
            buf.write(json.dumps({"_type": "session_nudge_log", **dict(row)}) + "\n")
    return buf.getvalue()


_OBS_COLUMNS = (
    "id", "ts", "session_id", "platform", "domain", "task_type", "issue_type",
    "severity", "confidence", "fixability", "cost_signal", "action_taken",
    "user_feedback", "evidence_summary", "shadow_only",
)
_PATTERN_COLUMNS = (
    "issue_type", "domain", "score", "evidence_count", "distinct_sessions",
    "cost_count", "status", "micro_habit", "last_seen_at", "last_notified_at",
    "resolved_at",
)
_INTERVENTION_COLUMNS = (
    "id", "ts", "issue_type", "surface", "shown", "suppressed_reason",
    "accepted", "dismissed", "ignored", "followup_improvement_detected",
    "explanation_chain",
)
_DECISION_COLUMNS = (
    "id", "ts", "action", "issue_type", "mode", "domain",
    "detection_source", "suppressed_reason",
)
_SESSION_TURN_COLUMNS = (
    "id", "session_id", "ts", "turn_index", "issue_type",
    "domain", "severity", "confidence", "shadow_only",
)
_SESSION_NUDGE_COLUMNS = (
    "session_id", "issue_type", "ts",
)


def import_observation_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    """Insert an observation row. Returns True if inserted, False if dedup
    or required fields missing."""
    init_db(platform, profile)
    # Validate required NOT NULL fields before insert; fall back on sane
    # defaults for recoverable gaps.
    if not row.get("session_id") or not row.get("domain"):
        return False
    obs_id = row.get("id") or str(uuid.uuid4())
    # Fill missing optional NOT-NULL fields with reasonable defaults so
    # imports from slightly different schema versions still work.
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("shadow_only", 0)
    values = tuple(row.get(c) for c in _OBS_COLUMNS[1:])
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT id FROM observations WHERE id=?", (obs_id,)
        ).fetchone()
        if existing:
            return False
        placeholders = ",".join(["?"] * len(_OBS_COLUMNS))
        try:
            con.execute(
                f"INSERT INTO observations ({','.join(_OBS_COLUMNS)}) "
                f"VALUES ({placeholders})",
                (obs_id,) + values,
            )
        except sqlite3.IntegrityError:
            return False
    return True


def import_intervention_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    init_db(platform, profile)
    row = dict(row)
    row.setdefault("id", str(uuid.uuid4()))
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    values = tuple(row.get(c) for c in _INTERVENTION_COLUMNS)
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT id FROM intervention_events WHERE id=?",
            (row["id"],),
        ).fetchone()
        if existing:
            return False
        placeholders = ",".join(["?"] * len(_INTERVENTION_COLUMNS))
        con.execute(
            f"INSERT INTO intervention_events ({','.join(_INTERVENTION_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )
    return True


def import_decision_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    init_db(platform, profile)
    row = dict(row)
    row.setdefault("id", str(uuid.uuid4()))
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("mode", "light")
    row.setdefault("domain", Domain.OTHER.value)
    row.setdefault("detection_source", "rules")
    if not row.get("action"):
        return False
    values = tuple(row.get(c) for c in _DECISION_COLUMNS)
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT id FROM decision_events WHERE id=?",
            (row["id"],),
        ).fetchone()
        if existing:
            return False
        placeholders = ",".join(["?"] * len(_DECISION_COLUMNS))
        con.execute(
            f"INSERT INTO decision_events ({','.join(_DECISION_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )
    return True


def import_session_turn_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    init_db(platform, profile)
    row = dict(row)
    row.setdefault("id", str(uuid.uuid4()))
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("shadow_only", 0)
    if not row.get("session_id"):
        return False
    values = tuple(row.get(c) for c in _SESSION_TURN_COLUMNS)
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT id FROM session_turns WHERE id=?",
            (row["id"],),
        ).fetchone()
        if existing:
            return False
        placeholders = ",".join(["?"] * len(_SESSION_TURN_COLUMNS))
        con.execute(
            f"INSERT INTO session_turns ({','.join(_SESSION_TURN_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )
    return True


def import_session_nudge_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    init_db(platform, profile)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    if not row.get("session_id") or not row.get("issue_type"):
        return False
    values = tuple(row.get(c) for c in _SESSION_NUDGE_COLUMNS)
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT 1 FROM session_nudge_log WHERE session_id=? AND issue_type=?",
            (row["session_id"], row["issue_type"]),
        ).fetchone()
        if existing:
            return False
        placeholders = ",".join(["?"] * len(_SESSION_NUDGE_COLUMNS))
        con.execute(
            f"INSERT INTO session_nudge_log ({','.join(_SESSION_NUDGE_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )
    return True


def repair_pattern_state(
    platform: Platform | None = None,
    profile: str | None = None,
) -> None:
    with _conn(platform, profile, immediate=True) as con:
        _rebuild_pattern_state_locked(con)
        _set_schema_version_locked(con, SCHEMA_VERSION)


# ── Session-level short-term memory ───────────────────────────────────────
#
# Unlike the cross-session `patterns` table, `session_turns` stores per-turn
# classifications within a single session so the policy can react to things
# like "3 of the last 5 turns had the same issue". No raw text is stored —
# only the structured signals (issue_type, domain, severity, confidence).
# A companion `session_nudge_log` table prevents repeating the same
# session-level nudge twice within one session.


def record_session_turn(
    session_id: str,
    issue_type: IssueType | None,
    domain: Domain,
    *,
    severity: float = 0.0,
    confidence: float = 0.0,
    shadow_only: bool = False,
    platform: Platform | None = None,
    profile: str | None = None,
) -> str:
    """Record one per-session turn. Called from `record_observation` so
    every observation also contributes to the short-term pattern store.
    Uses BEGIN IMMEDIATE because turn_index is computed via SELECT MAX()
    + 1 and two concurrent writes would otherwise race to the same index."""
    turn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn(platform, profile, immediate=True) as con:
        row = con.execute(
            "SELECT MAX(turn_index) as mx FROM session_turns WHERE session_id=?",
            (session_id,),
        ).fetchone()
        next_idx = ((row["mx"] if row and row["mx"] is not None else 0) + 1)
        con.execute(
            """INSERT INTO session_turns
               (id, session_id, ts, turn_index, issue_type, domain,
                severity, confidence, shadow_only)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                turn_id, session_id, now, next_idx,
                issue_type.value if issue_type else None,
                domain.value if domain else None,
                severity, confidence,
                1 if shadow_only else 0,
            ),
        )
    return turn_id


def get_session_pattern(
    session_id: str,
    *,
    window: int = 5,
    min_repeat: int = 3,
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Look at the most recent `window` turns of `session_id`. If at least
    `min_repeat` of them share the same v1-visible issue_type, return a
    summary describing that short-term pattern.

    Returns None when:
      - Fewer than `min_repeat` turns exist in the window
      - No issue_type reaches `min_repeat` within the window
      - The most-frequent issue_type is a shadow-only class (missing_context
        etc.) — v1 never surfaces those

    Shadow-only turns are excluded from the count so shadow observations
    don't dominate and mask real v1 signals.
    """
    from .taxonomy import V1_VISIBLE_ISSUES
    with _conn(platform, profile) as con:
        rows = con.execute(
            """SELECT issue_type, turn_index, domain FROM session_turns
               WHERE session_id=? AND shadow_only=0
               ORDER BY turn_index DESC LIMIT ?""",
            (session_id, window),
        ).fetchall()
    if len(rows) < min_repeat:
        return None
    issue_counts: dict[str, int] = {}
    issue_turns: dict[str, list[int]] = {}
    issue_domains: dict[str, str] = {}
    for r in rows:
        it = r["issue_type"]
        if it is None:
            continue
        issue_counts[it] = issue_counts.get(it, 0) + 1
        issue_turns.setdefault(it, []).append(r["turn_index"])
        issue_domains.setdefault(it, r["domain"] or "other")
    if not issue_counts:
        return None
    top_issue, count = max(issue_counts.items(), key=lambda kv: kv[1])
    if count < min_repeat:
        return None
    if top_issue not in {i.value for i in V1_VISIBLE_ISSUES}:
        return None
    return {
        "issue_type": top_issue,
        "domain": issue_domains[top_issue],
        "count": count,
        "window_size": len(rows),
        "turn_indices": sorted(issue_turns[top_issue]),
    }


def has_session_been_nudged(
    session_id: str,
    issue_type: IssueType,
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    """True if this (session, issue_type) has already received a
    session-level nudge — prevents repeating within one session."""
    with _conn(platform, profile) as con:
        row = con.execute(
            "SELECT ts FROM session_nudge_log WHERE session_id=? AND issue_type=?",
            (session_id, issue_type.value),
        ).fetchone()
    return row is not None


def mark_session_nudged(
    session_id: str,
    issue_type: IssueType,
    platform: Platform | None = None,
    profile: str | None = None,
) -> None:
    """Record that a session-level nudge was shown for (session, issue_type).
    Idempotent — if already marked, the INSERT OR IGNORE is a no-op."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn(platform, profile, immediate=True) as con:
        con.execute(
            "INSERT OR IGNORE INTO session_nudge_log (session_id, issue_type, ts) "
            "VALUES (?, ?, ?)",
            (session_id, issue_type.value, now),
        )


def prune_old_observations(
    days: int = 180,
    platform: Platform | None = None,
    profile: str | None = None,
) -> int:
    """Delete observations older than `days` whose associated pattern is
    no longer active. Keeps the DB bounded.

    Strategy: delete observations older than N days that belong to
    issue_types whose best pattern row is in 'resolved' or 'archived'
    status. Active patterns keep their full observation history so
    explanation chains and evidence counts stay accurate.

    Observations with issue_type=None (clean turns) are also eligible
    for pruning since they don't contribute to any pattern.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn(platform, profile, immediate=True) as con:
        # Collect non-active issue types
        rows = con.execute(
            "SELECT DISTINCT issue_type FROM patterns "
            "WHERE status IN ('resolved', 'archived')"
        ).fetchall()
        cold_issues = [r["issue_type"] for r in rows]

        total_deleted = 0
        # Prune cold-pattern observations
        if cold_issues:
            placeholders = ",".join("?" * len(cold_issues))
            c = con.execute(
                f"DELETE FROM observations WHERE ts < ? "
                f"AND issue_type IN ({placeholders})",
                (cutoff, *cold_issues),
            )
            total_deleted += c.rowcount

        # Prune clean-turn observations (issue_type=NULL) unconditionally
        # past the TTL — they never contribute to patterns.
        c2 = con.execute(
            "DELETE FROM observations WHERE ts < ? AND issue_type IS NULL",
            (cutoff,),
        )
        total_deleted += c2.rowcount
    return total_deleted


def prune_old_session_turns(
    days: int = 7,
    platform: Platform | None = None,
    profile: str | None = None,
) -> int:
    """Delete session_turns older than `days` to keep the short-term
    store bounded. Session-level signals have no value once the session
    has moved on; and the long-term patterns table is the durable
    aggregate. Returns rows deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn(platform, profile, immediate=True) as con:
        c = con.execute("DELETE FROM session_turns WHERE ts < ?", (cutoff,))
        con.execute(
            "DELETE FROM session_nudge_log WHERE ts < ?", (cutoff,)
        )
    return c.rowcount


def import_pattern_row(
    row: dict[str, Any],
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    """Insert a pattern row. Returns True if inserted, False if dedup."""
    init_db(platform, profile)
    issue = row.get("issue_type")
    domain = row.get("domain", "other")
    if not issue:
        return False
    with _conn(platform, profile) as con:
        existing = con.execute(
            "SELECT issue_type FROM patterns WHERE issue_type=? AND domain=?",
            (issue, domain),
        ).fetchone()
        if existing:
            return False
        values = tuple(row.get(c) for c in _PATTERN_COLUMNS)
        placeholders = ",".join(["?"] * len(_PATTERN_COLUMNS))
        con.execute(
            f"INSERT INTO patterns ({','.join(_PATTERN_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )
    return True
