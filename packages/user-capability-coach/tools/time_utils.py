"""Helpers for parsing persisted timestamps safely.

Config files or imported SQLite rows may contain ISO timestamps without an
explicit offset (legacy data, hand-edits, external imports). Python treats
those as offset-naive datetimes, which then crash when compared against the
UTC-aware timestamps we generate internally.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_datetime_utc(raw: str | datetime | None) -> datetime | None:
    """Parse an ISO datetime and normalize it to UTC.

    Naive timestamps are treated as UTC for backwards compatibility with
    legacy config/database values written without a timezone suffix.
    Invalid values return None so callers can degrade gracefully.
    """
    if raw is None:
        return None

    dt: datetime
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    else:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
