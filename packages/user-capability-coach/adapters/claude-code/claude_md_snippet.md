<!-- user-capability-coach:start -->
## User capability coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. They are DISABLED BY DEFAULT.

When the user asks to enable coaching ("turn on prompt coach", "开启 coaching", "/coach on"), run `coach enable` and acknowledge briefly with the first-use three-point disclosure. When the user asks to disable, run `coach disable`.

**Post-hoc coaching flow (v2).** After a non-trivial response — code, docs, plans, explanations, debug help, refactors, reviews — run ONE command at the END of your response:

```
coach select-action --text="<verbatim last user message>" --session-id="<stable per-conversation id>"
```

This call decides the action, records the observation, and returns JSON. If the returned `action` is `post_answer_tip` (or `pre_answer_micro_nudge`, treat the same in post-hoc flow), append the returned `coaching_text` on a new line prefixed with `💡`. For `silent_rewrite` / `none`, do nothing visible. For `retrospective_reminder` / `session_pattern_nudge`, delegate to growth-coach.

**Mode determines when to run the call:**

- **off** — never run the coach CLI, no observations recorded.
- **light** (default after `coach enable`) — run when you judge the user's request is underspecified and likely to reduce answer quality.
- **standard** — same as light, plus prefer running on higher-severity gaps.
- **strict** — run on EVERY non-trivial turn. The user opted into aggressive checking; the CLI's policy still decides whether a visible tip surfaces. Trivial exchanges (greetings, yes/no answers, one-word acks) are exempt.

Determine the mode at the start of every session by running `coach status` once; remember it for the rest of the session.

Only consider `growth-coach` when coaching is enabled, long-term memory is on, the 14-day observation period has passed, OR the user explicitly asks for memory inspection, habit review, or coaching commands.

Never more than one visible coaching note per turn. Suppress coaching in urgent, emotional, or sensitive contexts.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `coach dismiss` — silences proactive tips for 7 days without fully disabling coaching. If they say "turn it off" / "关闭教练", run `coach disable` instead.

The `coach` CLI is available at: `~/.claude/user-capability-coach/coach`.
<!-- user-capability-coach:end -->
