#!/usr/bin/env python3
"""Command-line entry point for the User Capability Coach.

Usage:
  coach enable
  coach disable
  coach status
  coach set-mode <off|light|standard|strict>
  coach set-memory <on|off>
  coach set-sensitive-logging <on|off>    (opt into sensitive-domain logging)
  coach dismiss                           (user said "don't remind me")
  coach record-observation                (CLI flags, or JSON from stdin)
  coach select-action --text "..." --session-id "..."   (preferred: auto-records)
  coach select-action                     (power user: JSON from stdin)
  coach doctor                            (self-diagnostic)
  coach update-patterns
  coach show-patterns
  coach forget-pattern <issue_type> [domain]
  coach forget-all
  coach why-reminded
  coach memory export
  coach memory import                     (reads JSONL from stdin)
  coach install --platform <claude-code|codex|all>
  coach uninstall --platform <claude-code|codex|all>

Output language: most commands auto-detect from the LANG env var
(starts with 'zh' → Chinese, else English). Pass --lang zh|en to override.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import (  # noqa: E402
    memory, settings, templates, policy, serializers,
)
from tools.taxonomy import (
    IssueType, Domain, Action, CoachMode, CostSignal, PatternStatus,
)
from tools.policy import PolicyInput, PatternSummary, SessionPatternSummary
from tools.detectors import detect, build_detection_from_agent
from tools.time_utils import parse_iso_datetime_utc


def _lang_marker(args: argparse.Namespace) -> str:
    """Return a short string whose language hints Chinese vs English.

    Used as `prompt_text` for template functions that sniff language via
    CJK character presence. Resolution order:
      1. --lang argument (zh/en)
      2. COACH_LANG env var (zh/en)
      3. LANG env var prefix (zh* → Chinese)
      4. Default: English ("")
    """
    lang = getattr(args, "lang", None)
    if not lang:
        lang = os.environ.get("COACH_LANG", "")
    if not lang:
        env_lang = os.environ.get("LANG", "")
        if env_lang.lower().startswith("zh"):
            lang = "zh"
    if lang and lang.lower().startswith("zh"):
        return "中文"
    return ""


def cmd_enable(args: argparse.Namespace) -> None:
    cfg = settings.enable(profile=args.profile)
    p = settings.observation_period_ends_at(profile=args.profile)
    period_str = p.strftime("%Y-%m-%d") if p else "N/A"
    lang_hint = _lang_marker(args)
    if not cfg.get("first_use_disclosed"):
        print(templates.first_use_disclosure(lang_hint))
        settings.mark_first_use_disclosed(profile=args.profile)
    else:
        if lang_hint:
            if cfg.get("memory_enabled"):
                print(f"Prompt 教练已开启（light 模式）。观察期截止：{period_str}")
            else:
                print("Prompt 教练已开启（light 模式）。长期记忆仍是关闭。")
        else:
            if cfg.get("memory_enabled"):
                print(f"Prompt Coach enabled (light mode). Observation period ends: {period_str}")
            else:
                print("Prompt Coach enabled (light mode). Long-term memory is off.")
    memory.init_db(profile=args.profile)


def cmd_disable(args: argparse.Namespace) -> None:
    settings.disable(profile=args.profile)
    print(templates.coach_off_ack(_lang_marker(args)))


def cmd_status(args: argparse.Namespace) -> None:
    cfg = settings.load(profile=args.profile)
    p = settings.observation_period_ends_at(profile=args.profile)
    period_str = p.strftime("%Y-%m-%d") if p else None
    decision_counts = memory.get_decision_counts_7d(profile=args.profile)
    count_pro = memory.get_proactive_count_7d(profile=args.profile)
    count_ret = memory.get_retrospective_count_7d(profile=args.profile)

    # Compute dismissed_until if within the 7-day window
    dismissed_until_str = None
    if settings.is_recently_dismissed(profile=args.profile):
        raw = cfg.get("last_dismissed_at")
        dismissed_at = parse_iso_datetime_utc(raw)
        if dismissed_at is not None:
            from datetime import timedelta as _td
            until = dismissed_at + _td(days=settings.DISMISSAL_WINDOW_DAYS)
            dismissed_until_str = until.strftime("%Y-%m-%d")

    print(templates.coach_status(
        mode=cfg["mode"],
        memory_enabled=cfg["memory_enabled"],
        observation_ends=period_str,
        checked_7d=decision_counts["checked"],
        surfaced_7d=decision_counts["surfaced"],
        silent_7d=decision_counts["silent"],
        proactive_7d=count_pro,
        retro_7d=count_ret,
        prompt_text=_lang_marker(args),
        dismissed_until=dismissed_until_str,
    ))


def cmd_set_mode(args: argparse.Namespace) -> None:
    # argparse already validates choices, so ValueError here is unreachable
    # via normal CLI use but kept as defense-in-depth for direct dispatch.
    mode = CoachMode(args.mode)
    settings.set_mode(mode, profile=args.profile)
    print(f"Mode set to: {mode.value}")


def cmd_set_memory(args: argparse.Namespace) -> None:
    enabled = args.value.lower() in ("on", "true", "1", "yes")
    settings.set_memory(enabled, profile=args.profile)
    print(f"Memory: {'enabled' if enabled else 'disabled'}")


def cmd_set_sensitive_logging(args: argparse.Namespace) -> None:
    """Opt into training on sensitive-domain prompts. Default: off.

    This is a privacy-sensitive setting: enabling it means prompts
    classified as sensitive (health, legal, emergency, mental distress,
    etc.) will have their issue_type and short evidence summary stored in
    the local DB. Default is OFF per plan.md §安全与隐私约束.
    """
    enabled = args.value.lower() in ("on", "true", "1", "yes")
    cfg = settings.load(profile=args.profile)
    cfg["sensitive_logging_enabled"] = enabled
    settings.save(cfg, profile=args.profile)
    state = "enabled" if enabled else "disabled"
    print(f"Sensitive-domain logging: {state}")


def _ensure_session_id(session_id: str | None, *, cmd: str) -> str:
    """Return a usable session_id, generating a fallback + stderr warning.

    Callers should prefer passing a stable per-conversation id so
    `distinct_sessions` accounting is correct. The fallback is
    non-reusable across process invocations by design — better to inflate
    nothing than to collapse real sessions into a literal "unknown"."""
    if session_id:
        return session_id
    import uuid as _uuid
    generated = f"anon-{_uuid.uuid4().hex[:12]}"
    sys.stderr.write(
        f"warning: {cmd} missing session_id; generated a one-shot id "
        f"({generated}). Agents should pass a stable per-conversation "
        "session_id.\n"
    )
    return generated


def _persist_observation_core(
    *,
    profile: str | None,
    cfg: dict,
    session_id: str,
    domain: Domain,
    issue_type: IssueType | None,
    action: Action,
    severity: float,
    confidence: float,
    fixability: float,
    cost_signal: CostSignal,
    evidence_summary: str,
    task_type: str | None = None,
    shadow_only: bool = False,
) -> tuple[dict, str | None]:
    """Write observation + (optionally) intervention rows for a decision.

    Single authoritative place for observation persistence. Used by:
    - `cmd_record_observation` (explicit JSON / CLI-flag call)
    - `cmd_select_action` --text path (auto-record)

    Returns `(status_dict, observation_id | None)`. `status_dict` is ready
    to be serialized to stdout for the record-observation subcommand;
    callers that merge the observation into a larger response can read
    `observation_id` directly.
    """
    # memory_enabled=false → don't persist observations. Emit a single
    # short-term session turn so session nudges still work, but do not
    # write longitudinal observations/patterns.
    if not cfg["memory_enabled"]:
        session_issue_type = issue_type
        session_shadow_only = shadow_only
        if domain == Domain.SENSITIVE and not cfg.get("sensitive_logging_enabled", False):
            session_issue_type = None
            session_shadow_only = True
        if action not in (Action.RETROSPECTIVE_REMINDER, Action.SESSION_PATTERN_NUDGE):
            memory.record_session_turn(
                session_id=session_id,
                issue_type=session_issue_type,
                domain=domain,
                severity=severity,
                confidence=confidence,
                shadow_only=session_shadow_only,
                profile=profile,
            )
        if action in (Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE):
            memory.record_intervention(
                issue_type=issue_type,
                surface=action.value,
                shown=True,
                profile=profile,
            )
        elif action == Action.SESSION_PATTERN_NUDGE and issue_type is not None:
            memory.record_intervention(
                issue_type=issue_type,
                surface=action.value,
                shown=True,
                profile=profile,
            )
            memory.mark_session_nudged(
                session_id=session_id,
                issue_type=issue_type,
                profile=profile,
            )
        return ({"status": "skipped", "reason": "memory_disabled"}, None)

    obs_id = memory.record_observation(
        session_id=session_id,
        domain=domain,
        issue_type=issue_type,
        action_taken=action,
        severity=severity,
        confidence=confidence,
        fixability=fixability,
        cost_signal=cost_signal,
        evidence_summary=evidence_summary,
        task_type=task_type,
        profile=profile,
        shadow_only=shadow_only,
        sensitive_logging_enabled=cfg.get("sensitive_logging_enabled", False),
    )

    # Record intervention_events for every visible coaching surface so
    # annoyance budgets (get_proactive_count_7d / get_retrospective_count_7d)
    # reflect actual shown tips. Without this, the 7-day budget is effectively
    # unbounded. Shadow observations never count — they were never shown.
    if shadow_only:
        return ({"status": "ok", "observation_id": obs_id}, obs_id)

    if action == Action.RETROSPECTIVE_REMINDER and issue_type is not None:
        chain = memory.build_explanation_chain(
            issue_type,
            domain=domain,
            profile=profile,
        )
        if chain is not None:
            memory.record_intervention(
                issue_type=issue_type,
                surface="retrospective_reminder",
                shown=True,
                explanation_chain=chain,
                profile=profile,
            )
            memory.mark_pattern_notified(
                issue_type=issue_type,
                domain=domain,
                profile=profile,
            )
    elif action in (Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE):
        memory.record_intervention(
            issue_type=issue_type,
            surface=action.value,
            shown=True,
            profile=profile,
        )
    elif action == Action.SESSION_PATTERN_NUDGE and issue_type is not None:
        # Record the intervention AND mark the (session, issue_type) pair
        # so we don't fire the same session-level nudge twice in one
        # session. Session-level nudges don't consume the proactive or
        # retrospective 7-day budgets — they have their own
        # once-per-session-per-issue cap.
        memory.record_intervention(
            issue_type=issue_type,
            surface=action.value,
            shown=True,
            profile=profile,
        )
        memory.mark_session_nudged(
            session_id=session_id,
            issue_type=issue_type,
            profile=profile,
        )

    return ({"status": "ok", "observation_id": obs_id}, obs_id)


def _auto_record_observation(
    *,
    profile: str | None,
    cfg: dict,
    session_id: str | None,
    detection,
    action: Action,
    issue_type: IssueType | None,
    out_domain: str,
) -> str | None:
    """Internal: called from the select-action --text path to persist an
    observation immediately after a decision. Pulls severity/confidence/
    fixability from the DetectorResult whose issue_type matches the
    chosen action; falls back to 0.0 when no matching candidate exists.
    """
    # Pick the DetectorResult matching the decided issue_type. If nothing
    # matches (e.g., policy fired retrospective_reminder from top_pattern
    # with no current-turn candidate, or action=none/silent_rewrite), use
    # the first candidate or zeros.
    sev = conf = fix = 0.0
    cost = CostSignal.NONE
    if detection.candidates:
        match = next(
            (c for c in detection.candidates if c.issue_type == issue_type),
            detection.candidates[0],
        )
        sev, conf, fix, cost = match.severity, match.confidence, match.fixability, match.cost_signal

    sid = _ensure_session_id(session_id, cmd="select-action --text auto-record")
    status, obs_id = _persist_observation_core(
        profile=profile,
        cfg=cfg,
        session_id=sid,
        domain=Domain(out_domain),
        issue_type=issue_type,
        action=action,
        severity=sev,
        confidence=conf,
        fixability=fix,
        cost_signal=cost,
        evidence_summary="",
        shadow_only=False,
    )
    return obs_id


def cmd_record_observation(args: argparse.Namespace) -> None:
    cfg = settings.load(profile=args.profile)
    # mode=off → zero persistence (no observation, no stats event).
    if cfg["mode"] == CoachMode.OFF.value:
        print(json.dumps({"status": "skipped", "reason": "mode=off"}))
        return

    # CLI-flag mode: any of --text/--action/--issue/--domain provided.
    # Otherwise fall back to reading JSON from stdin (backward-compat).
    cli_mode = any(
        getattr(args, attr, None) is not None
        for attr in ("text", "action_taken", "issue_type", "domain",
                     "severity", "confidence", "fixability",
                     "cost_signal", "evidence_summary")
    ) or getattr(args, "shadow", False)

    if cli_mode:
        try:
            domain_val = getattr(args, "domain", None) or "other"
            issue_raw = getattr(args, "issue_type", None)
            action_raw = getattr(args, "action_taken", None) or "none"
            cost_raw = getattr(args, "cost_signal", None) or "none"
            domain = Domain(domain_val)
            issue_type = IssueType(issue_raw) if issue_raw else None
            action = Action(action_raw)
            cost_signal = CostSignal(cost_raw)
        except ValueError as e:
            print(json.dumps({"status": "error", "reason": str(e)}))
            sys.exit(1)

        # If --text is provided and issue_type/domain aren't, run detection
        # to fill them in. This makes "coach record-observation --text X
        # --session-id Y" a zero-config post-hoc recorder.
        text = getattr(args, "text", None)
        det_severity = det_confidence = det_fixability = None
        if text and issue_raw is None:
            det = detect(text)
            # Pick the candidate the policy would pick: highest
            # (severity + confidence) sum. Deterministic under ties by
            # list order.
            top = (
                max(det.candidates, key=lambda c: c.severity + c.confidence)
                if det.candidates else None
            )
            if top is not None:
                issue_type = top.issue_type
                det_severity, det_confidence, det_fixability = top.severity, top.confidence, top.fixability
                cost_signal = top.cost_signal
            if not getattr(args, "domain", None):
                domain = det.domain
        severity = (
            args.severity if getattr(args, "severity", None) is not None
            else (det_severity if det_severity is not None else 0.0)
        )
        confidence = (
            args.confidence if getattr(args, "confidence", None) is not None
            else (det_confidence if det_confidence is not None else 0.0)
        )
        fixability = (
            args.fixability if getattr(args, "fixability", None) is not None
            else (det_fixability if det_fixability is not None else 0.0)
        )

        session_id = _ensure_session_id(
            getattr(args, "session_id", None), cmd="record-observation",
        )
        evidence_summary = getattr(args, "evidence_summary", None) or ""
        shadow_only = getattr(args, "shadow", False)
    else:
        data = serializers.read_stdin_json()
        try:
            domain = Domain(data.get("domain", "other"))
            issue_type = IssueType(data["issue_type"]) if data.get("issue_type") else None
            action = Action(data.get("action_taken", "none"))
            cost_signal = CostSignal(data.get("cost_signal", "none"))
        except (ValueError, KeyError) as e:
            print(json.dumps({"status": "error", "reason": str(e)}))
            sys.exit(1)

        session_id = _ensure_session_id(data.get("session_id"), cmd="record-observation")
        severity = data.get("severity", 0.0)
        confidence = data.get("confidence", 0.0)
        fixability = data.get("fixability", 0.0)
        evidence_summary = data.get("evidence_summary", "")
        shadow_only = data.get("shadow_only", False)

    task_type = None if cli_mode else data.get("task_type")

    status, _obs_id = _persist_observation_core(
        profile=args.profile,
        cfg=cfg,
        session_id=session_id,
        domain=domain,
        issue_type=issue_type,
        action=action,
        severity=severity,
        confidence=confidence,
        fixability=fixability,
        cost_signal=cost_signal,
        evidence_summary=evidence_summary,
        task_type=task_type,
        shadow_only=shadow_only,
    )
    print(json.dumps(status))


def cmd_select_action(args: argparse.Namespace) -> None:
    """Decide a coaching action for a user prompt.

    Two invocation modes:

    1. **CLI-arg mode** (preferred, low-friction): pass `--text "..."` and
       optionally `--session-id "..."`. Stdin is NOT read. The observation
       is AUTO-RECORDED after the decision unless `--no-record` is set,
       so one command covers both "pick action" and "persist observation".
       Use `--dry-run` for diagnostics: it returns the same decision JSON
       without writing decisions, observations, session turns, or budget rows.

    2. **Stdin JSON mode** (advanced, backward-compat): pipe a JSON object
       on stdin with fields `text`, `session_id`, and optional
       `agent_classification`. Observation is NOT auto-recorded — the
       caller must separately invoke `record-observation` for long-term
       pattern accumulation.

    If `agent_classification` is provided (via stdin or `--agent-json`),
    the agent's context-aware judgment is used as the primary detection
    source (rules become a fallback). This lets agents who see full
    conversation context catch issues that single-prompt regex can't
    see, and conversely suppress regex false-positives that look fine
    in context.
    """
    cli_mode = getattr(args, "text", None) is not None
    if cli_mode:
        prompt_text = args.text
        session_id = getattr(args, "session_id", None)
        agent_cls = None
        raw_agent = getattr(args, "agent_json", None)
        if raw_agent:
            try:
                agent_cls = json.loads(raw_agent)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"warning: --agent-json not valid JSON: {e}\n")
                agent_cls = None
    else:
        data = serializers.read_stdin_json()
        prompt_text = data.get("text", "")
        session_id = data.get("session_id")
        agent_cls = data.get("agent_classification")

    cfg = settings.load(profile=args.profile)
    mode = CoachMode(cfg.get("mode", CoachMode.OFF.value))
    dry_run = bool(getattr(args, "dry_run", False))

    # Prefer agent's context-aware classification when given. If the
    # payload is malformed (bad enum etc.), warn on stderr and fall
    # through to rules rather than erroring — this keeps the CLI
    # resilient to agent misconfiguration.
    detection_source = "rules"
    agent_cls_error: str | None = None
    detection = None
    if agent_cls is not None:
        if not isinstance(agent_cls, dict):
            agent_cls_error = (
                f"agent_classification must be a JSON object, got "
                f"{type(agent_cls).__name__}"
            )
        else:
            detection = build_detection_from_agent(agent_cls, prompt_text)
            if detection is not None:
                detection_source = "agent"
            else:
                # Only `issue_type` can cause a reject now (domain and
                # cost_signal have soft fallbacks). Surface the valid
                # IssueType values so the agent can fix its call.
                raw_issue = agent_cls.get("issue_type")
                try:
                    if raw_issue:
                        IssueType(raw_issue)
                    agent_cls_error = (
                        "agent_classification rejected (unknown reason)"
                    )
                except ValueError:
                    valid = ",".join(m.value for m in IssueType)
                    agent_cls_error = (
                        f"agent_classification.issue_type={raw_issue!r} "
                        f"is not a valid IssueType (valid: {valid})"
                    )
        if agent_cls_error:
            sys.stderr.write(f"warning: {agent_cls_error}\n")
    if detection is None:
        detection = detect(prompt_text)

    obs_end = settings.observation_period_ends_at(profile=args.profile)
    proactive_7d = memory.get_proactive_count_7d(profile=args.profile)
    retro_7d = memory.get_retrospective_count_7d(profile=args.profile)

    top_pat_raw = memory.get_top_pattern(profile=args.profile)
    top_pat = None
    top_last_notified: datetime | None = None
    if top_pat_raw:
        try:
            notified_raw = top_pat_raw.get("last_notified_at")
            top_last_notified = parse_iso_datetime_utc(notified_raw)
            top_pat = PatternSummary(
                issue_type=IssueType(top_pat_raw["issue_type"]),
                status=PatternStatus(top_pat_raw["status"]),
                evidence_count=top_pat_raw["evidence_count"],
                distinct_sessions=top_pat_raw["distinct_sessions"],
                cost_count=top_pat_raw["cost_count"],
                score=top_pat_raw["score"],
                last_notified_at=top_last_notified,
            )
        except (ValueError, KeyError):
            pass

    # Short-term session pattern: driven by recent session_turns and only
    # needs a session_id so we can inspect the current conversation.
    session_pat: SessionPatternSummary | None = None
    session_already_nudged = False
    if session_id:
        raw_pat = memory.get_session_pattern(session_id, profile=args.profile)
        if raw_pat:
            try:
                sp_issue = IssueType(raw_pat["issue_type"])
                sp_domain = Domain(raw_pat["domain"])
                session_pat = SessionPatternSummary(
                    issue_type=sp_issue,
                    domain=sp_domain,
                    count=raw_pat["count"],
                    window_size=raw_pat["window_size"],
                )
                session_already_nudged = memory.has_session_been_nudged(
                    session_id, sp_issue, profile=args.profile,
                )
            except (ValueError, KeyError):
                session_pat = None

    inp = PolicyInput(
        mode=mode,
        detection=detection,
        memory_enabled=cfg["memory_enabled"],
        observation_period_ends_at=obs_end,
        proactive_count_7d=proactive_7d,
        retrospective_count_7d=retro_7d,
        last_notified_pattern=(
            top_pat.issue_type.value if top_pat and top_last_notified else None
        ),
        last_notified_at=top_last_notified,
        user_dismissed_recently=settings.is_recently_dismissed(profile=args.profile),
        top_pattern=top_pat,
        session_pattern=session_pat,
        session_already_nudged=session_already_nudged,
    )

    result = policy.select_action(inp)

    # Build coaching text
    coaching_text = ""
    if result.action == Action.POST_ANSWER_TIP and result.issue_type:
        coaching_text = templates.post_answer_tip(result.issue_type, mode, prompt_text)
    elif result.action == Action.PRE_ANSWER_MICRO_NUDGE and result.issue_type:
        coaching_text = templates.pre_answer_micro_nudge(result.issue_type, prompt_text)
    elif result.action == Action.RETROSPECTIVE_REMINDER and result.issue_type:
        coaching_text = templates.retrospective_reminder(result.issue_type, prompt_text)
    elif result.action == Action.SESSION_PATTERN_NUDGE and result.issue_type:
        coaching_text = templates.session_pattern_nudge(result.issue_type, prompt_text)

    out_domain = (
        top_pat_raw["domain"]
        if result.action == Action.RETROSPECTIVE_REMINDER and top_pat_raw is not None
        else (
            session_pat.domain.value
            if result.action == Action.SESSION_PATTERN_NUDGE and session_pat is not None
            else detection.domain.value
        )
    )

    if mode != CoachMode.OFF and not dry_run:
        memory.record_decision(
            action=result.action,
            issue_type=result.issue_type,
            mode=mode.value,
            domain=Domain(out_domain),
            detection_source=detection_source,
            suppressed_reason=result.suppressed_reason,
            profile=args.profile,
        )

    # --- Auto-record observation (CLI-arg mode only) ---------------------
    # Stdin-JSON callers opt into the classic two-call flow; --text callers
    # get one-shot "decide + observe" so patterns accumulate without a
    # second CLI hop. mode=off and empty-prompt cases short-circuit here.
    observation_id: str | None = None
    auto_recorded = False
    if (
        cli_mode
        and not getattr(args, "no_record", False)
        and not dry_run
        and mode != CoachMode.OFF
        and prompt_text
    ):
        observation_id = _auto_record_observation(
            profile=args.profile,
            cfg=cfg,
            session_id=session_id,
            detection=detection,
            action=result.action,
            issue_type=result.issue_type,
            out_domain=out_domain,
        )
        auto_recorded = True

    out = {
        "action": result.action.value,
        "issue_type": result.issue_type.value if result.issue_type else None,
        "reason": result.reason,
        "suppressed_reason": result.suppressed_reason,
        "coaching_text": coaching_text,
        "domain": out_domain,
        "is_sensitive": detection.is_sensitive,
        "detection_source": detection_source,  # "agent" | "rules"
        "auto_recorded": auto_recorded,
        "dry_run": dry_run,
    }
    if observation_id:
        out["observation_id"] = observation_id
    if agent_cls_error:
        # Surface agent_classification errors in stdout JSON so the
        # caller can see exactly why it fell back to rules (stderr
        # warnings are often discarded by subprocess callers).
        out["agent_cls_error"] = agent_cls_error
    serializers.write_stdout_json(out)


def cmd_update_patterns(args: argparse.Namespace) -> None:
    memory.apply_weekly_decay(profile=args.profile)
    print(json.dumps({"status": "ok", "action": "decay_applied"}))


def cmd_doctor(args: argparse.Namespace) -> None:
    """Self-diagnostic: runs 3 fixture prompts and verifies DB writes.

    Doesn't modify any existing observation — uses a unique diagnostic
    session_id prefix and cleans up its own rows at the end. Prints a
    human-readable PASS/FAIL summary plus DB totals.
    """
    import subprocess
    import sqlite3
    import time as _time

    profile = args.profile
    cfg = settings.load(profile=profile)
    lang = _lang_marker(args)
    zh = bool(lang)

    def _L(en: str, cn: str) -> str:
        return cn if zh else en

    checks: list[tuple[str, bool, str]] = []

    # Check 1: config loads
    mode = cfg.get("mode", "off")
    mem = cfg.get("memory_enabled", False)
    checks.append((
        _L("config", "配置"),
        mode in ("off", "light", "standard", "strict"),
        _L(f"mode={mode} memory={'on' if mem else 'off'}",
           f"模式={mode} 记忆={'开' if mem else '关'}"),
    ))

    # Check 2: DB is reachable
    try:
        from tools.platform import data_dir as _resolve_data_dir
        db_path = _resolve_data_dir(profile=profile) / "coach.db"
        con = sqlite3.connect(str(db_path))
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        con.close()
        required = {"observations", "patterns", "decision_events",
                    "session_turns", "intervention_events", "preferences"}
        missing = required - tables
        checks.append((
            _L("db schema", "数据库结构"),
            not missing,
            _L(f"tables present: {len(tables)}",
               f"数据表: {len(tables)} 张") if not missing
            else _L(f"missing tables: {missing}", f"缺失表: {missing}"),
        ))
    except Exception as e:
        checks.append((_L("db schema", "数据库结构"), False, str(e)))

    # Check 3: run 3 fixture prompts (requires mode != off)
    ran = False
    tip_count = 0
    decisions_before = 0
    if mode != "off":
        try:
            con = sqlite3.connect(str(db_path))
            decisions_before = con.execute(
                "SELECT COUNT(*) FROM decision_events"
            ).fetchone()[0]
            con.close()

            sid = f"diag-{int(_time.time())}"
            fixtures = [
                _L("Write API docs for this endpoint", "帮我写接口文档"),
                "分析重构方案代码测试上线",
                _L("Convert JSON to CSV with pandas",
                   "用 pandas 把 JSON 转成 CSV"),
            ]
            cmd_base = [sys.executable, __file__]
            if profile:
                cmd_base += ["--profile", profile]

            suppressed_reasons: set[str] = set()
            for text in fixtures:
                r = subprocess.run(
                    cmd_base + ["select-action", "--text", text,
                                "--session-id", sid, "--dry-run"],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    try:
                        j = json.loads(r.stdout)
                        if j.get("coaching_text"):
                            tip_count += 1
                        if j.get("suppressed_reason"):
                            suppressed_reasons.add(j["suppressed_reason"])
                    except json.JSONDecodeError:
                        pass

            con = sqlite3.connect(str(db_path))
            decisions_after = con.execute(
                "SELECT COUNT(*) FROM decision_events"
            ).fetchone()[0]
            con.commit()
            con.close()
            ran = True
            checks.append((
                _L("select-action smoke", "select-action 烟测"),
                decisions_after == decisions_before,
                _L("3 prompts dry-ran with no decision rows written",
                   "3 条提示已 dry-run，未写入决策记录"),
            ))
            # Tip generation PASSES if at least one tip surfaced, OR if
            # tips were deliberately suppressed by an expected reason (budget
            # exhausted / user dismissed / sensitive). Only flag a failure if
            # NO tips AND no known suppression reason — suggesting rules
            # aren't firing at all.
            expected_suppressions = {
                "budget_exhausted", "budget_exhausted+urgent",
                "user_dismissed", "sensitive_domain",
            }
            tip_ok = tip_count > 0 or bool(suppressed_reasons & expected_suppressions)
            if tip_count > 0:
                tip_detail = _L(
                    f"{tip_count}/3 fixtures produced a visible tip",
                    f"{tip_count}/3 条生成了可见提示",
                )
            elif suppressed_reasons & expected_suppressions:
                reasons = ",".join(sorted(suppressed_reasons & expected_suppressions))
                tip_detail = _L(
                    f"0 tips — suppressed by policy ({reasons}); expected when "
                    f"budget spent or user dismissed",
                    f"0 条提示 — 被策略抑制 ({reasons})；预算用完或用户已免打扰时属正常",
                )
            else:
                tip_detail = _L(
                    f"{tip_count}/3 fixtures produced a tip and no suppression reason — "
                    f"rules may not be firing",
                    f"{tip_count}/3 生成提示且无抑制原因 — 规则可能未触发",
                )
            checks.append((
                _L("tip generation", "提示生成"),
                tip_ok,
                tip_detail,
            ))
        except Exception as e:
            checks.append((_L("smoke test", "烟测"), False, str(e)))
    else:
        checks.append((
            _L("smoke test", "烟测"),
            True,
            _L("skipped (mode=off)", "已跳过 (mode=off)"),
        ))

    # Print results
    all_pass = all(ok for _, ok, _ in checks)
    header = _L("Coach Doctor", "Coach 自检") + " — " + \
             (_L("ALL PASS", "全部通过") if all_pass else _L("ISSUES FOUND", "发现问题"))
    print(header)
    print("=" * len(header))
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}: {detail}")
    if not all_pass:
        sys.exit(1)


def cmd_prune(args: argparse.Namespace) -> None:
    """Prune stale observations and session turns without triggering decay.

    Useful for manual housekeeping or scheduled data-hygiene jobs that
    should not advance the weekly decay clock. Runs both prune passes
    (session turns and long-term observations) and reports rows deleted.
    """
    deleted_turns = memory.prune_old_session_turns(
        days=args.days_turns,
        profile=args.profile,
    )
    deleted_obs = memory.prune_old_observations(
        days=args.days_obs,
        profile=args.profile,
    )
    print(json.dumps({
        "status": "ok",
        "deleted_session_turns": deleted_turns,
        "deleted_observations": deleted_obs,
        "days_turns": args.days_turns,
        "days_obs": args.days_obs,
    }))


def cmd_show_patterns(args: argparse.Namespace) -> None:
    patterns = memory.get_all_patterns(profile=args.profile)
    serializers.write_stdout_json({"patterns": patterns})


def cmd_forget_pattern(args: argparse.Namespace) -> None:
    zh = bool(_lang_marker(args))
    try:
        issue = IssueType(args.issue_type)
    except ValueError:
        valid = ", ".join(i.value for i in IssueType)
        if zh:
            print(
                f"未知 issue_type: {args.issue_type!r}\n合法取值：{valid}",
                file=sys.stderr,
            )
        else:
            print(
                f"Unknown issue_type: {args.issue_type!r}\nValid values: {valid}",
                file=sys.stderr,
            )
        sys.exit(1)
    domain = None
    if args.domain:
        try:
            domain = Domain(args.domain)
        except ValueError:
            valid_d = ", ".join(d.value for d in Domain)
            if zh:
                print(
                    f"未知 domain: {args.domain!r}\n合法取值：{valid_d}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Unknown domain: {args.domain!r}\nValid values: {valid_d}",
                    file=sys.stderr,
                )
            sys.exit(1)
    n = memory.forget_pattern(issue, domain, profile=args.profile)
    print(json.dumps({"status": "ok", "deleted_pattern_rows": n}))


def cmd_forget_all(args: argparse.Namespace) -> None:
    memory.forget_all(profile=args.profile)
    print(json.dumps({"status": "ok", "action": "all_data_deleted"}))


def cmd_dismiss(args: argparse.Namespace) -> None:
    """Called by the skill when the user says "don't remind me" / "少教育我".

    Records a timestamp that downgrades all coaching to silent_rewrite for
    the next 7 days, without actually switching mode off.
    """
    settings.record_dismissal(profile=args.profile)
    lang = _lang_marker(args)
    if lang:
        print("已记录：近期不再主动提示。7 天内默认静默。如想完全关闭请用 /coach off。")
    else:
        print("Noted — I'll stay silent for the next 7 days. Use /coach off to fully disable.")


def cmd_why_reminded(args: argparse.Namespace) -> None:
    lang_hint = _lang_marker(args)
    ev = memory.get_last_why_reminded(profile=args.profile)
    if ev is None:
        print("最近没有触发提醒。" if lang_hint else "No recent coaching reminder found.")
        return
    chain = ev.get("explanation_chain") or {}
    print(templates.why_reminded(chain, prompt_text=lang_hint))


def cmd_memory_export(args: argparse.Namespace) -> None:
    print(memory.export_jsonl(profile=args.profile))


def cmd_memory_import(args: argparse.Namespace) -> None:
    """Import JSONL exported via `coach memory export`.

    Each line is either an observation row (has session_id, issue_type, ...)
    or a typed state row (`_type`: pattern / intervention_event /
    decision_event / session_turn / session_nudge_log). Duplicates are skipped.
    """
    raw = sys.stdin.read()
    imported_obs = 0
    imported_patterns = 0
    imported_state_rows = 0
    skipped = 0
    errors: list[str] = []
    if not raw.strip():
        print(json.dumps({
            "status": "ok",
            "imported_observations": 0,
            "imported_patterns": 0,
            "imported_state_rows": 0,
            "skipped": 0,
            "errors": [],
        }))
        return

    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {lineno}: {e}")
            continue

        row_type = row.get("_type")
        if row_type == "pattern":
            ok = memory.import_pattern_row(row, profile=args.profile)
            if ok:
                imported_patterns += 1
            else:
                skipped += 1
        elif row_type == "intervention_event":
            ok = memory.import_intervention_row(row, profile=args.profile)
            if ok:
                imported_state_rows += 1
            else:
                skipped += 1
        elif row_type == "decision_event":
            ok = memory.import_decision_row(row, profile=args.profile)
            if ok:
                imported_state_rows += 1
            else:
                skipped += 1
        elif row_type == "session_turn":
            ok = memory.import_session_turn_row(row, profile=args.profile)
            if ok:
                imported_state_rows += 1
            else:
                skipped += 1
        elif row_type == "session_nudge_log":
            ok = memory.import_session_nudge_row(row, profile=args.profile)
            if ok:
                imported_state_rows += 1
            else:
                skipped += 1
        else:
            ok = memory.import_observation_row(row, profile=args.profile)
            if ok:
                imported_obs += 1
            else:
                skipped += 1

    if imported_obs or imported_patterns:
        memory.repair_pattern_state(profile=args.profile)

    print(json.dumps({
        "status": "ok" if not errors else "partial",
        "imported_observations": imported_obs,
        "imported_patterns": imported_patterns,
        "imported_state_rows": imported_state_rows,
        "skipped": skipped,
        "errors": errors,
    }))


def cmd_install(args: argparse.Namespace) -> None:
    platform_arg = getattr(args, "platform", "all")
    script_dir = Path(__file__).parent.parent / "adapters"

    import subprocess
    targets = []
    if platform_arg in ("claude-code", "all"):
        targets.append(script_dir / "claude-code" / "install.sh")
    if platform_arg in ("codex", "all"):
        targets.append(script_dir / "codex" / "install.sh")

    for script in targets:
        if script.exists():
            result = subprocess.run(["bash", str(script)], capture_output=False)
            if result.returncode != 0:
                sys.exit(result.returncode)
        else:
            print(f"Install script not found: {script}", file=sys.stderr)
            sys.exit(1)


def cmd_uninstall(args: argparse.Namespace) -> None:
    platform_arg = getattr(args, "platform", "all")
    script_dir = Path(__file__).parent.parent / "adapters"

    import subprocess
    targets = []
    if platform_arg in ("claude-code", "all"):
        targets.append(script_dir / "claude-code" / "uninstall.sh")
    if platform_arg in ("codex", "all"):
        targets.append(script_dir / "codex" / "uninstall.sh")

    for script in targets:
        if script.exists():
            result = subprocess.run(["bash", str(script)], capture_output=False)
            if result.returncode != 0:
                sys.exit(result.returncode)


def build_parser() -> argparse.ArgumentParser:
    from tools import __version__ as _coach_version
    parser = argparse.ArgumentParser(
        prog="coach",
        description="User Capability Coach CLI",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {_coach_version}",
    )
    parser.add_argument("--profile", default=None, help="Override data directory path")
    parser.add_argument(
        "--lang",
        default=None,
        choices=["zh", "en"],
        help="Force output language (default: auto from LANG env var)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("enable", help="Enable coaching (light mode)")
    sub.add_parser("disable", help="Disable coaching")
    sub.add_parser("status", help="Show current status")

    p_mode = sub.add_parser("set-mode", help="Set coaching mode")
    p_mode.add_argument("mode", choices=["off", "light", "standard", "strict"])

    p_mem = sub.add_parser("set-memory", help="Enable or disable long-term memory")
    p_mem.add_argument("value", choices=["on", "off"])

    p_slog = sub.add_parser(
        "set-sensitive-logging",
        help="Opt into logging for sensitive-domain prompts (default: off)",
    )
    p_slog.add_argument("value", choices=["on", "off"])

    p_rec = sub.add_parser(
        "record-observation",
        help="Record an observation (CLI flags or JSON from stdin)",
    )
    p_rec.add_argument("--session-id", dest="session_id", default=None)
    p_rec.add_argument("--text", default=None, help="Raw prompt text (runs rule-based detection)")
    p_rec.add_argument("--action", dest="action_taken", default=None,
                       choices=[a.value for a in Action])
    p_rec.add_argument("--issue", dest="issue_type", default=None)
    p_rec.add_argument("--domain", default=None)
    p_rec.add_argument("--severity", type=float, default=None)
    p_rec.add_argument("--confidence", type=float, default=None)
    p_rec.add_argument("--fixability", type=float, default=None)
    p_rec.add_argument("--cost-signal", dest="cost_signal", default=None)
    p_rec.add_argument("--evidence", dest="evidence_summary", default=None)
    p_rec.add_argument("--shadow", action="store_true", help="Shadow-only observation (not counted)")

    p_sel = sub.add_parser(
        "select-action",
        help="Select action for a prompt (CLI flags or JSON from stdin)",
    )
    p_sel.add_argument(
        "--text",
        default=None,
        help="Raw prompt text. When provided, stdin is NOT read and the "
             "observation is auto-recorded after decision (one-call flow).",
    )
    p_sel.add_argument(
        "--session-id",
        dest="session_id",
        default=None,
        help="Stable per-conversation id. Required for session-level pattern accounting.",
    )
    p_sel.add_argument(
        "--agent-json",
        dest="agent_json",
        default=None,
        help="Optional agent_classification as JSON string (advanced; "
             "equivalent to passing {\"agent_classification\": {...}} on stdin).",
    )
    p_sel.add_argument(
        "--no-record",
        dest="no_record",
        action="store_true",
        help="Don't auto-record observation (--text mode only). Decision event is still logged.",
    )
    p_sel.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Return the selected action without writing decisions, observations, or budget rows.",
    )
    sub.add_parser("update-patterns", help="Apply pattern decay")
    sub.add_parser("show-patterns", help="Show all pattern cards")

    p_prune = sub.add_parser(
        "prune",
        help="Delete stale observations and session turns (no decay applied)",
    )
    p_prune.add_argument(
        "--days-obs", type=int, default=180,
        help="Delete observations older than N days (default: 180)",
    )
    p_prune.add_argument(
        "--days-turns", type=int, default=7,
        help="Delete session turns older than N days (default: 7)",
    )

    p_forget = sub.add_parser("forget-pattern", help="Delete a pattern")
    p_forget.add_argument("issue_type")
    p_forget.add_argument("domain", nargs="?", default=None)

    sub.add_parser("forget-all", help="Delete all coach data")
    sub.add_parser("why-reminded", help="Show last reminder explanation")
    sub.add_parser(
        "dismiss",
        help='Record a user dismissal ("don\'t remind me") — silences proactive tips for 7 days',
    )

    mem_p = sub.add_parser("memory", help="Memory operations")
    mem_sub = mem_p.add_subparsers(dest="memory_cmd")
    mem_sub.add_parser("export", help="Export data as JSONL")
    mem_sub.add_parser("import", help="Import data from JSONL (stdin)")

    p_install = sub.add_parser("install", help="Install for a platform")
    p_install.add_argument("--platform", default="all", choices=["claude-code", "codex", "all"])

    p_uninst = sub.add_parser("uninstall", help="Uninstall from a platform")
    p_uninst.add_argument("--platform", default="all", choices=["claude-code", "codex", "all"])

    sub.add_parser(
        "doctor",
        help="Run a self-diagnostic: runs 3 test prompts through select-action "
             "and confirms DB writes. Prints PASS/FAIL summary.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "enable": cmd_enable,
        "disable": cmd_disable,
        "status": cmd_status,
        "set-mode": cmd_set_mode,
        "set-memory": cmd_set_memory,
        "set-sensitive-logging": cmd_set_sensitive_logging,
        "record-observation": cmd_record_observation,
        "select-action": cmd_select_action,
        "update-patterns": cmd_update_patterns,
        "show-patterns": cmd_show_patterns,
        "prune": cmd_prune,
        "forget-pattern": cmd_forget_pattern,
        "forget-all": cmd_forget_all,
        "why-reminded": cmd_why_reminded,
        "dismiss": cmd_dismiss,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "doctor": cmd_doctor,
    }

    if args.command == "memory":
        if args.memory_cmd == "export":
            cmd_memory_export(args)
        elif args.memory_cmd == "import":
            cmd_memory_import(args)
        else:
            parser.parse_args(["memory", "--help"])
    elif args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
