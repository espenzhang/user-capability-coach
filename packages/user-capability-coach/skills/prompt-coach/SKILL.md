---
name: prompt-coach
description: |
  Post-hoc per-turn coaching. After answering a non-trivial user request
  (code, docs, plans, explanations, debug help, refactors, reviews), run
  ONE bash command at the END of your response to let the coach decide
  whether to surface a visible tip.

  Only active when coaching is enabled (check once per session via `coach status`).

  Run EVERY non-trivial turn in strict mode. In light/standard, run when
  you judge the request has a likely quality gap. Skip: pure greetings,
  one-word replies, emotional/sensitive context, urgent situations.
version: "2.0.0"
---

# prompt-coach

**One-command post-hoc flow.** You answer first, then at the very end of
the same turn run:

```
coach select-action --text="<verbatim last user message>" --session-id="<stable per-conversation id>"
```

This ONE call decides the action AND records the observation. No JSON to
construct, no separate `record-observation` call needed.

**Session id**: use any string that's stable for the duration of the
conversation and distinct across conversations. A hash of the first user
message works. If you pass no `--session-id`, coach generates an `anon-*`
fallback — but long-term session-count metrics will be off. Prefer
stable ids.

**Important — use `=` form**: always write `--text="<msg>"` and
`--session-id="<id>"` (with `=`), NOT `--text "<msg>"`. When the user's
message starts with a dash (e.g., `"-h"` or `"- check this"`) argparse
will mis-parse the space-separated form. The `=` form is unambiguous.

## What to do with the output

The CLI returns JSON with an `action` field. React per action:

- **`post_answer_tip`** → append the returned `coaching_text` on a new
  line, prefixed with `💡`. One tip per turn, max 2 sentences.
- **`silent_rewrite`** → do nothing visible. The CLI already logged it.
- **`none`** → nothing to do.
- **`pre_answer_micro_nudge`** → in post-hoc flow we don't interrupt the
  answer, so treat the same as `post_answer_tip` (render as 💡 tip).
- **`retrospective_reminder`** / **`session_pattern_nudge`** → delegate to
  growth-coach. Do NOT also emit a per-turn tip (one visible coaching
  per turn max).

If `suppressed_reason = "user_dismissed"`, the user recently said "don't
remind me" — stay silent for 7 days. Never override.

## When to skip the call entirely

- Pure greetings / acks / yes-no / one-word replies
- Emotional, sensitive, or urgent context (the policy also suppresses
  these, but skipping the call saves a CLI hop)
- You already emitted a coaching note this turn via growth-coach

## Advanced: passing agent_classification (optional)

If you have high-value context (prior turns, repo state) that changes
your judgment about the prompt, pass `--agent-json '<json-string>'` or
pipe the full JSON on stdin — both accept the same classification
schema (see `references/agent_classification.md`). Stdin-JSON mode does
NOT auto-record (for backward compat); callers must separately invoke
`record-observation` if they want pattern accumulation.

The `--text`-only form (no agent_classification) is the default — use it
unless you're confident the agent context beats rule-based detection.

## Example

User: "帮我写一个接口文档"

```
[you write the API doc in default markdown]

💡 下次加一句格式说明会让结果更直接可用，比如：「输出 markdown 表格，包含接口名、参数、返回值三列」。
```

The 💡 line comes verbatim from `coaching_text` in the CLI output.
