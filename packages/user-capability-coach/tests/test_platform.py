"""Tests for platform.py — path resolution."""
import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.platform import detect_platform, data_dir, ensure_data_dir, Platform


def test_data_dir_claude_code():
    d = data_dir(Platform.CLAUDE_CODE)
    assert "claude" in str(d)
    assert "user-capability-coach" in str(d)


def test_data_dir_codex():
    d = data_dir(Platform.CODEX)
    assert "user-capability-coach" in str(d)


def test_data_dir_with_profile(tmp_path):
    d = data_dir(profile=str(tmp_path))
    assert d == tmp_path


def test_ensure_data_dir_creates_dir(tmp_path):
    profile = str(tmp_path / "new_profile")
    d = ensure_data_dir(profile=profile)
    assert d.exists()
    assert d.is_dir()


def test_detect_platform_returns_enum():
    p = detect_platform()
    assert isinstance(p, Platform)
