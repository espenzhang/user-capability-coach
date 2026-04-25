# Advanced: passing agent_classification via stdin JSON

The low-friction path is:

```
coach select-action --text "<user message>" --session-id "<sid>"
```

Use the advanced JSON path ONLY when you have out-of-band context that
changes your classification of the prompt and you want the coach to use
your judgment instead of regex-only rules. Examples:

- Prior turns in the conversation already specified the output format,
  so a "write X" prompt this turn is NOT actually `missing_output_contract`
- The user said "改一下 tests" — regex sees a missing goal, but you've
  read the failing test output and know exactly what they mean
- Repo state supplies the constraint (e.g., the project has one test
  framework, so "add a test" has an obvious pattern)

## Schema

```bash
coach select-action < /dev/stdin
```

Pipe a JSON object:

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
    "evidence_summary": "system-generated description of the gap; NOT the user's raw text"
  }
}
```

## Field rules

- **`issue_type`** — STRICT enum. Must be one of:
  `missing_output_contract`, `overloaded_request`, `missing_goal`,
  `missing_context`, `unbound_reference`, `missing_success_criteria`,
  `conflicting_instructions`, `missing_constraints`, or `null` (context
  resolves ambiguity). Invalid values reject the whole classification
  and fall back to rules (coach emits `agent_cls_error` in stdout).
- **`domain`** — LENIENT. Canonical: `coding / writing / research /
  planning / ops / sensitive / other`. Synonyms auto-map
  (`docs/email/article` → `writing`, `api/debug/test` → `coding`,
  `infra/devops/security` → `ops`, etc.). Unknown → `other`.
- **`cost_signal`** — LENIENT. Canonical: `high_risk_guess /
  clarification_needed / output_format_mismatch / rework_required / none`.
- **`confidence`/`severity`** — 0.0–1.0. Below policy thresholds
  (0.70 / 0.50) the classification won't surface as visible coaching
  regardless. Don't inflate to force a tip.
- **`evidence_summary`** — system-generated description, NOT user's raw
  words. Lands in the local DB.
- **Omit the whole field** if you can't judge; rules-based fallback
  is fine.

## Important

Stdin-JSON mode does NOT auto-record an observation. If you want the
turn to contribute to long-term patterns, follow up with either:

```
coach record-observation --session-id <sid> --action <action_from_output> --issue <issue_type>
```

or the full stdin-JSON `record-observation` form.

Or just use the `--text` mode — it's simpler and records automatically.
