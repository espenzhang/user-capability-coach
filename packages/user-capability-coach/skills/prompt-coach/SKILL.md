---
name: prompt-coach
description: Use when prompt coaching is enabled and a non-trivial user request may have a high-confidence prompt quality gap; skip greetings, urgent, emotional, or sensitive contexts.
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
- Coaching feedback/control intents such as "误报", "哪里犯了",
  "少教育我", "别提醒我", or "don't remind me". For dismissal requests,
  run `coach dismiss` and do not render a coaching tip.

## Context-aware classification for professional work

For non-trivial professional work, prefer passing your semantic judgment
with `--agent-json` when you can see prior turns, repo state, or attached
context. The regex-only `--text` path is still valid for simple cases, but
visible coaching should be a high-precision signal: stay silent unless a
missing detail is likely to cause real error, rework, or unsafe guessing.

Clear professional tasks should normally be classified as no issue:

```
coach select-action \
  --text="<verbatim last user message>" \
  --session-id="<stable per-conversation id>" \
  --agent-json='{"issue_type":null,"domain":"coding","evidence_summary":"clear professional task in context"}'
```

Use that null classification for clear review/debug/follow-up/repo
operations, for example: "review this PR", "逐条核对 review comments",
"把刚才发现的问题修掉", "跑 benchmark 看 regression", "检查 loss 曲线",
"提交并推送git吧", or "跑一下测试".

Only pass a non-null issue when the gap is specific and high confidence.
For example, `missing_output_contract` is appropriate for deliverables
whose usable shape materially matters, such as API docs, reports, PR
descriptions, changelogs, tables, JSON/CSV, or templates. Do not use it
for ordinary code review, debugging, test, git, benchmark, or follow-up
execution requests whose intended action is already clear.

`--agent-json` and stdin JSON accept the same classification schema (see
`references/agent_classification.md`). Stdin-JSON mode does NOT
auto-record (for backward compat); callers must separately invoke
`record-observation` if they want pattern accumulation.

## Example

User: "帮我写一个接口文档"

```
[you write the API doc in default markdown]

💡 下次加一句格式说明会让结果更直接可用，比如：「输出 markdown 表格，包含接口名、参数、返回值三列」。
```

The 💡 line comes verbatim from `coaching_text` in the CLI output.
