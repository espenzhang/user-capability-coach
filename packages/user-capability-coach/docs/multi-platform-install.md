# Multi-Platform Installation Guide

## Prerequisites

- Python 3.10+
- Bash (for install scripts)
- Either Claude Code or a Codex-compatible agent environment

## Installing for Claude Code

```bash
# From the package directory
bash adapters/claude-code/install.sh
```

This will:
1. Copy `prompt-coach/` and `growth-coach/` to `~/.claude/skills/`
2. Initialize the SQLite database at `~/.claude/user-capability-coach/`
3. Create a `coach` wrapper script at `~/.claude/user-capability-coach/coach`
4. Append the coaching snippet to `~/.claude/CLAUDE.md` (with markers)

After installation, coaching is **OFF**. To enable:
```
/coach on
```
or
```bash
~/.claude/user-capability-coach/coach enable
```

## Installing for Codex

```bash
# Set the target project root
export COACH_PROJECT_ROOT=/path/to/your/project
bash adapters/codex/install.sh
```

This will:
1. Copy skill files to `.agents/skills/`
2. Copy CLI tools to `.agents/skills/user-capability-coach/`
3. Initialize the SQLite database
4. Append the snippet to `AGENTS.md`

## Installing for both platforms

```bash
bash adapters/claude-code/install.sh
export COACH_PROJECT_ROOT=/path/to/project
bash adapters/codex/install.sh
```

Both platforms share the same detection logic and templates but use separate databases.

## Uninstalling

```bash
# Claude Code
bash adapters/claude-code/uninstall.sh

# Codex
export COACH_PROJECT_ROOT=/path/to/project
bash adapters/codex/uninstall.sh
```

The uninstall script will:
- Remove skill directories
- Remove the snippet from CLAUDE.md / AGENTS.md (using the `<!-- user-capability-coach:start/end -->` markers)
- Ask whether to delete the local database

## Using the `coach` CLI directly

After installation, the `coach` CLI is available at:

| Platform | Path |
|----------|------|
| Claude Code | `~/.claude/user-capability-coach/coach` |
| `~/.local/bin` symlink | `coach` (if `~/.local/bin` exists) |
| Codex | `python3 .agents/skills/user-capability-coach/tools/cli.py` |

## Verification

```bash
coach status
# Expected output:
# Prompt Coach status: off
# Long-term memory: off
# Proactive tips this week: 0
# Retrospective reminders this week: 0

coach enable
# Expected: first-use disclosure + "observation period ends: YYYY-MM-DD"

coach status
# Expected: mode=light

coach disable
# Expected: "Prompt Coach is off."
```

## Migrating data between platforms

```bash
# Export from Claude Code
~/.claude/user-capability-coach/coach memory export > coaching_backup.jsonl

# Import on the target platform (round-trip compatible)
coach memory import < coaching_backup.jsonl
```

v1 does not auto-merge cross-platform data — the import is idempotent (de-duplicates by observation id / pattern primary key), so running `import` against an already-populated DB only adds new rows. Rows missing required fields (session_id, domain) are skipped with an error report.

## Troubleshooting

**"coach: command not found"**  
Run `~/.claude/user-capability-coach/coach status` directly, or add `~/.local/bin` to your PATH.

**"No module named 'tools'"**  
Run the CLI from the package directory: `python3 tools/cli.py --help`

**Skills not showing up in Claude Code**  
Check that `~/.claude/skills/prompt-coach/SKILL.md` exists. If not, re-run the install script.

**CLAUDE.md has duplicate snippets**  
The install script is idempotent — it checks for the marker before appending. If you have duplicates, run `uninstall.sh` then `install.sh`.
