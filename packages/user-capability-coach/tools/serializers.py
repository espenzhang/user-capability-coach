"""Safe JSON serialization for CLI input/output.

Agents pass structured data via stdin JSON, not via shell arguments,
to avoid injection and quoting issues.
"""
from __future__ import annotations
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any


def read_stdin_json() -> dict[str, Any]:
    """Read a JSON object from stdin. Raises SystemExit on parse error
    or on a non-object JSON value (e.g. a bare string or array) —
    downstream CLI code calls `.get(...)` on the result."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"serializers: invalid JSON on stdin: {e}\n")
        sys.exit(1)
    if not isinstance(data, dict):
        sys.stderr.write(
            f"serializers: expected a JSON object on stdin, got "
            f"{type(data).__name__}\n"
        )
        sys.exit(1)
    return data


def write_stdout_json(data: Any) -> None:
    """Write JSON to stdout, handling dataclasses and enums."""
    if is_dataclass(data) and not isinstance(data, type):
        data = asdict(data)
    print(json.dumps(data, default=_default, indent=2))


def _default(obj: Any) -> Any:
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


def safe_json(data: Any) -> str:
    return json.dumps(data, default=_default)
