# User Memory Controls — User Guide

## What Prompt Coach stores about you

When long-term memory is enabled (it's off by default), the system stores:

| What's stored | Example |
|---------------|---------|
| Issue type | `missing_output_contract` |
| Domain | `coding` |
| When it happened | `2026-04-18T10:00:00Z` |
| A system-generated description | "coding task lacked output format — assistant guessed markdown" |
| Whether a tip was shown | `true` |
| Your session count (not IDs) | `3 sessions` |

**What is NOT stored:**

- Your original prompt text
- Full conversation history
- Any personal details
- Cross-domain personality inferences

## Enabling and disabling memory

```
/coach on              → enables coaching in light mode (memory still off)
coach set-memory on    → enables long-term pattern memory
coach set-memory off   → disables memory (existing data preserved)
```

When you first enable memory, a 14-day observation period begins. During this period, the system collects data but doesn't show any long-term pattern reminders. This prevents early noise from becoming a reminder.

**Partial silence — `coach dismiss`**

If you want to keep coaching enabled but stop the tips for a while, run:

```
coach dismiss           → no proactive tips for 7 days, coaching still active
```

This is gentler than `coach disable`: the mode stays on, long-term patterns keep accumulating (if memory is on), but no visible tips appear until the 7-day window passes.

## Viewing your data

```
coach show-patterns    → show all pattern cards and scores
coach why-reminded     → see why the last reminder appeared (with evidence)
coach memory export    → export all data as JSONL
```

## Deleting your data

```
coach forget-pattern missing_output_contract     → delete one pattern type
coach forget-pattern missing_output_contract coding  → delete for one domain
coach forget-all       → delete everything (patterns + observations + events)
coach uninstall        → removes skills, CLAUDE.md snippet, asks about data
```

All deletions are permanent and immediate.

## Where the data lives

| Platform | Path |
|----------|------|
| Claude Code | `~/.claude/user-capability-coach/coach.db` |
| Codex / generic | `$XDG_DATA_HOME/user-capability-coach/coach.db` or `~/.local/share/user-capability-coach/coach.db` |

Data is stored locally on your machine only. It is not synced, not sent to any server, and not included in git repositories.

## Pattern lifecycle

```
active → (60 days no recurrence) → resolved → (90 days) → archived
active → (30 days no recurrence or score < 0.5) → cooling
```

Once a pattern reaches `resolved`, you won't see reminders for it. Archived patterns are kept for reference but never shown.

## Reminder rules

A retrospective reminder will only appear if ALL of these are true:

1. Coaching is enabled
2. Long-term memory is enabled
3. The 14-day observation period has ended
4. The pattern has ≥ 4 observations across ≥ 3 sessions
5. At least 2 of those observations had a cost signal
6. It's been ≥ 14 days since the last reminder for this pattern
7. You haven't exceeded the weekly retrospective limit (1 per 7 days)
8. The current conversation isn't sensitive or urgent
9. The system can build a complete explanation chain

If any condition fails, no reminder is shown. Ever.
