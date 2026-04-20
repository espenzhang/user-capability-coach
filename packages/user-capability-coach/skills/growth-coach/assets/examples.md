# Growth Coach Interaction Examples

## Retrospective Reminder

Shown after observation period, pattern meets all thresholds:

**Chinese:**
```
最近几次编程任务里，我经常需要猜你要的输出格式。以后固定在请求末尾加一行说明，比如"输出 JSON""用 markdown 表格"，通常能让我首轮更准。
```

**English:**
```
Over the past few coding sessions, I've often had to guess the output format you wanted. Adding one line at the end — e.g. 'output as JSON' or 'use a markdown table' — usually gets you a usable result on the first try.
```

---

## `/coach on` (first use)

```
Prompt Coach enabled (light mode). Here's what this means:

✦ What it does: Occasionally suggest how to phrase requests for more reliable results. At most once per turn, always after I've answered, never in sensitive situations.
✦ What it doesn't do: Store your message text, judge your personality, or coach you on topics marked sensitive or urgent.
✦ How to stop: Say '/coach off' or 'coach disable' at any time. Settings and data stay until you delete them.

Observation period ends: 2026-05-02 (no long-term reminders until then).
```

---

## `/coach off`

```
Prompt Coach is off. Local data is retained — use `coach memory export` to export or `coach uninstall` to remove.
```

---

## `/coach status`

```
Prompt Coach status: light
Long-term memory: off
Observation period ends: 2026-05-02
Checks this week: 7
Visible coaching this week: 2
Silent decisions this week: 5
Proactive tips this week: 1
Retrospective reminders this week: 0
```

---

## `/coach why` (after a retrospective reminder)

```
Last reminder: missing_output_contract (coding domain)

Evidence:
  - 6 observations across 4 sessions over 21 days
  - 3 of those had a cost signal (output format mismatch)
  - Last reminded: never before (first reminder for this pattern)
  - Cooldown OK: yes (no prior reminder)
  - Observation period: ended 7 days ago
  - Weekly budget: 0/1 retrospective reminders used this week

Why this session: all conditions met — pattern is active, data sufficient, cooldown clear.
```

---

## `/coach why` (no recent reminder)

```
No recent coaching reminder found.
```

---

## `show-patterns`

```json
{
  "patterns": [
    {
      "issue_type": "missing_output_contract",
      "domain": "coding",
      "score": 2.4,
      "evidence_count": 6,
      "distinct_sessions": 4,
      "cost_count": 3,
      "status": "active",
      "last_seen_at": "2026-04-17T...",
      "last_notified_at": null
    }
  ]
}
```

---

## Deleting patterns

```
User: "Forget the output contract pattern"
→ coach forget-pattern missing_output_contract
→ Response: Pattern deleted. 1 pattern row and related observations removed.

User: "Delete all my coaching history"
→ coach forget-all
→ Response: All coaching data deleted. Patterns, observations, and intervention events cleared.
```
