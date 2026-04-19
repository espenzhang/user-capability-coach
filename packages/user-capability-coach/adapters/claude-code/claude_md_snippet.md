<!-- user-capability-coach:start -->
## User capability coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. They are DISABLED BY DEFAULT.

When the user asks to enable coaching ("turn on prompt coach", "开启 coaching", "/coach on"), run `coach enable` and acknowledge briefly with the first-use three-point disclosure. When the user asks to disable, run `coach disable`.

Only consider using `prompt-coach` when coaching is enabled AND the user's request is underspecified in a way that is likely to reduce answer quality, increase clarification turns, or force high-risk guessing. Do not use it just because a prompt is short. Never more than one coaching note per turn. Suppress coaching in urgent, emotional, or sensitive contexts unless the missing information blocks safe or correct execution.

Only consider `growth-coach` when coaching is enabled, long-term memory is on, the 14-day observation period has passed, OR the user explicitly asks for prompt coaching, memory inspection, or habit review.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `coach dismiss` — this silences proactive tips for 7 days without fully disabling coaching. If they say "turn it off" / "关闭教练", run `coach disable` instead.

The `coach` CLI is available at: `~/.claude/user-capability-coach/coach`.
<!-- user-capability-coach:end -->
