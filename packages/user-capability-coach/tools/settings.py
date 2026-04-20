"""Read and write coach configuration (config.json next to coach.db)."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .platform import Platform, ensure_data_dir
from .taxonomy import CoachMode
from .time_utils import parse_iso_datetime_utc


DEFAULT_CONFIG: dict[str, Any] = {
    "mode": CoachMode.OFF.value,
    "memory_enabled": False,
    "sensitive_logging_enabled": False,
    "max_proactive_per_7d": 2,
    "max_retrospective_per_7d": 1,
    "observation_period_ends_at": None,
    "last_updated_at": None,
    "platform": None,
    "first_use_disclosed": False,
    # ISO timestamp of the last time the user said "don't remind me" or
    # similar dismissal. Policy treats a dismissal within 7 days as grounds
    # to downgrade to silent_rewrite for the rest of the window.
    "last_dismissed_at": None,
}


DISMISSAL_WINDOW_DAYS = 7
# Backwards-compat alias (was referenced as a private name)
_DISMISSAL_WINDOW_DAYS = DISMISSAL_WINDOW_DAYS


def record_dismissal(
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    cfg = load(platform, profile)
    cfg["last_dismissed_at"] = datetime.now(timezone.utc).isoformat()
    save(cfg, platform, profile)
    return cfg


def is_recently_dismissed(
    platform: Platform | None = None,
    profile: str | None = None,
) -> bool:
    cfg = load(platform, profile)
    raw = cfg.get("last_dismissed_at")
    dismissed_at = parse_iso_datetime_utc(raw)
    if dismissed_at is None:
        return False
    return (datetime.now(timezone.utc) - dismissed_at) < timedelta(days=DISMISSAL_WINDOW_DAYS)


def _config_path(platform: Platform | None = None, profile: str | None = None) -> Path:
    return ensure_data_dir(platform, profile) / "config.json"


def load(platform: Platform | None = None, profile: str | None = None) -> dict[str, Any]:
    path = _config_path(platform, profile)
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with path.open() as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config.json root is not an object")
    except (json.JSONDecodeError, ValueError, OSError):
        # Corrupt or unreadable config — fall back to defaults. Don't
        # overwrite the file automatically; let the next explicit
        # `save()` recover the canonical form so we don't lose whatever
        # the user had there in case they're hand-editing.
        return dict(DEFAULT_CONFIG)
    # Fill missing keys with defaults
    for k, v in DEFAULT_CONFIG.items():
        data.setdefault(k, v)
    return data


def save(cfg: dict[str, Any], platform: Platform | None = None, profile: str | None = None) -> None:
    """Atomically persist config — write to a sibling tmp file then
    rename, so a crash mid-write or a concurrent reader never sees a
    truncated / half-written config.json."""
    path = _config_path(platform, profile)
    cfg["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, default=str))
    tmp.replace(path)  # atomic on POSIX


def get_mode(platform: Platform | None = None, profile: str | None = None) -> CoachMode:
    cfg = load(platform, profile)
    return CoachMode(cfg.get("mode", CoachMode.OFF.value))


def set_mode(
    mode: CoachMode,
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Set the coach mode without touching memory_enabled. Memory has
    its own opt-in via set_memory() — changing mode never turns memory
    on or off."""
    cfg = load(platform, profile)
    cfg["mode"] = mode.value
    save(cfg, platform, profile)
    return cfg


def enable(platform: Platform | None = None, profile: str | None = None) -> dict[str, Any]:
    """Switch to light mode. Does NOT start the observation period —
    plan says "长期记忆首次开启后的一段时间", i.e. the 14-day window
    starts when memory is first enabled, not when coaching mode is. A
    user can sit in light mode with memory off for weeks; when they
    later turn memory on, they should still get a fresh 14-day window.

    Also clears any active dismissal window — if the user explicitly
    asks to re-enable coaching, they're opting back in.
    """
    cfg = load(platform, profile)
    cfg["mode"] = CoachMode.LIGHT.value
    cfg["last_dismissed_at"] = None
    save(cfg, platform, profile)
    return cfg


def disable(platform: Platform | None = None, profile: str | None = None) -> dict[str, Any]:
    cfg = load(platform, profile)
    cfg["mode"] = CoachMode.OFF.value
    save(cfg, platform, profile)
    return cfg


def set_memory(
    enabled: bool,
    platform: Platform | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    cfg = load(platform, profile)
    cfg["memory_enabled"] = enabled
    if enabled and not cfg.get("observation_period_ends_at"):
        ends = datetime.now(timezone.utc) + timedelta(days=14)
        cfg["observation_period_ends_at"] = ends.isoformat()
    save(cfg, platform, profile)
    return cfg


def mark_first_use_disclosed(
    platform: Platform | None = None,
    profile: str | None = None,
) -> None:
    cfg = load(platform, profile)
    cfg["first_use_disclosed"] = True
    save(cfg, platform, profile)


def observation_period_ends_at(
    platform: Platform | None = None,
    profile: str | None = None,
) -> datetime | None:
    cfg = load(platform, profile)
    return parse_iso_datetime_utc(cfg.get("observation_period_ends_at"))
