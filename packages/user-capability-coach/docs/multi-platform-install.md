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

## Installing for Codex (project-local)

```bash
# Set the target project root
export COACH_PROJECT_ROOT=/path/to/your/project
bash adapters/codex/install.sh
```

This will:
1. Copy skill files to `.agents/skills/`
2. Copy CLI tools to `.agents/skills/user-capability-coach/`
3. Create a project-local wrapper at `.agents/skills/user-capability-coach/coach`
4. Initialize the SQLite database
5. Append the snippet to `AGENTS.md`

## Installing for Codex (global shared install)

```bash
export COACH_CODEX_SCOPE=global
bash adapters/codex/install.sh
```

This will:
1. Copy skill files to `$CODEX_HOME/skills/` (default: `~/.codex/skills/`)
2. Copy CLI tools to `$CODEX_HOME/skills/user-capability-coach/`
3. Create a global wrapper at `$CODEX_HOME/skills/user-capability-coach/coach`
4. Initialize the shared SQLite database
5. Append the snippet to `$CODEX_HOME/AGENTS.md`

Global Codex install is the best choice when you want the same coach to apply across repos. Project-local and global Codex installs can coexist.

## Installing for both platforms

```bash
bash adapters/claude-code/install.sh
export COACH_CODEX_SCOPE=global
bash adapters/codex/install.sh
```

Claude Code and Codex still use separate host directories, but Codex global mode shares one coach install across repos.

## Installing for both Codex scopes

```bash
export COACH_PROJECT_ROOT=/path/to/project
bash adapters/codex/install.sh

export COACH_CODEX_SCOPE=global
bash adapters/codex/install.sh
```

Use this only if you intentionally want a repo-pinned Codex install and a global fallback.

## Uninstalling

```bash
# Claude Code
bash adapters/claude-code/uninstall.sh

# Codex project-local
export COACH_PROJECT_ROOT=/path/to/project
bash adapters/codex/uninstall.sh

# Codex global
export COACH_CODEX_SCOPE=global
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
| Codex project-local | `.agents/skills/user-capability-coach/coach` |
| Codex global | `$CODEX_HOME/skills/user-capability-coach/coach` |

## Verification

```bash
# Claude Code
~/.claude/user-capability-coach/coach status

# Codex project-local
.agents/skills/user-capability-coach/coach status

# Codex global
~/.codex/skills/user-capability-coach/coach status
```

Expected output:
```text
Prompt Coach status: off
Long-term memory: off
Checks this week: 0
Visible coaching this week: 0
Silent decisions this week: 0
Proactive tips this week: 0
Retrospective reminders this week: 0
```

To enable:
```bash
# Claude Code
~/.claude/user-capability-coach/coach enable

# Codex project-local
.agents/skills/user-capability-coach/coach enable

# Codex global
~/.codex/skills/user-capability-coach/coach enable
```

## Migrating data between platforms

```bash
# Export from Claude Code
~/.claude/user-capability-coach/coach memory export > coaching_backup.jsonl

# Import into Codex global (round-trip compatible)
~/.codex/skills/user-capability-coach/coach memory import < coaching_backup.jsonl
```

v1 does not auto-merge cross-platform data — the import is idempotent (de-duplicates by observation id / pattern primary key), so running `import` against an already-populated DB only adds new rows. Rows missing required fields (session_id, domain) are skipped with an error report.

## Troubleshooting

**Codex is still using a project-local install**  
If you want shared behavior across repos, reinstall with `COACH_CODEX_SCOPE=global`.

**"coach: command not found"**  
Run the wrapper directly, for example `~/.claude/user-capability-coach/coach status` or `~/.codex/skills/user-capability-coach/coach status`.

**"No module named 'tools'"**  
Run the generated wrapper (`.../user-capability-coach/coach`) instead of calling `python3 tools/cli.py` from an arbitrary directory.

**Skills not showing up in Codex**  
Check that either `.agents/skills/prompt-coach/SKILL.md` or `~/.codex/skills/prompt-coach/SKILL.md` exists, depending on the install scope you chose. If not, re-run the install script.

**AGENTS.md has duplicate snippets**  
The install script is idempotent — it refreshes the marked block in place. If you have duplicates, run `uninstall.sh` then `install.sh`.
