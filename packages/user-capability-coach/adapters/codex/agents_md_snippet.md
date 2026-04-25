<!-- user-capability-coach:start -->
## User Capability Coaching

Two optional skills are installed: `prompt-coach` and `growth-coach`. **Both are disabled by default.**

In this Codex host, treat any bare `coach ...` command mentioned in these skills or references as `__COACH_CMD__ ...`.

To enable: user says "turn on prompt coach" or "开启 coaching" → run `__COACH_CMD__ enable`.
To disable: run `__COACH_CMD__ disable`.

**Post-hoc coaching flow (v2).** After a non-trivial response — generating code, docs, plans, reviews, debugging help — run ONE command at the END of your response:

```
__COACH_CMD__ select-action --text="<verbatim last user message>" --session-id="<stable per-conversation id>"
```

This call decides the action, records the observation, and returns JSON. If the returned `action` is `post_answer_tip` (or `pre_answer_micro_nudge`, treat the same here), append the returned `coaching_text` on a new line prefixed with `💡`. For `silent_rewrite` / `none`, do nothing visible. For `retrospective_reminder` / `session_pattern_nudge`, delegate to growth-coach.

**Mode determines when to run the call:**

- **off** — never invoke.
- **light / standard** — run when YOU judge the current request has a clear, high-confidence gap that will meaningfully reduce answer quality.
- **strict** — run on every non-trivial turn. The user opted into aggressive checking; the CLI's policy decides whether a visible tip surfaces. Trivial exchanges (greetings, yes/no, one-word acks) are exempt.

**growth-coach** — triggers only when coaching is enabled AND long-term memory is on AND the 14-day observation period has passed, OR the user explicitly asks to manage coaching data, view patterns, or understand reminders.

Never emit coaching in urgent, emotional, or sensitive contexts.

If the user says "don't remind me" / "少教育我" / "别提醒我", run `__COACH_CMD__ dismiss` — silences proactive tips for 7 days without fully disabling. Use `__COACH_CMD__ disable` only when they ask to fully turn off.

The coach CLI tool is available at `__COACH_CMD__`.
<!-- user-capability-coach:end -->
