# User Capability Coach

> Local, opt-in, privacy-first coaching skills that help users write better prompts — without ever getting in the way.

[中文版](README.zh-CN.md)

## Why this project exists

Today's AI assistants are **answer-friendly**: they work hard to guess what you meant even when you didn't say it clearly. That's great for the current task, but a pattern emerges across sessions:

- The task gets done
- The user asks the next question the same way
- Clarification rounds, rework, and style mismatches keep recurring
- The user's prompting skill doesn't actually grow — they just learn to tolerate the guesswork

This project addresses a gap almost every AI tool leaves open: **the model keeps getting smarter at answering, but the user never gets better at asking.** `user-capability-coach` is a bet that the assistant should also, quietly, help the user grow — without ever interrupting the current task.

The design deliberately avoids two anti-patterns:

1. **Prompt police** — critiquing every message. Users turn it off within days.
2. **Silent rewrite only** — the assistant fixes your intent internally and you never learn where the leverage is.

The target user isn't a first-time prompter (they need more answers, not coaching). It's **people who use AI assistants heavily**, whose bottleneck is no longer "is the model smart enough" but "can I ask for what I want in fewer rounds, with less rework". The coach surfaces that leverage in a way that's brief, ignorable, and never judgmental — so the user can take it or leave it in under a second.

**Success is not measured by "how many tips we showed"** but by three outcomes the plan explicitly tracks: first-turn hit rate goes up, repeat prompting gaps go down, and users still feel *helped* rather than *observed*.

---

## What the package actually is

`user-capability-coach` is a two-skill package (`prompt-coach` + `growth-coach`) that helps users improve their prompting — **only after they explicitly turn it on**, in a way that's brief, ignorable, and never judgmental.

It runs entirely on the user's machine. No network calls. No raw prompts stored. Zero output until the user runs `coach enable`.

## What it does

- **Single-turn coaching**: when the current prompt has a clear, fixable gap (missing output format / bundled phases / unstated goal / etc.), appends a short tip *after* the answer — never interrupts the task.
- **Session patterns**: spots within-session repetitions (e.g. "3 of the last 5 turns lacked a clear goal") and surfaces a mid-session nudge, once per session per issue.
- **Long-term patterns**: accumulates cross-session patterns with proper evidence thresholds (≥4 observations / ≥3 sessions / ≥2 cost signals), a 14-day observation period before any long-term reminders, and a 14-day cooldown between repeat reminders for the same pattern.
- **Full user control**: `coach dismiss` for 7-day soft silence, `coach disable` for hard off, `coach forget-pattern` / `coach forget-all` for data deletion, `coach why-reminded` for full audit trail.

## Key design principles

1. **Default `off`**. After install, the coach does nothing visible and writes nothing to disk until the user explicitly runs `coach enable`.
2. **Task first, coaching second**. Always give the answer first, then (optionally) a short ignorable suggestion. Never block a task on clarification.
3. **At most one coaching note per turn**. Hard invariant enforced by the policy layer.
4. **Agent-first classification**. Agents with full conversation context classify each prompt and pass their judgment to coach; rule-based detection is a fallback only.
5. **Privacy by construction**. No raw prompt text ever lands on disk — only system-generated summaries. Sensitive-domain prompts are content-free records (`shadow_only=1`). Long-term memory defaults to off.
6. **Audit everything**. Every retrospective reminder carries a complete `explanation_chain` (evidence count / distinct sessions / cost count / cooldown state / reason). If the chain can't be built, the reminder doesn't fire.

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────┐
│  Agent (Claude Code / Codex) — has full conversation    │
│  context, classifies each prompt, passes to coach       │
│  ├─ prompt-coach    — single-turn handling               │
│  └─ growth-coach    — session + long-term patterns       │
└────────────────┬────────────────────────────────────────┘
                 │ stdin JSON (text, session_id, agent_classification)
                 ▼
┌─────────────────────────────────────────────────────────┐
│  Coach CLI (Python, local subprocess, <60ms per call)    │
│  ├─ detectors.py    — rule fallback when agent omits     │
│  ├─ policy.py       — mode / budget / cooldown / dismissal│
│  └─ templates.py    — bilingual coaching text            │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  Local SQLite                                            │
│  ├─ observations      — cross-session evidence           │
│  ├─ patterns          — longitudinal aggregates          │
│  ├─ session_turns     — short-term within-session        │
│  ├─ intervention_events — every reminder, with audit chain│
│  └─ preferences / session_nudge_log / schema_meta        │
└─────────────────────────────────────────────────────────┘
```

## Issue types

All 8 are user-visible (after `coach enable`). Rule-based fallbacks exist for 7 of them; `missing_constraints` is agent-classification-only.

| Type | What it catches | Rule |
|---|---|---|
| `missing_output_contract` | No format/structure specified | ✅ strong |
| `overloaded_request` | 3+ distinct phases bundled in one request | ✅ strong |
| `missing_goal` | No recognizable intent or unbound reference | ✅ strong |
| `unbound_reference` | Bare pronoun + problem word ("It's broken.") | ✅ strong |
| `missing_context` | References content ("this code") but none attached | ✅ strong |
| `missing_success_criteria` | Improve verb without verifiable metric | ⚠️ weak |
| `conflicting_instructions` | Explicit conflict markers ("shorter but detailed") | ⚠️ weak |
| `missing_constraints` | Goal set but no length / audience / quality bounds | agent-only |

## Install

**For Claude Code (user-level):**
```bash
cd packages/user-capability-coach
bash adapters/claude-code/install.sh
```

This:
1. Copies `prompt-coach/` and `growth-coach/` to `~/.claude/skills/`
2. Creates the local data dir at `~/.claude/user-capability-coach/`
3. Sets up the `coach` CLI wrapper
4. Appends a short snippet to `~/.claude/CLAUDE.md` (with `<!-- user-capability-coach:start/end -->` markers for precise uninstall)

**For Codex / AGENTS.md projects:**
```bash
export COACH_PROJECT_ROOT=/path/to/your/project
bash adapters/codex/install.sh
```

**After install, coaching is OFF.** To enable:
```
/coach on          # in Claude Code
# or
coach enable       # in any shell
```

## What the user actually sees

### Example 1 — single-turn tip (most common)

**User**: `帮我写一个接口文档`

**Without coach**: agent guesses at format, produces verbose markdown, user re-asks.

**With coach** (`light` mode, after `coach enable`):

> [API doc output in default markdown format]
>
> ---
>
> 💡 下次加一句输出格式说明会让结果更直接可用，比如：「输出 markdown 表格」「给编号列表」。

One line, appended *after* the answer. User can ignore it and continue.

### Example 2 — session pattern nudge

After 3 vague requests in the same session (different wordings, same gap), on the 4th turn — **even if the 4th prompt itself is fine**:

> [answer to current prompt]
>
> ---
>
> 📊 这个会话里最近几次你的请求里都没明确说要什么结果。下次开头固定一个意图动词（总结/批改/重写/评审），能少一两轮澄清。

Fires at most **once per session per issue**. Not dependent on the 14-day observation period.

### Example 3 — agent suppresses a rule false positive via context

**User, turn N−1**: `please write the docs as markdown bullet points`
**User, turn N**: `帮我写一个接口文档`

The rule-based detector alone would flag turn N as `missing_output_contract` — but the agent saw turn N−1. Agent sends:

```json
{
  "text": "帮我写一个接口文档",
  "session_id": "conv-xyz",
  "agent_classification": { "issue_type": null,
    "evidence_summary": "prior turn already specified markdown bullets" }
}
```

Coach returns `action: silent_rewrite`. No tip surfaces. Context wins.

### Example 4 — `coach why-reminded`

After a retrospective reminder lands, the user asks:

```
$ coach why-reminded
上次提醒的原因：
• 问题类型: missing_output_contract
• 过去记录次数: 6 次，涉及 4 个会话
• 其中造成明显成本的次数: 3 次
• 上次提醒时间: never（冷却期 14 天）
• 本次允许原因: evidence=6>=4, sessions=4>=3, cost=3>=2, cooldown=ok
```

If the chain can't be built (data missing / corrupt), the reminder wouldn't have fired in the first place.

## Quick start

```bash
# Enable coaching (light mode by default)
coach enable

# Also turn on long-term memory (starts 14-day observation period)
coach set-memory on

# Promote to standard mode (can do pre-answer nudges on high-severity issues)
coach set-mode standard

# Check status
coach status

# See accumulated patterns
coach show-patterns

# Temporarily silence (7 days) without fully disabling
coach dismiss

# Fully disable
coach disable

# Ask why you were just reminded
coach why-reminded

# Remove a specific pattern
coach forget-pattern missing_output_contract [coding]

# Nuclear option
coach forget-all

# Data export / import (round-trips between machines)
coach memory export > backup.jsonl
coach memory import < backup.jsonl
```

## How the agent is expected to interact

On each user turn, the agent:

1. **Classifies the prompt with full context**, then calls `coach select-action` with the classification attached:
   ```json
   {
     "text": "<current user message>",
     "session_id": "<stable per-conversation id>",
     "agent_classification": {
       "issue_type": "missing_output_contract",
       "confidence": 0.85,
       "severity": 0.75,
       "fixability": 0.9,
       "cost_signal": "output_format_mismatch",
       "domain": "coding",
       "is_sensitive": false,
       "is_urgent": false,
       "evidence_summary": "system-generated description, not user's raw words"
     }
   }
   ```
2. **Renders coaching based on the returned `action`**:
   - `none` / `silent_rewrite` → just answer
   - `post_answer_tip` → answer, then append `💡 <coaching_text>`
   - `pre_answer_micro_nudge` → prepend `⚡ <coaching_text>`, then answer
   - `retrospective_reminder` / `session_pattern_nudge` → delegate to `growth-coach`, append `📊 <coaching_text>`
3. **Records the turn** via `coach record-observation` with the same classification.

Agents that don't supply `agent_classification` fall through to rule-based detection — still functional but blind to context.

## Privacy

Hard rules (all enforced in code + tested):

1. **Nothing is written until the user runs `coach enable`.**
2. **No raw prompt text is ever stored.** Only system-generated `evidence_summary`, structured fields (issue_type / domain / timestamps / counts), and pattern scores.
3. **Long-term memory defaults to off.** Enabling it starts a 14-day observation period during which nothing is surfaced — the system only learns.
4. **Sensitive-domain observations are stripped by default.** `domain=sensitive` hits write `issue_type=null, evidence_summary="", shadow_only=1` regardless of what the caller passed. An explicit `coach set-sensitive-logging on` is required to opt in.
5. **Deletion is permanent.** `forget-pattern` clears patterns + observations + related intervention_events. `forget-all` wipes all three tables.
6. **Data stays local.** `~/.claude/user-capability-coach/coach.db` (Claude Code) or `~/.local/share/user-capability-coach/coach.db` (Codex) or `~/Library/Application Support/...` (macOS Codex). No network calls, no cloud sync.

## Reminder safety rails

A long-term retrospective reminder fires **only** when all of these are true:

- `mode != off` and `memory_enabled = true`
- 14-day observation period has ended
- Pattern has ≥ 4 observations across ≥ 3 distinct sessions
- At least 2 observations had a cost signal
- ≥ 14 days since the last reminder for this pattern
- Weekly retrospective budget not exhausted (1/week)
- Current context is not sensitive or urgent
- User is not in a 7-day dismissal window
- `build_explanation_chain()` returns a complete chain

If ANY fails, the reminder is silently suppressed. No exceptions.

## Development

```bash
cd packages/user-capability-coach

# Run all tests
python3 -m pytest tests/ -v

# Run the fixture-based evaluation
python3 tests/eval_fixtures.py

# Run with verbose miss analysis
python3 tests/eval_fixtures.py --verbose
```

Current state:
- **247 unit + integration tests** passing
- **v1 detector**: 100% hit rate on the 57-prompt weak fixture set
- **False positive rate**: 0% on the 25-prompt good fixture set
- **Sensitive suppression**: 100% on the 18-prompt sensitive fixture set

## Multi-platform

The core (taxonomy / detectors / policy / memory / templates / CLI) is platform-agnostic Python. Each platform gets a thin adapter:

- `adapters/claude-code/` — copies skills to `~/.claude/skills/`, appends snippet to `~/.claude/CLAUDE.md`
- `adapters/codex/` — copies skills to `<project>/.agents/skills/`, appends snippet to `AGENTS.md`

Adding a new host (Cursor / Windsurf / Continue / ...) only requires a new adapter directory. The core doesn't change.

## Documentation

- [`docs/user-capability-coach.md`](packages/user-capability-coach/docs/user-capability-coach.md) — developer guide, architecture, thresholds, how to add issue types
- [`docs/user-capability-memory-controls.md`](packages/user-capability-coach/docs/user-capability-memory-controls.md) — end-user guide for memory controls and data deletion
- [`docs/multi-platform-install.md`](packages/user-capability-coach/docs/multi-platform-install.md) — install / uninstall for each supported platform

## License

MIT
