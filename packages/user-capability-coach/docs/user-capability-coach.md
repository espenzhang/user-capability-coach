# User Capability Coach — Developer Guide

## Overview

User Capability Coach is a two-skill coaching system for Claude Code and Codex that helps users write clearer prompts — without being annoying about it.

**Key design principles:**
- Default `off` — zero output until user explicitly enables
- Task first, coaching second — always answer the question before giving a tip
- One issue per turn, one tip per turn
- 14-day observation period before any long-term reminders
- All data local, no raw prompt text stored

## Architecture

```
packages/user-capability-coach/
├── tools/                  # Platform-agnostic core (Python)
│   ├── taxonomy.py         # Enums, constants, micro-habits
│   ├── detectors.py        # Heuristic signal detection
│   ├── policy.py           # Action selection logic
│   ├── templates.py        # User-visible text generation
│   ├── memory.py           # SQLite persistence
│   ├── settings.py         # Config read/write
│   ├── platform.py         # Platform detection and path resolution
│   ├── serializers.py      # Safe JSON I/O for CLI
│   └── cli.py              # Command-line entry point
├── skills/
│   ├── prompt-coach/SKILL.md   # Single-turn coaching skill
│   └── growth-coach/SKILL.md   # Long-term pattern skill
├── adapters/
│   ├── claude-code/        # Claude Code platform adapter
│   └── codex/              # Codex/AGENTS.md platform adapter
├── tests/                  # Unit tests + fixture eval
└── docs/                   # This documentation
```

## v1 Issue Types

All 8 types are visible as of ④. The `Rule` column indicates whether
a rule-based detector exists (all agent-driven types still work via
the `agent_classification` path from option ②).

| Type | Signal | Rule | Surfaces |
|------|--------|------|----------|
| `missing_output_contract` | No format/structure specified | ✅ | per-turn + session + retrospective |
| `overloaded_request` | 3+ distinct phases in one request | ✅ | per-turn + session + retrospective |
| `missing_goal` | No recognizable intent | ✅ | per-turn + session + retrospective |
| `unbound_reference` | Bare pronoun at prompt start | ✅ | per-turn + session + retrospective |
| `missing_context` | References content not provided | ✅ | per-turn + session + retrospective |
| `missing_constraints` | No bounds on scope/length/quality | agent-only | per-turn + session + retrospective |
| `conflicting_instructions` | Self-contradictory requirements | agent-only | per-turn + session + retrospective |
| `missing_success_criteria` | No way to verify correctness | agent-only | per-turn + session + retrospective |

## Detection Pipeline

Two detection paths — agent-first is preferred when the agent has
conversation context:

```
User prompt  +  agent_classification (if present)
    ↓
  ┌─ agent_classification given + valid ──→ build_detection_from_agent()
  │     → adopts agent's issue_type / confidence / severity / is_sensitive /
  │       is_urgent; clamps numerics to [0,1]; accepts shadow types;
  │       null issue_type = agent actively suppresses (context-resolved)
  │
  └─ agent_classification missing/malformed ──→ detect(text)  [rule fallback]
        → domain classification (coding/writing/research/planning/ops/sensitive/other)
        → sensitivity check (SENSITIVE_KEYWORDS + SENSITIVE_PATTERN_RE)
        → social/greeting bypass (SOCIAL_PHRASE_RE)
        → output contract check (_has_output_contract)
        → overloaded check (_is_overloaded, _count_task_nouns)
        → missing goal check (_is_missing_goal)
    ↓
DetectionOutput(domain, is_sensitive, candidates)
    ↓
policy.select_action(PolicyInput)
    → mode check (off → Action.NONE)
    → sensitive domain → Action.NONE
    → filter candidates by confidence >= 0.70
    → budget check (proactive_count_7d vs max_proactive_per_7d)
    → user dismissed → Action.SILENT_REWRITE
    → urgency → prefer post over pre
    → severity/confidence → pre (standard) or post (light)
    → PolicyOutput(action, issue_type, reason)
    ↓
templates.post_answer_tip / pre_answer_micro_nudge (if coaching shown)
    ↓
memory.record_observation (always, if mode != off and non-sensitive)
```

## Adding a New Issue Type

1. Add enum value to `IssueType` in `taxonomy.py`
2. Add micro-habit strings in `MICRO_HABITS` and `MICRO_HABITS_EN`
3. Add detection logic in `detectors.py` (add to `detect()`)
4. Add templates in `templates.py` (POST_TIP_ZH/EN, PRE_NUDGE_ZH/EN, RETRO_ZH/EN)
5. Add to `V1_VISIBLE_ISSUES` in `taxonomy.py` (or leave as shadow-only)
6. Add fixture cases in `tests/fixtures/weak_prompts.jsonl`
7. Run `python3 tests/eval_fixtures.py` to verify metrics

## Adding a New Platform

1. Create `adapters/<platform-name>/install.sh` and `uninstall.sh`
2. Create `adapters/<platform-name>/<config>_snippet.md` (follows same marker convention)
3. Extend `platform.py`: add a `Platform` enum value and handle in `data_dir()`, `skill_dir()`, `claude_md_path()`
4. Test install → enable → trigger coaching → disable → uninstall flow

## Adjusting Thresholds

All thresholds live in `PolicyConfig` in `policy.py`. Default values:

```python
min_confidence: float = 0.70        # Minimum detector confidence for visible coaching
min_severity: float = 0.50          # Minimum severity
light_max_proactive_per_7d: int = 2  # Max visible tips in 7 days (light mode)
standard_max_proactive_per_7d: int = 4
light_max_retrospective_per_7d: int = 1
standard_max_retrospective_per_7d: int = 1
pattern_min_evidence: int = 4        # Min observations before retrospective
pattern_min_sessions: int = 3        # Min distinct sessions
pattern_min_cost_count: int = 2      # Min cost-signal observations
pattern_cooldown_days: int = 14      # Days between same-pattern reminders
observation_period_days: int = 14    # Days before any retro reminders
```

## CLI Reference

```bash
coach enable                     # Enable (light mode), show first-use disclosure
coach disable                    # Disable all coaching
coach status                     # Show mode, memory, counts
coach set-mode <off|light|standard>
coach set-memory <on|off>
coach set-sensitive-logging <on|off>   # Opt into logging on sensitive-domain prompts (default off)
coach dismiss                    # User said "don't remind me" — silent for 7 days

# Per-turn operations (called by agent via stdin JSON)
echo '{"text":"...", "session_id":"..."}' | coach select-action
echo '{...}' | coach record-observation

# Pattern management
coach update-patterns            # Apply weekly score decay
coach show-patterns              # List all pattern cards
coach forget-pattern <issue_type> [domain]
coach forget-all                 # Nuclear option

# Explanation
coach why-reminded               # Show last reminder chain

# Memory I/O
coach memory export              # JSONL to stdout
coach memory import              # JSONL from stdin (round-trips `memory export` output)

# Language override (default: auto from LANG env var)
coach --lang zh status           # Force Chinese output
coach --lang en enable           # Force English output

# Installation
coach install --platform claude-code
coach install --platform codex
coach install --all
coach uninstall --platform claude-code
```

## SQLite Schema

Database at `~/.claude/user-capability-coach/coach.db` (Claude Code).

| Table | Purpose |
|-------|---------|
| `observations` | One row per detected issue per session |
| `patterns` | Aggregated pattern cards with scores |
| `intervention_events` | Every time coaching was shown or suppressed |
| `preferences` | Lightweight key-value for non-coaching settings |
| `schema_meta` | Schema version tracking |

## Testing

```bash
# Unit tests
python3 -m pytest tests/ -v

# Fixture-based eval (milestone-zero targets)
python3 tests/eval_fixtures.py
python3 tests/eval_fixtures.py --verbose

# Single module
python3 -m pytest tests/test_detectors.py -v
```

## Shadow Mode (Milestone 6)

Before enabling visible coaching for users, run in shadow mode:
1. Install normally
2. Users run `coach enable` — coach operates in light mode
3. Coaching is shown, but use the `shadow_only=True` flag when evaluating new issue types
4. Collect observation distribution from `coach memory export | python3 -c "import json,sys; [print(json.dumps(r)) for l in sys.stdin for r in [json.loads(l)]]"`
5. Check: any issue_type with shadow_only=1 has >10% false positives? → Tune before opening
