<!-- user-capability-coach:start -->
## User Capability Coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. **Both are disabled by default.**

To enable: user says "turn on prompt coach" or "开启 coaching" → run `coach enable`.
To disable: run `coach disable`.

**prompt-coach** — Triggers only when:
- Coaching is enabled (mode ≠ off)
- The user's current request has a clear, high-confidence gap (missing output format, bundled multi-phase request, or no stated goal)
- The gap will meaningfully reduce answer quality or require high-risk guessing

**growth-coach** — Triggers only when:
- Coaching is enabled AND long-term memory is on AND 14-day observation period has passed
- OR the user explicitly asks to manage coaching data, view patterns, or understand reminders

Never emit coaching in urgent, emotional, or sensitive contexts.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `coach dismiss` — this silences proactive tips for 7 days without fully disabling. Use `coach disable` only when they ask to fully turn off.

The `coach` CLI tool is available in `.agents/skills/user-capability-coach/tools/cli.py`.
<!-- user-capability-coach:end -->
