"""Platform detection and path resolution for User Capability Coach."""
from __future__ import annotations
import os
import sys
from enum import Enum
from pathlib import Path


class Platform(str, Enum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    UNKNOWN = "unknown"


def detect_platform() -> Platform:
    """Detect current hosting environment."""
    # Claude Code sets CLAUDE_CODE or ANTHROPIC_CLAUDE_CODE env var
    if os.environ.get("CLAUDE_CODE") or os.environ.get("ANTHROPIC_CLAUDE_CODE"):
        return Platform.CLAUDE_CODE
    # Codex sets CODEX or OPENAI_CODEX
    if os.environ.get("CODEX") or os.environ.get("OPENAI_CODEX"):
        return Platform.CODEX
    # Heuristic: look for ~/.claude directory
    if Path.home().joinpath(".claude").exists():
        return Platform.CLAUDE_CODE
    return Platform.UNKNOWN


def data_dir(platform: Platform | None = None, profile: str | None = None) -> Path:
    """Return the platform-appropriate data directory for coach.db and config."""
    if profile:
        return Path(profile)

    p = platform or detect_platform()

    if p == Platform.CLAUDE_CODE:
        return Path.home() / ".claude" / "user-capability-coach"

    # Codex / generic: follow XDG
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / "user-capability-coach"


def skill_dir(platform: Platform | None = None) -> Path | None:
    """Return the directory where SKILL.md files should live, or None."""
    p = platform or detect_platform()
    if p == Platform.CLAUDE_CODE:
        return Path.home() / ".claude" / "skills"
    return None


def claude_md_path(platform: Platform | None = None) -> Path | None:
    """Return path to the host's main config file (CLAUDE.md / AGENTS.md)."""
    p = platform or detect_platform()
    if p == Platform.CLAUDE_CODE:
        return Path.home() / ".claude" / "CLAUDE.md"
    return None


def ensure_data_dir(platform: Platform | None = None, profile: str | None = None) -> Path:
    """Create data dir if missing and return it."""
    d = data_dir(platform, profile)
    d.mkdir(parents=True, exist_ok=True)
    return d
