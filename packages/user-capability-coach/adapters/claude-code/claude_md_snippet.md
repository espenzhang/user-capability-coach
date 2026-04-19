<!-- user-capability-coach:start -->
## User capability coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. They are DISABLED BY DEFAULT.

When the user asks to enable coaching ("turn on prompt coach", "开启 coaching", "/coach on"), run `coach enable` and acknowledge briefly with the first-use three-point disclosure. When the user asks to disable, run `coach disable`.

**Coaching mode determines how aggressively `prompt-coach` is invoked:**

- **mode=off** — never invoke prompt-coach, no observations recorded.
- **mode=light** (default after `coach enable`) — invoke `prompt-coach` only when you judge the user's request is underspecified and likely to reduce answer quality. Short ≠ weak; context often resolves apparent gaps. Conservative.
- **mode=standard** — same discretion as light, plus prefer pre-answer nudges on high-severity issues. Still invoked when you judge it worthwhile.
- **mode=strict** — **invoke `coach select-action` BEFORE responding to any non-trivial user request** (writing code, docs, plans, explanations, debug help, refactoring, reviews). Pass your best `agent_classification` based on the full conversation context. The user explicitly opted into aggressive checking by choosing this mode. Do not skip the call. Trivial exchanges (greetings, yes/no answers, one-word acks) are exempt.

Determine the mode at the start of every session by running `coach status` once; remember it for the rest of the session.

Only consider `growth-coach` when coaching is enabled, long-term memory is on, the 14-day observation period has passed, OR the user explicitly asks for prompt coaching, memory inspection, or habit review.

Never more than one visible coaching note per turn. Suppress coaching in urgent, emotional, or sensitive contexts unless the missing information blocks safe or correct execution.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `coach dismiss` — this silences proactive tips for 7 days without fully disabling coaching. If they say "turn it off" / "关闭教练", run `coach disable` instead.

The `coach` CLI is available at: `~/.claude/user-capability-coach/coach`.
<!-- user-capability-coach:end -->
