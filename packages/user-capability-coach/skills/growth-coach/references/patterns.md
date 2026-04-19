# Growth Coach Pattern Reference

## Pattern Lifecycle

```
active → (30 days no recurrence OR score < 0.5) → cooling
active → (60 days no recurrence) → resolved
cooling → (30 days no recurrence) → resolved
resolved → (90 days) → archived
```

Once `resolved`, no reminders are shown. Once `archived`, the pattern is kept for reference only.

---

## Retrospective Reminder Conditions (ALL must be true)

1. `mode != off`
2. `memory_enabled = true`
3. Observation period has ended (14 days after first `coach enable`)
4. Pattern has ≥ 4 observations
5. Pattern spans ≥ 3 distinct sessions
6. At least 2 observations had a cost signal
7. ≥ 14 days since last reminder for this pattern
8. Weekly retrospective budget not exhausted (≤ 1 per 7 days)
9. Current conversation is not sensitive or urgent
10. `build_explanation_chain()` returns a complete chain

If ANY condition fails → no reminder. No exceptions.

---

## Evidence Scoring

On each new non-shadow observation (same issue_type + domain):

```python
weeks_elapsed = (now - last_seen_at).total_seconds() / (7 * 86400)
decayed = old_score * (0.85 ** weeks_elapsed)
new_score = decayed + 1.0
```

So the pattern score equals the sum of contributions of past observations, each weighted by `0.85^weeks_since_that_observation`. Severity/confidence/cost signals influence which observations *qualify* (via detector thresholds) and whether they count in the explanation chain, but do not change the per-observation score contribution in v1.

Weekly decay (applied by `coach update-patterns`):

```
score = score * (0.85 ** weeks_since_last_seen)
```

Pattern status transitions (computed by `apply_weekly_decay`):
- `days_since_last_seen ≥ 90` → `archived`
- `days_since_last_seen ≥ 60` → `resolved`
- `days_since_last_seen ≥ 30` OR `decayed_score < 0.5` → `cooling`
- otherwise → `active`

Archived patterns never reactivate — a new observation of an archived
issue+domain keeps status=archived (prevents very old patterns from
repeatedly coming back to life). Resolved patterns do reactivate on a
new observation (score jumps back up, `resolved_at` cleared).

---

## Explanation Chain Fields

Required for any retrospective reminder. `build_explanation_chain()`
returns this shape (or None if the pattern can't be found — which
suppresses the reminder):

```json
{
  "issue_type": "missing_output_contract",
  "evidence_count": 6,
  "distinct_sessions": 4,
  "cost_count": 3,
  "last_notified_at": null,
  "cooldown_days": 14,
  "days_since_last_notification": null,
  "cooldown_ok": true,
  "pattern_score": 2.34,
  "pattern_status": "active",
  "allow_reason": "evidence=6>=4, sessions=4>=3, cost=3>=2, cooldown=ok",
  "built_at": "2026-04-18T11:05:00+00:00"
}
```

Counts exclude shadow-only observations (mirrors what patterns see).
If any required field is None, the chain is considered incomplete and
the reminder is suppressed.

---

## CLI Pattern Management

```bash
# View all patterns
coach show-patterns

# Delete one pattern type
coach forget-pattern missing_output_contract

# Delete pattern for specific domain
coach forget-pattern missing_output_contract coding

# Delete everything
coach forget-all

# Why was I reminded?
coach why-reminded

# Apply score decay
coach update-patterns
```

---

## Pattern Status Display

| Status | Meaning | User-visible? |
|--------|---------|---------------|
| active | Being tracked, may trigger reminders | Yes (if threshold met) |
| cooling | Score too low or 30+ days without recurrence | No reminders |
| resolved | 60+ days clean, pattern considered fixed | No reminders |
| archived | 90+ days after resolved | No |

---

## Privacy Guarantees to Communicate

When displaying patterns to users:
- No raw prompt text is stored
- `evidence_summary` is system-generated, not a copy of the user's words
- Data is local only (`~/.claude/user-capability-coach/coach.db`)
- User can delete any pattern or all data at any time
- Sensitive-domain observations are stripped (no content stored)
