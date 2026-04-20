<!-- user-capability-coach:start -->
## User Capability Coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. **Both are disabled by default.**

In this Codex host, treat any bare `coach ...` command mentioned in these skills or references as `__COACH_CMD__ ...`.

To enable: user says "turn on prompt coach" or "开启 coaching" → run `__COACH_CMD__ enable`.
To disable: run `__COACH_CMD__ disable`.

**prompt-coach triggering depends on the current coaching mode:**

- **off** — never invoke.
- **light / standard** — invoke when YOU judge the user's current request has a clear, high-confidence gap (missing output format, bundled multi-phase request, no stated goal, etc.) that will meaningfully reduce answer quality.
- **strict** — invoke `__COACH_CMD__ select-action` BEFORE responding to any non-trivial request (generating code, docs, plans, reviews, debugging help). Pass your `agent_classification` based on full conversation context. The user opted into aggressive checking — do not skip. Trivial exchanges (greetings, yes/no, acks) are exempt.

**growth-coach** — Triggers only when:
- Coaching is enabled AND long-term memory is on AND 14-day observation period has passed
- OR the user explicitly asks to manage coaching data, view patterns, or understand reminders

Never emit coaching in urgent, emotional, or sensitive contexts.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `__COACH_CMD__ dismiss` — this silences proactive tips for 7 days without fully disabling. Use `__COACH_CMD__ disable` only when they ask to fully turn off.

The coach CLI tool is available at `__COACH_CMD__`.
<!-- user-capability-coach:end -->
