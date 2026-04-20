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
  coach record-observation                (reads JSON from stdin)
  coach select-action                     (reads JSON from stdin)
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
            print(f"Prompt 教练已开启（light 模式）。观察期截止：{period_str}")
        else:
            print(f"Prompt Coach enabled (light mode). Observation period ends: {period_str}")
    memory.init_db(profile=args.profile)


def cmd_disable(args: argparse.Namespace) -> None:
    settings.disable(profile=args.profile)
    print(templates.coach_off_ack(_lang_marker(args)))


def cmd_status(args: argparse.Namespace) -> None:
    cfg = settings.load(profile=args.profile)
    p = settings.observation_period_ends_at(profile=args.profile)
    period_str = p.strftime("%Y-%m-%d") if p else None
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


def cmd_record_observation(args: argparse.Namespace) -> None:
    cfg = settings.load(profile=args.profile)
    # mode=off → zero persistence (no observation, no stats event).
    if cfg["mode"] == CoachMode.OFF.value:
        print(json.dumps({"status": "skipped", "reason": "mode=off"}))
        return

    data = serializers.read_stdin_json()
    try:
        domain = Domain(data.get("domain", "other"))
        issue_type = IssueType(data["issue_type"]) if data.get("issue_type") else None
        action = Action(data.get("action_taken", "none"))
        cost_signal = CostSignal(data.get("cost_signal", "none"))
    except (ValueError, KeyError) as e:
        print(json.dumps({"status": "error", "reason": str(e)}))
        sys.exit(1)

    # memory_enabled=false → don't persist observations. Emit a single
    # suppressed intervention_event (content-free) so aggregate stats still
    # reflect that coaching was active, but nothing about the user is stored.
    if not cfg["memory_enabled"]:
        memory.record_intervention(
            issue_type=None,
            surface=action.value if action else "none",
            shown=False,
            suppressed_reason="memory_disabled",
            profile=args.profile,
        )
        print(json.dumps({"status": "skipped", "reason": "memory_disabled"}))
        return

    session_id = data.get("session_id")
    if not session_id:
        # Generate a per-process fallback so distinct_sessions isn't inflated
        # across calls but also doesn't collapse multiple real sessions into
        # the literal string "unknown".
        import uuid as _uuid
        session_id = f"anon-{_uuid.uuid4().hex[:12]}"
        sys.stderr.write(
            "warning: record-observation missing session_id; generated a "
            f"one-shot id ({session_id}). Agents should pass a stable "
            "per-conversation session_id.\n"
        )

    obs_id = memory.record_observation(
        session_id=session_id,
        domain=domain,
        issue_type=issue_type,
        action_taken=action,
        severity=data.get("severity", 0.0),
        confidence=data.get("confidence", 0.0),
        fixability=data.get("fixability", 0.0),
        cost_signal=cost_signal,
        evidence_summary=data.get("evidence_summary", ""),
        task_type=data.get("task_type"),
        profile=args.profile,
        shadow_only=data.get("shadow_only", False),
        sensitive_logging_enabled=cfg.get("sensitive_logging_enabled", False),
    )

    # Record intervention_events for every visible coaching surface so
    # annoyance budgets (get_proactive_count_7d / get_retrospective_count_7d)
    # reflect actual shown tips. Without this, the 7-day budget is effectively
    # unbounded. Shadow observations never count — they were never shown.
    shadow_obs = data.get("shadow_only", False)
    if shadow_obs:
        print(json.dumps({"status": "ok", "observation_id": obs_id}))
        return

    if action == Action.RETROSPECTIVE_REMINDER and issue_type is not None:
        chain = memory.build_explanation_chain(issue_type, profile=args.profile)
        if chain is not None:
            memory.record_intervention(
                issue_type=issue_type,
                surface="retrospective_reminder",
                shown=True,
                explanation_chain=chain,
                profile=args.profile,
            )
            memory.mark_pattern_notified(
                issue_type=issue_type,
                domain=domain,
                profile=args.profile,
            )
    elif action in (Action.POST_ANSWER_TIP, Action.PRE_ANSWER_MICRO_NUDGE):
        memory.record_intervention(
            issue_type=issue_type,
            surface=action.value,
            shown=True,
            profile=args.profile,
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
            profile=args.profile,
        )
        if session_id:
            memory.mark_session_nudged(
                session_id=session_id,
                issue_type=issue_type,
                profile=args.profile,
            )

    print(json.dumps({"status": "ok", "observation_id": obs_id}))


def cmd_select_action(args: argparse.Namespace) -> None:
    """Read prompt text from stdin JSON and return recommended action.

    If `agent_classification` is provided, the agent's context-aware
    judgment is used as the primary detection source (rules become a
    fallback). This lets agents who see full conversation context catch
    issues that single-prompt regex can't see, and conversely suppress
    regex false-positives that look fine in context.
    """
    data = serializers.read_stdin_json()
    prompt_text = data.get("text", "")
    session_id = data.get("session_id")
    agent_cls = data.get("agent_classification")

    cfg = settings.load(profile=args.profile)
    mode = CoachMode(cfg.get("mode", CoachMode.OFF.value))

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

    # Short-term session pattern: only relevant when memory is on AND the
    # caller supplied a session_id so we can look up this session's turns.
    session_pat: SessionPatternSummary | None = None
    session_already_nudged = False
    if cfg["memory_enabled"] and session_id:
        raw_pat = memory.get_session_pattern(session_id, profile=args.profile)
        if raw_pat:
            try:
                sp_issue = IssueType(raw_pat["issue_type"])
                session_pat = SessionPatternSummary(
                    issue_type=sp_issue,
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

    out = {
        "action": result.action.value,
        "issue_type": result.issue_type.value if result.issue_type else None,
        "reason": result.reason,
        "suppressed_reason": result.suppressed_reason,
        "coaching_text": coaching_text,
        "domain": detection.domain.value,
        "is_sensitive": detection.is_sensitive,
        "detection_source": detection_source,  # "agent" | "rules"
    }
    if agent_cls_error:
        # Surface agent_classification errors in stdout JSON so the
        # caller can see exactly why it fell back to rules (stderr
        # warnings are often discarded by subprocess callers).
        out["agent_cls_error"] = agent_cls_error
    serializers.write_stdout_json(out)


def cmd_update_patterns(args: argparse.Namespace) -> None:
    memory.apply_weekly_decay(profile=args.profile)
    print(json.dumps({"status": "ok", "action": "decay_applied"}))


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
    or a pattern row (has "_type": "pattern"). Duplicates by id are skipped.
    """
    raw = sys.stdin.read()
    imported_obs = 0
    imported_patterns = 0
    skipped = 0
    errors: list[str] = []
    if not raw.strip():
        print(json.dumps({
            "status": "ok",
            "imported_observations": 0,
            "imported_patterns": 0,
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

        if row.get("_type") == "pattern":
            ok = memory.import_pattern_row(row, profile=args.profile)
            if ok:
                imported_patterns += 1
            else:
                skipped += 1
        else:
            ok = memory.import_observation_row(row, profile=args.profile)
            if ok:
                imported_obs += 1
            else:
                skipped += 1

    print(json.dumps({
        "status": "ok" if not errors else "partial",
        "imported_observations": imported_obs,
        "imported_patterns": imported_patterns,
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
    parser = argparse.ArgumentParser(
        prog="coach",
        description="User Capability Coach CLI",
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

    sub.add_parser("record-observation", help="Record an observation (JSON from stdin)")
    sub.add_parser("select-action", help="Select action for a prompt (JSON from stdin)")
    sub.add_parser("update-patterns", help="Apply pattern decay")
    sub.add_parser("show-patterns", help="Show all pattern cards")

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
        "forget-pattern": cmd_forget_pattern,
        "forget-all": cmd_forget_all,
        "why-reminded": cmd_why_reminded,
        "dismiss": cmd_dismiss,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
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
