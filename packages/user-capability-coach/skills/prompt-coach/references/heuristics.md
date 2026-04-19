# Prompt Coach Heuristic Reference

## Detection Signals (from `detectors.py`)

These are heuristic signals — use them as input to your semantic judgment, not as final verdicts.

### missing_output_contract

**Signal**: No specific output structure specified.

**CLI check**:
```bash
echo '{"text": "...", "session_id": "..."}' | coach select-action
```

**What counts as an output contract** (specific format words):
- Structural: JSON, table, markdown, CSV, YAML, XML, HTML, diff, outline, checklist, numbered list
- Document types: PR description, changelog, test case, ERD, sequence diagram, flowchart
- Code artifacts: function, class, script, dockerfile, regex, SQL query, config file

**What does NOT count** (too generic):
- "document", "report", "文档", "报告" — these don't specify structure

**Chinese length threshold**: prompt must be ≥ 5 characters to trigger (filters trivial 1-2 char inputs)
**English length threshold**: ≥ 3 words

**Bypass conditions** (do NOT flag):
- Translation tasks (target language = implicit format)
- Length constraints present ("one-line", "in 3 sentences")
- Transformation tasks ("rewrite this as", "convert this to")
- Factual questions ("What is", "How does X work")

---

### overloaded_request

**Signal**: Multiple distinct task phases bundled into one request.

**Strong signals**:
- 3+ action verbs connected with conjunctions (then/and/also/并且/然后/再/还要)
- 3+ task nouns in one request (plan, spec, code, tests, docs, deployment guide)
- Explicit phase enumeration ("first X then Y then Z")

**Score thresholds**:
- Verb count ≥ 3: +0.25 base score
- Task nouns ≥ 3: +0.35; ≥ 4 nouns: +0.40
- Final score ≥ 0.5 → flag as overloaded

**Do NOT flag**:
- Single compound task that naturally has parts ("write a function with error handling")
- Sequential but same-domain steps ("refactor this and update the tests")

---

### missing_goal

**Signal**: Content given but desired outcome not stated.

**Strong signals**:
- Bare demonstratives: "look at this", "check this out", "see this"
- Chinese bare verbs: "分析" / "处理" / "优化" without a clear object
- Write verbs without specification: "帮我写" + no domain/type

**Do NOT flag**:
- Transformation verbs make goal implicit ("rewrite", "summarize", "translate")
- Questions make goal implicit ("What is the best way to...")
- Single clear action verb + object is sufficient

---

## Sensitive Domain Suppression

If `is_sensitive=true`, **always** choose `action=none` or `action=silent_rewrite`. Never show coaching.

Sensitive triggers (keyword or pattern match):
- Mental health: depression, depressed, anxiety, anxious, suicide, self-harm, panic attack
- Health: cancer, tumor, medication, surgery
- Legal: lawsuit, sued, divorce, lawyer
- Financial distress: bankruptcy, debt pressure, 喘不过气
- Family crisis: domestic violence, abusive, child missing
- Job loss: laid off, fired, 裁员, 被裁
- Bereavement: passed away, 去世
- Emergency: emergency, accident, 救命

---

## Budget Rules (light mode defaults)

| Metric | Limit |
|--------|-------|
| Proactive tips per 7 days | 2 |
| Retrospective reminders per 7 days | 1 |
| Min confidence to trigger | 0.70 |
| Min severity to trigger | 0.50 |

If budget exhausted → `silent_rewrite` (costs nothing from budget).

---

## Action Selection Priority

```
mode=off → none (always)
sensitive domain → none
user dismissed within 7d → silent_rewrite (suppressed_reason=user_dismissed)
per-turn candidate present AND budget exhausted → silent_rewrite
per-turn candidate present AND urgent → post_answer_tip (never pre)
per-turn candidate present AND standard + sev≥0.75 + conf≥0.80 → pre_answer_micro_nudge
per-turn candidate present otherwise → post_answer_tip
no per-turn candidate + session pattern (3 of last 5 turns same issue, not already nudged this session) → session_pattern_nudge
no per-turn candidate + qualifying cross-session pattern (past 14d observation, cooldown, budget) → retrospective_reminder
else → silent_rewrite
```

Note on 1-per-turn invariant: per-turn branches always win over session / retrospective. Session nudge fires **only** when the current prompt is clean AND a prior-turn pattern exists.

## Dismissal vs disable

- `coach dismiss` — 7-day soft silence; coaching stays enabled; patterns still accumulate if memory is on. Use when user says "少教育我 / 别提醒我 / stop suggesting improvements".
- `coach disable` — hard off; mode=off; no observations, no patterns, no coaching. Use when user says "turn it off / 关闭教练 / /coach off".

Choose dismiss over disable when the user sounds annoyed but hasn't explicitly asked to fully turn it off.
