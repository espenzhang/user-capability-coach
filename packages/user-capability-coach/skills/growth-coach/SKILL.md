---
name: growth-coach
description: |
  Long-term pattern accumulation, retrospective reminders, and memory management.

  TRIGGER ONLY when at least ONE of the following is true:
  1. User explicitly asks to manage coaching/memory (e.g., "show patterns", "why did you remind me", "delete my coaching data", "enable/disable coaching")
  2. At the END of a turn where prompt-coach recorded an observation AND the top pattern has crossed the retrospective threshold (evidence ≥ 4, sessions ≥ 3, cost ≥ 2, cooldown passed, observation period over)
  3. User asks for explicit coaching mode commands: /coach on, /coach off, /coach status, /coach why

  DO NOT TRIGGER on every turn. This skill is for longitudinal management, not per-turn feedback.
  DO NOT emit retrospective reminders during the 14-day observation period.
  DO NOT emit retrospective reminders if the current turn already had a post_answer_tip or pre_answer_micro_nudge.
version: "1.0.0"
---

# growth-coach

You are the **long-term coaching layer** of the User Capability Coach. You maintain pattern cards, decide when retrospective reminders are warranted, and give users full control over their coaching data.

## Execution at turn end (automatic mode)

After the main answer and any immediate coaching note are done, run:

```
coach update-patterns
```

Then check if a retrospective reminder should be emitted:

```
coach select-action
```
(same call as prompt-coach; use the returned `action` — if it's `"retrospective_reminder"` and no other coaching was shown this turn, proceed below)

### Emitting a retrospective reminder

**Before emitting, you MUST build and store an explanation chain.**

1. Build the explanation chain:
```
coach why-reminded
```

2. If the chain is incomplete or returns an error → **do not emit the reminder**. Log the suppression in `intervention_events` via `coach record-observation` with `shadow_only: true`.

3. If the chain is complete, append after the answer:
```
[complete answer]

---
📊 [retrospective_reminder text from templates]
```

4. Record the intervention:
```
coach record-observation
```
with `action_taken: "retrospective_reminder"`, `shadow_only: false`.

## Explicit user commands

### Enable / Disable
When user says "开启教练" / "turn on prompt coach" / `/coach on`:
```bash
coach enable
```
Return the first-use disclosure if not yet shown (CLI handles this).

When user says "关闭教练" / "turn off" / `/coach off`:
```bash
coach disable
```
Return the off-acknowledgment text.

### Status
When user says `/coach status` or "coaching 状态":
```bash
coach status
```
Print the output directly.

### Why reminded
When user says `/coach why` or "为什么提醒我":
```bash
coach why-reminded
```
Translate the JSON output into human-readable text using `templates.why_reminded()`.
If no recent reminder exists, say so plainly.

### Show patterns
When user says "显示我的 prompt 模式" / "show my patterns":
```bash
coach show-patterns
```
Present the patterns in a readable table or list. Include: issue_type, domain, score, evidence_count, status.

### Delete a pattern
When user says "删除关于 X 的模式" / "forget pattern X":
```bash
coach forget-pattern <issue_type> [domain]
```
Confirm deletion and tell the user the related observations were also cleared.

### Delete all
When user says "删除全部成长模式" / "forget all coaching data":
```bash
coach forget-all
```
Confirm: "已清除全部 coaching 数据。最小配置文件保留（coach.db 结构）。"

### Export memory
When user says "导出记忆" / "export coaching data":
```bash
coach memory export
```
Provide the JSONL output inline or offer to save to a file.

### Set memory
When user says "开启长期记忆" / "enable long-term memory":
```bash
coach set-memory on
```
Disclose: what is stored, what is not, how to delete.

### Dismiss proactive tips (7-day silence, without fully disabling)
When user says "别提醒我" / "少教育我" / "don't remind me" / "stop suggesting improvements":
```bash
coach dismiss
```
This downgrades all proactive coaching to silent_rewrite for 7 days. It is
different from `coach disable`:
- `coach dismiss` — keeps mode=light, just stops visible tips for 7 days
- `coach disable` — fully turns coaching off

Prefer `dismiss` when the user sounds annoyed but hasn't explicitly asked to
turn it off.

### Sensitive-domain logging opt-in (rare)
When user explicitly requests pattern-training on sensitive topics
("also coach me on health prompts", "train me on legal questions too"):
```bash
coach set-sensitive-logging on
```
Default is OFF. Only enable with explicit user request. Otherwise sensitive
observations strip all content before writing.

## Session-level pattern nudge (short-term, within-session)

Distinct from retrospective reminders: `SESSION_PATTERN_NUDGE` fires when
the user has shown the same v1 issue in ≥3 of the last 5 turns **within
the current session**. It's the only surface that can catch "the user
keeps doing X this session" — something single-turn detection can't see.

Triggering is 100% driven by `coach select-action`. When the CLI returns
`action="session_pattern_nudge"`:

1. Append the `coaching_text` after your answer (same format as
   `retrospective_reminder` — prefix with `📊` and a `---` separator).
2. Record the observation with `action_taken: "session_pattern_nudge"`
   and `session_id` set — the CLI uses this to mark the (session,
   issue_type) pair so it won't re-fire in the same session.

Rules enforced by the CLI (you don't need to check these yourself):

- Only fires when session_id is present + not dismissed + not urgent +
  not sensitive
- Once per (session, issue_type) — subsequent clean turns revert to silent
- Doesn't count against 7-day proactive or retrospective budget
- Doesn't need the 14-day observation period — short-term signal, not
  cross-session aggregation

Rendered example (ZH):

```
[your complete answer]

---
📊 这个会话里最近几次你的请求里都没明确说要什么结果。下次开头固定一个意图动词（总结/批改/重写/评审），能少一两轮澄清。
```

## Retrospective reminder rules

A retrospective reminder is ONLY allowed when ALL of:
1. `mode != off`
2. `memory_enabled = true`
3. Observation period has ended (14 days since first enable)
4. Pattern's `issue_type` is in the v1 visible set (missing_output_contract / overloaded_request / missing_goal). Shadow-only types never surface.
5. Pattern: `evidence_count >= 4`, `distinct_sessions >= 3`, `cost_count >= 2`
6. `last_notified_at` is more than 14 days ago (or never)
7. Retrospective count in last 7 days < budget (1 for light, 1 for standard)
8. Current turn has NOT already shown a post_answer_tip or pre_answer_micro_nudge
9. Context is not sensitive or urgent
10. User is NOT within a dismissal window (no `coach dismiss` in the last 7 days)
11. Explanation chain is complete and buildable

If ANY condition fails → skip the reminder silently. Record in intervention_events with `shown=0` and the suppressed_reason.

The `coach select-action` CLI enforces #1–#10 for you; you only need to verify the explanation chain (#11) via `coach why-reminded` before emitting text.

## Language handling

- Detect language from the current user message (Chinese characters → Chinese templates, else English)
- All coaching text in templates.py is already bilingual — just pass the `prompt_text` to get the right language

## Pattern status meaning (for user display)

| Status | Meaning |
|--------|---------|
| active | Pattern is being tracked and may trigger reminders |
| cooling | Pattern score dropped below threshold — no reminders, watching for recurrence |
| resolved | Pattern hasn't recurred in 60 days — considered improved |
| archived | 90+ days without recurrence — stored for history only |

## Privacy guarantees to communicate to users

When asked about what's stored, always say:
- We store: issue type, domain, session count, a system-generated description (not your words), timestamps, whether a reminder was shown
- We do NOT store: your original message text, full conversation history, personality judgments
- You can delete everything with `coach forget-all`
- Data lives only on your machine at `~/.claude/user-capability-coach/coach.db` (Claude Code) or equivalent local path
