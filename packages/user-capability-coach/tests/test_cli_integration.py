from __future__ import annotations

"""CLI integration tests — run the actual coach CLI as a subprocess.

These tests verify that the CLI's stdin/stdout contract works correctly,
that mode=off suppresses everything, and that the main commands return
well-formed JSON.
"""
import json
import os
import subprocess
import sys
import pytest
import tempfile
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent
CLI = str(PACKAGE_DIR / "tools" / "cli.py")
CODEX_INSTALL = str(PACKAGE_DIR / "adapters" / "codex" / "install.sh")
CODEX_UNINSTALL = str(PACKAGE_DIR / "adapters" / "codex" / "uninstall.sh")
CLAUDE_INSTALL = str(PACKAGE_DIR / "adapters" / "claude-code" / "install.sh")


def run_coach(*args, stdin_data: str | None = None, profile: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, CLI]
    if profile:
        cmd += ["--profile", profile]
    cmd += list(args)
    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
    )


def run_codex_install(
    tmp_path: Path,
    *,
    scope: str,
    project_root: Path | None = None,
) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    xdg_home = tmp_path / "xdg-data"
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    codex_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "XDG_DATA_HOME": str(xdg_home),
        "COACH_CODEX_SCOPE": scope,
    })
    if project_root is not None:
        env["COACH_PROJECT_ROOT"] = str(project_root)
    result = subprocess.run(
        ["bash", CODEX_INSTALL],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PACKAGE_DIR),
    )
    return result, env


def run_codex_uninstall(
    *,
    env: dict[str, str],
    input_text: str = "n\n",
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", CODEX_UNINSTALL],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PACKAGE_DIR),
    )


def run_claude_install(tmp_path: Path) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", CLAUDE_INSTALL],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PACKAGE_DIR),
    )
    return result, env


@pytest.fixture
def profile(tmp_path):
    profile_dir = str(tmp_path)
    run_coach("enable", profile=profile_dir)
    run_coach("disable", profile=profile_dir)
    return profile_dir


class TestSelectActionCLI:
    def test_mode_off_returns_none(self, profile):
        """When mode=off, select-action must return action=none."""
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "cli-test-001"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] == "none"
        assert data["coaching_text"] == ""

    def test_mode_light_returns_tip(self, profile):
        """When mode=light, a weak prompt should return a coaching tip."""
        run_coach("enable", profile=profile)
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "cli-test-002"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] in ("post_answer_tip", "pre_answer_micro_nudge", "silent_rewrite")

    def test_sensitive_returns_none(self, profile):
        """Sensitive prompt must be suppressed regardless of mode."""
        run_coach("enable", profile=profile)
        payload = json.dumps({"text": "I've been feeling very depressed and anxious", "session_id": "cli-test-003"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] == "none"
        assert data["is_sensitive"] is True

    def test_good_prompt_returns_silent_rewrite(self, profile):
        """A clear prompt with output contract should return silent_rewrite."""
        run_coach("enable", profile=profile)
        payload = json.dumps({"text": "Convert this JSON to CSV format", "session_id": "cli-test-004"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] == "silent_rewrite"
        assert data["issue_type"] is None

    def test_output_has_required_fields(self, profile):
        """select-action output must always have all required fields."""
        run_coach("enable", profile=profile)
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "cli-test-005"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        required = {"action", "issue_type", "reason", "suppressed_reason", "coaching_text", "domain", "is_sensitive"}
        assert required.issubset(data.keys())


class TestSelectActionBareText:
    """CLI-arg mode: `coach select-action --text X --session-id Y`.

    This is the low-friction post-hoc path. A single call must:
      1. Return the same decision JSON as stdin mode,
      2. Record a decision_event,
      3. Auto-record an observation (unless --no-record or mode=off or good prompt),
      4. NOT read stdin at all.
    """

    def _db_counts(self, profile):
        import sqlite3
        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        out = {
            "decisions": con.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0],
            "observations": con.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "session_turns": con.execute("SELECT COUNT(*) FROM session_turns").fetchone()[0],
            "interventions": con.execute("SELECT COUNT(*) FROM intervention_events").fetchone()[0],
        }
        con.close()
        return out

    def test_bare_text_returns_valid_output(self, profile):
        """--text mode must return the same required fields as stdin mode."""
        run_coach("enable", profile=profile)
        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档", "--session-id", "bare-001",
            profile=profile,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        required = {"action", "issue_type", "reason", "suppressed_reason",
                    "coaching_text", "domain", "is_sensitive", "auto_recorded"}
        assert required.issubset(data.keys())
        assert data["auto_recorded"] is True

    def test_bare_text_auto_records_observation(self, profile):
        """A weak prompt in light mode must produce one decision + one observation."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        before = self._db_counts(profile)
        run_coach(
            "select-action", "--text", "帮我写一个接口文档", "--session-id", "bare-002",
            profile=profile,
        )
        after = self._db_counts(profile)
        assert after["decisions"] == before["decisions"] + 1
        assert after["observations"] == before["observations"] + 1
        assert after["session_turns"] == before["session_turns"] + 1

    def test_bare_text_no_record_flag_skips_observation(self, profile):
        """--no-record must record a decision but NOT an observation."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        before = self._db_counts(profile)
        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档",
            "--session-id", "bare-003", "--no-record",
            profile=profile,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["auto_recorded"] is False
        after = self._db_counts(profile)
        assert after["decisions"] == before["decisions"] + 1
        assert after["observations"] == before["observations"]

    def test_bare_text_dry_run_has_no_persistence_side_effects(self, profile):
        """--dry-run lets diagnostics inspect the selected action without
        consuming visible-tip budget or writing any tracking rows."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        before = self._db_counts(profile)

        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档",
            "--session-id", "bare-dry-run", "--dry-run",
            profile=profile,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["action"] == "post_answer_tip"
        assert data["coaching_text"]
        assert data["auto_recorded"] is False
        assert data["dry_run"] is True
        after = self._db_counts(profile)
        assert after == before

    def test_bare_text_good_prompt_silent_rewrite(self, profile):
        """A good prompt auto-records a 'silent_rewrite' observation (for data
        completeness) but emits no visible tip."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        result = run_coach(
            "select-action", "--text", "Convert this JSON to CSV format",
            "--session-id", "bare-004",
            profile=profile,
        )
        data = json.loads(result.stdout)
        assert data["action"] == "silent_rewrite"
        assert data["coaching_text"] == ""
        assert data["auto_recorded"] is True

    def test_bare_text_mode_off_no_persistence(self, profile):
        """mode=off: --text returns action=none and writes NOTHING to the DB."""
        before = self._db_counts(profile)
        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档",
            "--session-id", "bare-005",
            profile=profile,
        )
        data = json.loads(result.stdout)
        assert data["action"] == "none"
        after = self._db_counts(profile)
        # mode=off: decision + observation both suppressed
        assert after == before

    def test_bare_text_sensitive_suppressed(self, profile):
        """Sensitive prompt must return none regardless of enablement."""
        run_coach("enable", profile=profile)
        result = run_coach(
            "select-action", "--text", "I've been feeling very depressed",
            "--session-id", "bare-006",
            profile=profile,
        )
        data = json.loads(result.stdout)
        assert data["action"] == "none"
        assert data["is_sensitive"] is True

    def test_bare_text_sensitive_memory_off_records_shadow_session_turn(self, profile):
        """With memory disabled, sensitive turns may be counted locally for
        session mechanics but must stay shadow_only so they cannot feed
        visible pattern nudges or contradict the privacy contract."""
        import sqlite3

        run_coach("enable", profile=profile)
        result = run_coach(
            "select-action", "--text", "I've been feeling very depressed",
            "--session-id", "sensitive-memory-off",
            profile=profile,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["action"] == "none"
        assert data["is_sensitive"] is True

        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT domain, issue_type, shadow_only FROM session_turns "
            "WHERE session_id='sensitive-memory-off'"
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0]["domain"] == "sensitive"
        assert rows[0]["issue_type"] is None
        assert rows[0]["shadow_only"] == 1

    def test_bare_text_missing_session_id_generates_fallback(self, profile):
        """Missing --session-id falls back to a one-shot anon-* id + stderr warn."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档",
            profile=profile,
        )
        assert result.returncode == 0
        assert "missing session_id" in result.stderr

    def test_stdin_mode_does_not_auto_record(self, profile):
        """Stdin-JSON mode must NOT auto-record observations (backward compat).
        Callers are responsible for a separate record-observation call."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        before = self._db_counts(profile)
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "stdin-compat-001"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data.get("auto_recorded") is False
        after = self._db_counts(profile)
        assert after["decisions"] == before["decisions"] + 1
        assert after["observations"] == before["observations"]  # unchanged

    def test_agent_json_flag_works_same_as_stdin(self, profile):
        """--agent-json accepts the classification inline (for agents that
        want to pass judgment via flag instead of stdin)."""
        run_coach("enable", profile=profile)
        agent_cls = json.dumps({
            "issue_type": "missing_output_contract",
            "confidence": 0.9,
            "severity": 0.8,
            "fixability": 0.9,
            "domain": "coding",
        })
        result = run_coach(
            "select-action", "--text", "帮我写一个接口文档",
            "--session-id", "bare-007", "--agent-json", agent_cls,
            profile=profile,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["detection_source"] == "agent"


class TestRecordObservationCLI:
    def test_skipped_when_mode_off(self, profile):
        """record-observation returns status=skipped when mode=off."""
        payload = json.dumps({
            "session_id": "cli-obs-001",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
        })
        result = run_coach("record-observation", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "skipped"

    def test_not_persisted_when_memory_disabled(self, profile):
        """Even when mode=light, if memory_enabled=false we must NOT write
        an observation row. A single suppressed intervention event is allowed."""
        run_coach("enable", profile=profile)
        # memory is off by default
        payload = json.dumps({
            "session_id": "cli-nomem-001",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
            "evidence_summary": "should NOT be stored",
        })
        result = run_coach("record-observation", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "skipped"
        assert data["reason"] == "memory_disabled"

        # Verify DB has zero observations
        import sqlite3
        db_path = str(Path(profile) / "coach.db")
        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        con.close()
        assert count == 0, "observations must not be persisted when memory disabled"

    def test_writes_when_memory_enabled(self, profile):
        """record-observation writes an observation when mode=light and memory=on."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        payload = json.dumps({
            "session_id": "cli-obs-002",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
            "severity": 0.65,
            "confidence": 0.80,
            "evidence_summary": "coding task lacked output format",
        })
        result = run_coach("record-observation", stdin_data=payload, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert "observation_id" in data

    def test_flag_mode_basic(self, profile):
        """record-observation accepts CLI flags (no stdin)."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        result = run_coach(
            "record-observation",
            "--session-id", "flag-001",
            "--action", "post_answer_tip",
            "--issue", "missing_output_contract",
            "--domain", "coding",
            "--severity", "0.7",
            "--confidence", "0.8",
            profile=profile,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert "observation_id" in data

    def test_text_only_zero_config_record(self, profile):
        """record-observation --text auto-classifies via detection.
        Running with just --text + --session-id + --action should write an observation
        with issue_type/domain derived from the prompt."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        result = run_coach(
            "record-observation",
            "--text", "帮我写一个接口文档",
            "--session-id", "text-001",
            "--action", "post_answer_tip",
            profile=profile,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        # Verify the written row has the detected issue_type, not a default
        import sqlite3
        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        row = con.execute(
            "SELECT issue_type, domain FROM observations "
            "WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
            ("text-001",),
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == "missing_output_contract"
        assert row[1] == "coding"


class TestVersionFlag:
    """`coach --version` prints the package version; useful for debugging."""

    def test_version_flag_exits_zero(self):
        """--version prints and exits 0 without touching any profile."""
        result = run_coach("--version")
        assert result.returncode == 0
        assert "coach " in result.stdout  # format: "coach <version>"
        # Must not error on missing profile
        assert not result.stderr

    def test_short_v_flag_works(self):
        """-V short form also works."""
        result = run_coach("-V")
        assert result.returncode == 0
        assert "coach " in result.stdout


class TestDoctorCommand:
    """`coach doctor` self-diagnostic: runs 3 test prompts + verifies DB schema."""

    def test_doctor_all_pass_on_fresh_profile(self, profile):
        """A freshly-enabled profile should pass all 4 checks."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        result = run_coach("doctor", profile=profile)
        assert result.returncode == 0, f"doctor failed: {result.stdout}\n{result.stderr}"
        out = result.stdout
        assert "Coach Doctor" in out
        assert "ALL PASS" in out
        assert "✓ config" in out
        assert "✓ db schema" in out
        assert "✓ select-action smoke" in out
        assert "✓ tip generation" in out

    def test_doctor_skips_smoke_when_mode_off(self, profile):
        """mode=off → doctor should still pass (skips smoke test gracefully)."""
        result = run_coach("doctor", profile=profile)
        assert result.returncode == 0, f"doctor failed: {result.stdout}\n{result.stderr}"
        assert "skipped" in result.stdout or "已跳过" in result.stdout

    def test_doctor_cleans_up_its_diagnostic_rows(self, profile):
        """Doctor must NOT leave behind decision rows from its test prompts."""
        import sqlite3
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        before = con.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        con.close()
        run_coach("doctor", profile=profile)
        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        after = con.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        con.close()
        assert after == before, f"doctor leaked {after - before} rows"


class TestCorruptConfigRecovery:
    def test_corrupt_config_falls_back_to_defaults(self, tmp_path):
        """A corrupt config.json must not crash — fall back to defaults."""
        profile_dir = str(tmp_path)
        (tmp_path / "config.json").write_text("not valid json")
        result = run_coach("status", profile=profile_dir)
        assert result.returncode == 0, f"status crashed: {result.stderr}"
        assert "off" in result.stdout.lower()


class TestFreshProfileSmoke:
    """Regression: common commands must work on a fresh profile without
    requiring `coach enable` to have been called first (DB auto-init)."""

    def test_status_on_empty_profile(self, tmp_path):
        """`coach status` works without any prior coach enable call."""
        profile_dir = str(tmp_path)
        result = run_coach("status", profile=profile_dir)
        assert result.returncode == 0, f"status failed: {result.stderr}"
        assert "off" in result.stdout.lower()

    def test_select_action_on_empty_profile(self, tmp_path):
        """`coach select-action` works without prior init."""
        profile_dir = str(tmp_path)
        payload = json.dumps({"text": "test prompt", "session_id": "smoke-001"})
        result = run_coach("select-action", stdin_data=payload, profile=profile_dir)
        assert result.returncode == 0, f"select-action failed: {result.stderr}"
        data = json.loads(result.stdout)
        # mode=off (default) → action=none
        assert data["action"] == "none"


class TestStatusCLI:
    def test_status_shows_off(self, profile):
        """status command shows mode=off after install."""
        result = run_coach("status", profile=profile)
        assert result.returncode == 0
        assert "off" in result.stdout.lower()

    def test_status_shows_light_after_enable(self, profile):
        """status shows light after coach enable."""
        run_coach("enable", profile=profile)
        result = run_coach("status", profile=profile)
        assert result.returncode == 0
        assert "light" in result.stdout.lower()

    def test_reenable_memory_off_does_not_claim_observation_period(self, profile):
        """The 14-day observation period starts only after memory is enabled,
        so repeated enable output must not print a fake N/A deadline."""
        first = run_coach("--lang", "en", "enable", profile=profile)
        assert first.returncode == 0

        second = run_coach("--lang", "en", "enable", profile=profile)
        assert second.returncode == 0
        out = second.stdout.lower()
        assert "observation period ends" not in out
        assert "n/a" not in out
        assert "long-term memory is off" in out

    def test_status_shows_off_after_disable(self, profile):
        """status reverts to off after disable."""
        run_coach("enable", profile=profile)
        run_coach("disable", profile=profile)
        result = run_coach("status", profile=profile)
        assert result.returncode == 0
        assert "off" in result.stdout.lower()

    def test_status_shows_checked_surfaced_and_silent_counts(self, profile):
        run_coach("enable", profile=profile)

        surfaced = json.dumps({
            "text": "帮我写一个接口文档",
            "session_id": "status-visible",
            "agent_classification": {
                "issue_type": "missing_output_contract",
                "confidence": 0.9,
                "severity": 0.8,
                "fixability": 0.9,
                "cost_signal": "output_format_mismatch",
                "domain": "coding",
                "is_sensitive": False,
                "is_urgent": False,
                "evidence_summary": "request lacks output format",
            },
        })
        run_coach("select-action", stdin_data=surfaced, profile=profile)

        silent = json.dumps({
            "text": "Convert this JSON to CSV format",
            "session_id": "status-silent",
            "agent_classification": {
                "issue_type": None,
                "domain": "coding",
                "is_sensitive": False,
                "is_urgent": False,
            },
        })
        run_coach("select-action", stdin_data=silent, profile=profile)

        result = run_coach("--lang", "en", "status", profile=profile)
        assert result.returncode == 0
        out = result.stdout.lower()
        assert "checks this week: 2" in out
        assert "visible coaching this week: 1" in out
        assert "silent decisions this week: 1" in out


class TestCodexInstallScripts:
    def test_generated_agents_directory_is_gitignored(self):
        """Project-local Codex installs are generated runtime state, not
        source. Keeping them untracked prevents stale wrappers and copied
        tools from drifting away from packages/user-capability-coach."""
        gitignore = PACKAGE_DIR.parent.parent / ".gitignore"
        ignored = {
            line.strip()
            for line in gitignore.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        assert ".agents/" in ignored

    def test_project_install_creates_wrapper_and_unambiguous_agents_command(self, tmp_path):
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, env = run_codex_install(tmp_path, scope="project", project_root=project_root)

        assert result.returncode == 0, result.stderr
        wrapper = project_root / ".agents" / "skills" / "user-capability-coach" / "coach"
        agents_md = project_root / "AGENTS.md"
        assert wrapper.exists()
        assert ".agents/skills/user-capability-coach/coach enable" in agents_md.read_text()
        assert "run `coach enable`" not in agents_md.read_text()

        status = subprocess.run(
            [str(wrapper), "status"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(project_root),
        )
        assert status.returncode == 0, status.stderr
        assert "off" in status.stdout.lower()

    def test_global_install_targets_codex_home_and_uses_global_wrapper(self, tmp_path):
        result, env = run_codex_install(tmp_path, scope="global")

        assert result.returncode == 0, result.stderr
        codex_home = Path(env["CODEX_HOME"])
        wrapper = codex_home / "skills" / "user-capability-coach" / "coach"
        agents_md = codex_home / "AGENTS.md"
        assert (codex_home / "skills" / "prompt-coach" / "SKILL.md").exists()
        assert (codex_home / "skills" / "growth-coach" / "SKILL.md").exists()
        assert wrapper.exists()
        content = agents_md.read_text()
        assert f"run `{wrapper} enable`" in content
        assert "run `coach enable`" not in content

        status = subprocess.run(
            [str(wrapper), "status"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(codex_home),
        )
        assert status.returncode == 0, status.stderr
        assert "off" in status.stdout.lower()

    def test_global_uninstall_removes_codex_home_snippet(self, tmp_path):
        install_result, env = run_codex_install(tmp_path, scope="global")
        assert install_result.returncode == 0, install_result.stderr

        result = run_codex_uninstall(env=env)

        assert result.returncode == 0, result.stderr
        codex_home = Path(env["CODEX_HOME"])
        agents_md = codex_home / "AGENTS.md"
        assert "<!-- user-capability-coach:start -->" not in agents_md.read_text()
        assert not (codex_home / "skills" / "user-capability-coach").exists()


class TestClaudeInstallScripts:
    def test_claude_install_copies_tools_and_wrapper_uses_installed_copy(self, tmp_path):
        result, env = run_claude_install(tmp_path)

        assert result.returncode == 0, result.stderr
        home = Path(env["HOME"])
        data_dir = home / ".claude" / "user-capability-coach"
        wrapper = data_dir / "coach"
        installed_cli = data_dir / "tools" / "cli.py"

        assert wrapper.exists()
        assert installed_cli.exists()
        wrapper_text = wrapper.read_text()
        assert str(PACKAGE_DIR) not in wrapper_text
        assert str(installed_cli) in wrapper_text

        status = subprocess.run(
            [str(wrapper), "status"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )
        assert status.returncode == 0, status.stderr
        assert "off" in status.stdout.lower()


class TestMemoryExportCLI:
    def test_export_empty_when_no_observations(self, profile):
        """memory export returns empty string when no data recorded."""
        result = run_coach("memory", "export", profile=profile)
        assert result.returncode == 0
        # Empty or just whitespace
        assert result.stdout.strip() == ""

    def test_export_has_jsonl_after_observation(self, profile):
        """memory export returns JSONL after an observation is recorded."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        payload = json.dumps({
            "session_id": "export-test",
            "domain": "coding",
            "issue_type": "overloaded_request",
            "action_taken": "post_answer_tip",
            "evidence_summary": "multi-phase task",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        result = run_coach("memory", "export", profile=profile)
        assert result.returncode == 0
        lines = [l for l in result.stdout.strip().split("\n") if l]
        assert len(lines) >= 1
        obs = json.loads(lines[0])
        assert obs.get("session_id") == "export-test"


class TestMemoryImportCLI:
    def test_import_roundtrip(self, profile, tmp_path):
        """Export → wipe → import restores observations and patterns."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        payload = json.dumps({
            "session_id": "roundtrip-1",
            "domain": "coding",
            "issue_type": "missing_goal",
            "action_taken": "post_answer_tip",
            "evidence_summary": "roundtrip test",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        # Export
        exp = run_coach("memory", "export", profile=profile)
        assert exp.returncode == 0
        exported = exp.stdout

        # Wipe
        run_coach("forget-all", profile=profile)
        check = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert check["patterns"] == []

        # Import back
        result = run_coach("memory", "import", stdin_data=exported, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["imported_observations"] >= 1

    def test_import_empty_input(self, profile):
        """Importing empty input does not error."""
        result = run_coach("memory", "import", stdin_data="", profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["imported_observations"] == 0

    def test_cross_profile_migration(self, tmp_path):
        """Simulate migrating data from profile A to profile B via export/import."""
        prof_a = str(tmp_path / "profile_a")
        prof_b = str(tmp_path / "profile_b")
        Path(prof_a).mkdir()
        Path(prof_b).mkdir()

        # Seed profile A with observations across 3 sessions
        run_coach("enable", profile=prof_a)
        run_coach("set-memory", "on", profile=prof_a)
        for i in range(3):
            payload = json.dumps({
                "session_id": f"migrate_sess_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65, "confidence": 0.8,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "migration test",
            })
            run_coach("record-observation", stdin_data=payload, profile=prof_a)

        a_patterns = json.loads(run_coach("show-patterns", profile=prof_a).stdout)
        assert a_patterns["patterns"][0]["evidence_count"] == 3

        # Export from A
        exp = run_coach("memory", "export", profile=prof_a).stdout

        # Import into B (which is empty)
        run_coach("enable", profile=prof_b)
        run_coach("set-memory", "on", profile=prof_b)
        result = run_coach("memory", "import", stdin_data=exp, profile=prof_b)
        data = json.loads(result.stdout)
        assert data["status"] == "ok"

        # Profile B should now have the same pattern
        b_patterns = json.loads(run_coach("show-patterns", profile=prof_b).stdout)
        assert len(b_patterns["patterns"]) == 1
        assert b_patterns["patterns"][0]["evidence_count"] == 3
        assert b_patterns["patterns"][0]["issue_type"] == "missing_output_contract"

    def test_cross_profile_migration_preserves_status_counters(self, tmp_path):
        prof_a = str(tmp_path / "profile_a_status")
        prof_b = str(tmp_path / "profile_b_status")
        Path(prof_a).mkdir()
        Path(prof_b).mkdir()

        run_coach("enable", profile=prof_a)
        select_payload = json.dumps({
            "text": "帮我写一个接口文档",
            "session_id": "migrate-status",
            "agent_classification": {
                "issue_type": "missing_output_contract",
                "confidence": 0.9,
                "severity": 0.8,
                "fixability": 0.9,
                "cost_signal": "output_format_mismatch",
                "domain": "coding",
                "is_sensitive": False,
                "is_urgent": False,
                "evidence_summary": "request lacks output format",
            },
        })
        selected = json.loads(run_coach("select-action", stdin_data=select_payload, profile=prof_a).stdout)
        record_payload = json.dumps({
            "session_id": "migrate-status",
            "domain": selected["domain"],
            "issue_type": selected["issue_type"],
            "action_taken": selected["action"],
            "severity": 0.8,
            "confidence": 0.9,
            "fixability": 0.9,
            "cost_signal": "output_format_mismatch",
            "evidence_summary": "request lacks output format",
        })
        run_coach("record-observation", stdin_data=record_payload, profile=prof_a)

        status_a = run_coach("--lang", "en", "status", profile=prof_a).stdout
        assert "Checks this week: 1" in status_a
        assert "Visible coaching this week: 1" in status_a

        exported = run_coach("memory", "export", profile=prof_a).stdout
        run_coach("enable", profile=prof_b)
        run_coach("memory", "import", stdin_data=exported, profile=prof_b)

        status_b = run_coach("--lang", "en", "status", profile=prof_b).stdout
        assert "Checks this week: 1" in status_b
        assert "Visible coaching this week: 1" in status_b

    def test_cross_profile_migration_preserves_why_reminded_history(self, tmp_path):
        prof_a = str(tmp_path / "profile_a_why")
        prof_b = str(tmp_path / "profile_b_why")
        Path(prof_a).mkdir()
        Path(prof_b).mkdir()

        run_coach("enable", profile=prof_a)
        run_coach("set-memory", "on", profile=prof_a)

        for i in range(4):
            payload = json.dumps({
                "session_id": f"why_seed_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=prof_a)

        cfg_path = Path(prof_a) / "config.json"
        cfg = json.loads(cfg_path.read_text())
        from datetime import datetime, timezone, timedelta
        cfg["observation_period_ends_at"] = (
            datetime.now(timezone.utc) - timedelta(days=15)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        selected = json.loads(run_coach(
            "select-action",
            stdin_data=json.dumps({"text": "Convert this JSON to CSV format", "session_id": "why-clean"}),
            profile=prof_a,
        ).stdout)
        assert selected["action"] == "retrospective_reminder"

        record_payload = json.dumps({
            "session_id": "why-emit",
            "domain": selected["domain"],
            "issue_type": selected["issue_type"],
            "action_taken": selected["action"],
            "severity": 0.65,
            "confidence": 0.80,
            "evidence_summary": "retrospective reminder shown",
        })
        run_coach("record-observation", stdin_data=record_payload, profile=prof_a)

        why_a = run_coach("why-reminded", profile=prof_a).stdout
        assert "missing_output_contract" in why_a.lower() or "output" in why_a.lower()

        exported = run_coach("memory", "export", profile=prof_a).stdout
        run_coach("enable", profile=prof_b)
        run_coach("set-memory", "on", profile=prof_b)
        run_coach("memory", "import", stdin_data=exported, profile=prof_b)

        why_b = run_coach("why-reminded", profile=prof_b).stdout
        assert "missing_output_contract" in why_b.lower() or "output" in why_b.lower()

    def test_import_tolerates_bad_lines(self, profile):
        """Malformed JSON or rows missing required fields are skipped,
        good rows still import."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        mixed = (
            '{"session_id":"good-1","domain":"coding",'
            '"issue_type":"missing_goal","action_taken":"post_answer_tip"}\n'
            'this is not json\n'
            '{"malformed":\n'
            '{"no_session":"bad","domain":"coding"}\n'
        )
        result = run_coach("memory", "import", stdin_data=mixed, profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["imported_observations"] == 1
        assert data["skipped"] >= 1
        assert len(data["errors"]) >= 1


class TestStrictModeCLI:
    def test_set_mode_strict_persists(self, profile):
        run_coach("enable", profile=profile)
        result = run_coach("set-mode", "strict", profile=profile)
        assert result.returncode == 0
        status = run_coach("status", profile=profile).stdout
        assert "strict" in status

    def test_set_mode_invalid_lists_strict(self, profile):
        """Invalid value errors out with strict listed as a choice."""
        result = run_coach("set-mode", "aggressive", profile=profile)
        assert result.returncode != 0
        assert "strict" in result.stderr.lower()


class TestDismissCLI:
    def test_dismiss_downgrades_action_to_silent_rewrite(self, profile):
        """After `coach dismiss`, a weak prompt should not get post_answer_tip."""
        run_coach("enable", profile=profile)
        # Before dismiss: weak prompt gets a visible tip
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "dismiss-001"})
        before = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert before["action"] in ("post_answer_tip", "pre_answer_micro_nudge")

        # Record dismissal
        result = run_coach("dismiss", profile=profile)
        assert result.returncode == 0

        # Now same prompt → silent_rewrite
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "dismiss-002"})
        after = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert after["action"] == "silent_rewrite"
        assert after["suppressed_reason"] == "user_dismissed"

    def test_reenable_clears_dismiss_window(self, profile):
        """coach enable explicitly opts user back in, clearing dismissal."""
        run_coach("enable", profile=profile)
        run_coach("dismiss", profile=profile)

        # Confirm dismissed
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "reen-001"})
        d = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert d["suppressed_reason"] == "user_dismissed"

        # Re-enable explicitly
        run_coach("enable", profile=profile)

        # Dismissal should be cleared
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "reen-002"})
        d = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert d["action"] in ("post_answer_tip", "pre_answer_micro_nudge")
        assert d["suppressed_reason"] != "user_dismissed"

    def test_dismiss_window_expires_after_7_days(self, profile):
        """After 7 days, dismissed state expires and coaching returns."""
        run_coach("enable", profile=profile)
        run_coach("dismiss", profile=profile)

        # Backdate last_dismissed_at to >7 days ago
        cfg_path = Path(profile) / "config.json"
        cfg = json.loads(cfg_path.read_text())
        from datetime import datetime, timezone, timedelta
        cfg["last_dismissed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        # Same weak prompt now gets post_answer_tip again
        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "dismiss-003"})
        data = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert data["action"] in ("post_answer_tip", "pre_answer_micro_nudge")
        assert data["suppressed_reason"] != "user_dismissed"

    def test_naive_dismiss_timestamp_does_not_crash_select_action(self, profile):
        """Older/manual configs may store a naive ISO timestamp.

        select-action should treat it as UTC instead of crashing on
        offset-aware vs offset-naive datetime arithmetic.
        """
        run_coach("enable", profile=profile)
        cfg_path = Path(profile) / "config.json"
        cfg = json.loads(cfg_path.read_text())

        from datetime import datetime, timezone
        cfg["last_dismissed_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        payload = json.dumps({"text": "帮我写一个接口文档", "session_id": "dismiss-naive"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["suppressed_reason"] == "user_dismissed"


class TestLanguageOverride:
    def test_lang_zh_in_disable_ack(self, profile):
        """--lang zh should produce a Chinese off-acknowledgement."""
        run_coach("enable", profile=profile)
        cmd = [sys.executable, CLI, "--profile", profile, "--lang", "zh", "disable"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        # Chinese disable message contains characters that English doesn't
        assert "教练" in result.stdout or "关闭" in result.stdout

    def test_lang_env_auto_zh(self, profile):
        """COACH_LANG=zh env var forces Chinese output."""
        import os
        env = os.environ.copy()
        env["COACH_LANG"] = "zh"
        cmd = [sys.executable, CLI, "--profile", profile, "enable"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert result.returncode == 0
        # First-use disclosure in Chinese has unmistakable CJK chars
        assert any("\u4e00" <= ch <= "\u9fff" for ch in result.stdout)


class TestForgetCLI:
    def test_forget_pattern_removes_data(self, profile):
        """forget-pattern removes the pattern card."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        payload = json.dumps({
            "session_id": "forget-test",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
            "evidence_summary": "coding task lacked format",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        result = run_coach("forget-pattern", "missing_output_contract", profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"

    def test_show_patterns_empty_after_forget_all(self, profile):
        """show-patterns returns empty list after forget-all."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        payload = json.dumps({
            "session_id": "forget-all-test",
            "domain": "writing",
            "issue_type": "missing_goal",
            "action_taken": "post_answer_tip",
            "evidence_summary": "no goal stated",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)
        run_coach("forget-all", profile=profile)

        result = run_coach("show-patterns", profile=profile)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["patterns"] == []


class TestProactiveBudgetEnforcement:
    """Bug fix regression: recording a post_answer_tip observation must
    write an intervention_event so get_proactive_count_7d reflects the
    shown tip and the 7-day budget can actually be enforced."""

    def test_post_answer_tip_increments_proactive_count(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        import sqlite3
        db_path = str(Path(profile) / "coach.db")

        def _count_intervention_events() -> int:
            con = sqlite3.connect(db_path)
            c = con.execute(
                "SELECT COUNT(*) FROM intervention_events WHERE shown=1 "
                "AND surface='post_answer_tip'"
            ).fetchone()[0]
            con.close()
            return c

        assert _count_intervention_events() == 0

        payload = json.dumps({
            "session_id": "budget-001",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
            "severity": 0.65, "confidence": 0.80,
            "evidence_summary": "post tip shown",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        assert _count_intervention_events() == 1, (
            "post_answer_tip must create an intervention_event so "
            "proactive budget can be enforced"
        )


class TestProactiveBudgetEnforced:
    """End-to-end: after N post_answer_tip observations in 7 days,
    additional weak prompts get silent_rewrite (budget exhausted)."""

    def test_light_budget_exhausted_after_2_tips(self, profile):
        """light mode: budget is 2/7d. 3rd tip in same window gets silent_rewrite."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        # Fire 2 tip observations to exhaust light budget
        for i in range(2):
            payload = json.dumps({
                "session_id": f"budget_e_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65, "confidence": 0.8,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "tip shown",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # 3rd weak prompt: policy must return silent_rewrite, reason=budget_exhausted
        payload = json.dumps({"text": "帮我写另一个接口文档", "session_id": "budget_check"})
        result = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert result["action"] == "silent_rewrite"
        assert result["suppressed_reason"] == "budget_exhausted"


class TestAgentFirstClassification:
    """agent_classification on stdin overrides rule-based detection —
    this is how an agent with full conversation context tells coach
    "I've looked at the history, here's my verdict" without being
    second-guessed by single-turn regex."""

    def test_agent_null_suppresses_rule_false_positive(self, profile):
        """Rule-based would flag missing_output_contract on '帮我写一个接口文档',
        but agent (with prior-turn context) says clean → silent_rewrite."""
        run_coach("enable", profile=profile)
        payload = json.dumps({
            "text": "帮我写一个接口文档",
            "session_id": "agent-null",
            "agent_classification": {
                "issue_type": None,
                "evidence_summary": "prior turn already specified markdown format",
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] == "silent_rewrite"
        assert data["detection_source"] == "agent"

    def test_agent_classification_adopted_over_rules(self, profile):
        """Clean prompt by rules, but agent sees cross-turn issue → coaching."""
        run_coach("enable", profile=profile)
        payload = json.dumps({
            "text": "再来一次",  # rule: VAGUE_ONLY fires → missing_goal
            "session_id": "agent-pick",
            "agent_classification": {
                "issue_type": "missing_output_contract",  # agent's different take
                "confidence": 0.85,
                "severity": 0.75,
                "fixability": 0.9,
                "cost_signal": "output_format_mismatch",
                "domain": "coding",
                "evidence_summary": "referent in prior turn was OK; what's missing is output format",
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["issue_type"] == "missing_output_contract", (
            f"agent's classification should win, got {data['issue_type']}"
        )
        assert data["detection_source"] == "agent"

    def test_agent_flags_urgent_blocks_pre_nudge(self, profile):
        """Agent tags is_urgent=True → never pre_answer_micro_nudge."""
        run_coach("enable", profile=profile)
        run_coach("set-mode", "standard", profile=profile)
        payload = json.dumps({
            "text": "帮我改这段代码",
            "session_id": "agent-urgent",
            "agent_classification": {
                "issue_type": "missing_output_contract",
                "confidence": 0.9, "severity": 0.85,  # would normally be PRE in standard
                "is_urgent": True,
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] != "pre_answer_micro_nudge"

    def test_agent_flags_sensitive_suppresses(self, profile):
        """Agent says is_sensitive=True → action=none regardless of issue_type."""
        run_coach("enable", profile=profile)
        payload = json.dumps({
            "text": "帮我优化这段代码",
            "session_id": "agent-sens",
            "agent_classification": {
                "issue_type": "missing_goal",
                "confidence": 0.9, "severity": 0.9,
                "is_sensitive": True,  # agent spotted sensitive context
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] == "none"

    def test_agent_can_classify_all_promoted_types(self, profile):
        """Post-④: agent can classify as any of the 5 newly-promoted
        types, and coach surfaces them as visible coaching with
        dedicated templates."""
        run_coach("enable", profile=profile)
        for issue_type in (
            "missing_context",
            "missing_constraints",
            "conflicting_instructions",
            "unbound_reference",
            "missing_success_criteria",
        ):
            payload = json.dumps({
                "text": "some prompt",
                "session_id": f"agent-{issue_type}",
                "agent_classification": {
                    "issue_type": issue_type,
                    "confidence": 0.9, "severity": 0.8,
                    "evidence_summary": f"agent says {issue_type}",
                },
            })
            result = run_coach("select-action", stdin_data=payload, profile=profile)
            data = json.loads(result.stdout)
            assert data["action"] == "post_answer_tip", (
                f"{issue_type}: expected post_answer_tip, got {data['action']}"
            )
            assert data["issue_type"] == issue_type
            assert data["coaching_text"], (
                f"{issue_type}: expected non-empty template text"
            )
            assert data["detection_source"] == "agent"

    def test_malformed_agent_classification_falls_back_to_rules(self, profile):
        """Bad enum values in agent payload → warning + rule-based fallback."""
        run_coach("enable", profile=profile)
        payload = json.dumps({
            "text": "帮我写一个接口文档",
            "session_id": "agent-bad",
            "agent_classification": {
                "issue_type": "not_a_real_type",  # invalid enum
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert result.returncode == 0  # didn't error out
        assert data["detection_source"] == "rules", "should fall back"
        # Rules see missing_output_contract → POST tip
        assert data["action"] == "post_answer_tip"
        # Error is surfaced BOTH in stdout (for callers that capture it)
        # AND in stderr (for humans watching the terminal).
        assert "not_a_real_type" in result.stderr
        assert "agent_cls_error" in data
        assert "not_a_real_type" in data["agent_cls_error"]

    def test_no_agent_classification_uses_rules(self, profile):
        """Backwards compat: omit agent_classification → rule path."""
        run_coach("enable", profile=profile)
        payload = json.dumps({
            "text": "帮我写一个接口文档",
            "session_id": "no-agent",
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["detection_source"] == "rules"

    def test_agent_confidence_clamped_to_unit_interval(self, profile):
        """Agent sending confidence=99.9 (bug or attack) gets clamped to 1.0
        so it can't bypass policy's min_confidence gate via large numbers."""
        run_coach("enable", profile=profile)
        # severity clamped from 0.0 up to 0.65 threshold — this one will fail
        # the policy min_severity, so action=silent_rewrite
        payload = json.dumps({
            "text": "some prompt",
            "session_id": "clamp-test",
            "agent_classification": {
                "issue_type": "missing_goal",
                "confidence": 99.9,
                "severity": -5.0,  # below min_severity after clamp
                "fixability": 2.0,
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        # severity clamp → below threshold → no visible coaching
        assert data["action"] == "silent_rewrite"


class TestShadowObservationIsolation:
    """Shadow observations accumulate in DB but don't affect patterns or budgets."""

    def test_shadow_observation_does_not_touch_patterns(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        for i in range(3):
            payload = json.dumps({
                "session_id": f"shadow_{i}",
                "domain": "coding",
                "issue_type": "missing_goal",
                "action_taken": "silent_rewrite",
                "shadow_only": True,
                "evidence_summary": "shadow obs",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Patterns table should be empty
        patterns = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert patterns["patterns"] == [], (
            "shadow observations must not update patterns"
        )

    def test_shadow_observation_does_not_consume_proactive_budget(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        import sqlite3
        db_path = str(Path(profile) / "coach.db")

        def _proactive_events() -> int:
            con = sqlite3.connect(db_path)
            c = con.execute(
                "SELECT COUNT(*) FROM intervention_events WHERE shown=1 "
                "AND surface IN ('post_answer_tip','pre_answer_micro_nudge')"
            ).fetchone()[0]
            con.close()
            return c

        # Shadow observation with action=post_answer_tip — contradictory but
        # we should still not count it.
        payload = json.dumps({
            "session_id": "shadow_budget",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "post_answer_tip",
            "shadow_only": True,
            "evidence_summary": "shadow — should not count",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)
        assert _proactive_events() == 0


class TestObservationPeriodSemantics:
    """The 14-day observation period must be tied to memory enablement,
    not coach enablement — per plan.md 长期记忆首次开启后有 14 天观察期."""

    def test_enable_without_memory_does_not_start_period(self, tmp_path):
        """coach enable alone should NOT set observation_period_ends_at."""
        profile_dir = str(tmp_path)
        result = run_coach("enable", profile=profile_dir)
        assert result.returncode == 0

        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["observation_period_ends_at"] is None, (
            "observation period must only start when memory is enabled"
        )

    def test_set_memory_on_starts_period(self, tmp_path):
        """coach set-memory on starts the 14-day period."""
        profile_dir = str(tmp_path)
        run_coach("enable", profile=profile_dir)
        run_coach("set-memory", "on", profile=profile_dir)

        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["observation_period_ends_at"] is not None

    def test_late_memory_enable_gets_fresh_period(self, tmp_path):
        """User enables coach, later (past day 14) turns on memory.
        They should get a fresh 14-day window — not an already-expired one."""
        profile_dir = str(tmp_path)
        run_coach("enable", profile=profile_dir)
        # 20 days pass without memory being enabled...
        # (simulated by verifying period is None, then enabling memory)
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["observation_period_ends_at"] is None

        run_coach("set-memory", "on", profile=profile_dir)
        cfg = json.loads((tmp_path / "config.json").read_text())
        from datetime import datetime as _dt, timezone as _tz
        ends = _dt.fromisoformat(cfg["observation_period_ends_at"])
        # Should be ~14 days from now, not in the past
        remaining = (ends - _dt.now(_tz.utc)).days
        assert 13 <= remaining <= 14, (
            f"late memory enable should grant ~14 days, got {remaining}"
        )

    def test_naive_observation_period_timestamp_does_not_crash_retrospective(self, profile):
        """Legacy/manual configs with naive ISO timestamps should still work."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        for i in range(4):
            payload = json.dumps({
                "session_id": f"naive-obs-{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "naive observation period regression",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        cfg_path = Path(profile) / "config.json"
        cfg = json.loads(cfg_path.read_text())

        from datetime import datetime, timezone, timedelta
        cfg["observation_period_ends_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=15)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        payload = json.dumps({
            "text": "Convert this JSON to CSV format",
            "session_id": "retro-naive-period",
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["action"] == "retrospective_reminder"


class TestSessionPatternCLI:
    """End-to-end: record 3 vague turns in one session, then a clean
    prompt → SESSION_PATTERN_NUDGE action with templated text."""

    def test_session_nudge_fires_after_pattern_accumulates(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        for i in range(3):
            payload = json.dumps({
                "session_id": "e2e_sess",
                "domain": "coding",
                "issue_type": "missing_goal",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "high_risk_guess",
                "evidence_summary": f"turn {i}",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        payload = json.dumps({
            "text": "Convert this JSON to CSV format",
            "session_id": "e2e_sess",
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] == "session_pattern_nudge"
        assert data["issue_type"] == "missing_goal"
        assert data["coaching_text"] != ""

    def test_session_nudge_fires_with_memory_disabled(self, profile):
        run_coach("enable", profile=profile)

        for i in range(3):
            payload = json.dumps({
                "session_id": "short_term_only",
                "domain": "coding",
                "issue_type": "missing_goal",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "high_risk_guess",
                "evidence_summary": f"short term only turn {i}",
            })
            result = run_coach("record-observation", stdin_data=payload, profile=profile)
            assert result.returncode == 0

        clean = json.dumps({
            "text": "Convert this JSON to CSV format",
            "session_id": "short_term_only",
        })
        data = json.loads(run_coach("select-action", stdin_data=clean, profile=profile).stdout)
        assert data["action"] == "session_pattern_nudge"
        assert data["issue_type"] == "missing_goal"

        patterns = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert patterns["patterns"] == []

    def test_session_nudge_uses_pattern_domain_not_current_prompt_domain(self, profile):
        run_coach("enable", profile=profile)

        for i in range(3):
            payload = json.dumps({
                "session_id": "session_domain",
                "domain": "writing",
                "issue_type": "missing_goal",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "high_risk_guess",
                "evidence_summary": f"writing turn {i}",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        clean = json.dumps({
            "text": "Convert this JSON to CSV format",
            "session_id": "session_domain",
        })
        data = json.loads(run_coach("select-action", stdin_data=clean, profile=profile).stdout)
        assert data["action"] == "session_pattern_nudge"
        assert data["domain"] == "writing"

    def test_session_nudge_does_not_repeat_with_memory_disabled(self, profile):
        run_coach("enable", profile=profile)

        for i in range(3):
            payload = json.dumps({
                "session_id": "short_term_repeat",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "output_format_mismatch",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        clean = json.dumps({"text": "Translate to English: hi", "session_id": "short_term_repeat"})
        first = json.loads(run_coach("select-action", stdin_data=clean, profile=profile).stdout)
        assert first["action"] == "session_pattern_nudge"

        record = json.dumps({
            "session_id": "short_term_repeat",
            "domain": first["domain"],
            "issue_type": first["issue_type"],
            "action_taken": first["action"],
            "severity": 0.7, "confidence": 0.8,
        })
        run_coach("record-observation", stdin_data=record, profile=profile)

        second = json.loads(run_coach("select-action", stdin_data=clean, profile=profile).stdout)
        assert second["action"] != "session_pattern_nudge"

    def test_session_nudge_does_not_repeat_in_same_session(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        for i in range(3):
            payload = json.dumps({
                "session_id": "repeat_sess",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "output_format_mismatch",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # First clean prompt → session_pattern_nudge
        payload = json.dumps({"text": "Translate to English: hi", "session_id": "repeat_sess"})
        first = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert first["action"] == "session_pattern_nudge"

        # Agent records the nudge
        record = json.dumps({
            "session_id": "repeat_sess",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "session_pattern_nudge",
            "severity": 0.7, "confidence": 0.8,
        })
        run_coach("record-observation", stdin_data=record, profile=profile)

        # Another clean prompt in same session → should NOT re-nudge
        payload = json.dumps({"text": "Convert this JSON to CSV format", "session_id": "repeat_sess"})
        second = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert second["action"] != "session_pattern_nudge"

    def test_session_nudge_isolated_across_sessions(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)
        # Pattern in sessionX
        for i in range(3):
            payload = json.dumps({
                "session_id": "sessionX",
                "domain": "coding",
                "issue_type": "missing_goal",
                "action_taken": "post_answer_tip",
                "severity": 0.7, "confidence": 0.8,
                "cost_signal": "high_risk_guess",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Clean prompt in DIFFERENT session → no pattern there
        payload = json.dumps({"text": "Convert this JSON to CSV format", "session_id": "sessionY"})
        data = json.loads(run_coach("select-action", stdin_data=payload, profile=profile).stdout)
        assert data["action"] != "session_pattern_nudge"


class TestRetrospectiveCLI:
    """Verify the CLI returns retrospective_reminder action when all conditions met."""

    def test_clean_prompt_with_qualifying_pattern_returns_retrospective(self, profile, tmp_path):
        """When current prompt is clean but DB has a qualifying pattern, select-action returns retrospective."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        # Seed the DB with 4 observations across distinct sessions, all with cost signals
        for i in range(4):
            payload = json.dumps({
                "session_id": f"retro_seed_{i:03d}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Backdate observation_period_ends_at to simulate having passed the 14-day gate
        import json as _json
        cfg_path = Path(profile) / "config.json"
        cfg = _json.loads(cfg_path.read_text())
        from datetime import datetime, timezone, timedelta
        cfg["observation_period_ends_at"] = (
            datetime.now(timezone.utc) - timedelta(days=15)
        ).isoformat()
        cfg_path.write_text(_json.dumps(cfg))

        # Send a CLEAN prompt — no per-turn issue
        payload = json.dumps({"text": "Convert this JSON to CSV format", "session_id": "retro_check"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] == "retrospective_reminder"

    def test_light_budget_still_counts_visible_tips_when_memory_disabled(self, profile):
        run_coach("enable", profile=profile)

        for i in range(2):
            select_payload = json.dumps({
                "text": "帮我写一个接口文档",
                "session_id": f"budget_memoff_{i}",
                "agent_classification": {
                    "issue_type": "missing_output_contract",
                    "confidence": 0.9,
                    "severity": 0.8,
                    "fixability": 0.9,
                    "cost_signal": "output_format_mismatch",
                    "domain": "coding",
                    "is_sensitive": False,
                    "is_urgent": False,
                    "evidence_summary": "request lacks output format",
                },
            })
            selected = json.loads(run_coach("select-action", stdin_data=select_payload, profile=profile).stdout)
            assert selected["action"] in ("post_answer_tip", "pre_answer_micro_nudge")

            record_payload = json.dumps({
                "session_id": f"budget_memoff_{i}",
                "domain": selected["domain"],
                "issue_type": selected["issue_type"],
                "action_taken": selected["action"],
                "severity": 0.9,
                "confidence": 0.8,
                "fixability": 0.9,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "request lacks output format",
            })
            record = run_coach("record-observation", stdin_data=record_payload, profile=profile)
            assert record.returncode == 0

        third = json.loads(run_coach("select-action", stdin_data=select_payload, profile=profile).stdout)
        assert third["action"] == "silent_rewrite"
        assert third["suppressed_reason"] == "budget_exhausted"
        assert third["issue_type"] == "missing_output_contract"
        assert third["coaching_text"] == ""

    def test_cooldown_blocks_retrospective_after_notification(self, profile):
        """Once a pattern is notified, a fresh call to select-action must NOT
        return retrospective_reminder again (14-day cooldown)."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        # Seed qualifying pattern
        for i in range(4):
            payload = json.dumps({
                "session_id": f"retro_cd_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Backdate observation period end
        cfg_path = Path(profile) / "config.json"
        cfg = json.loads(cfg_path.read_text())
        from datetime import datetime, timezone, timedelta
        cfg["observation_period_ends_at"] = (
            datetime.now(timezone.utc) - timedelta(days=15)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        # Record a retrospective reminder (marks pattern notified)
        payload = json.dumps({
            "session_id": "retro_cd_emit",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "retrospective_reminder",
            "severity": 0.65, "confidence": 0.80,
            "evidence_summary": "retrospective reminder shown",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        # Now a clean prompt should NOT return retrospective — cooldown in effect
        payload = json.dumps({"text": "Convert this JSON to CSV format", "session_id": "retro_cd_check"})
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        # Allowed: silent_rewrite, none; NOT allowed: retrospective_reminder
        assert data["action"] != "retrospective_reminder", (
            f"cooldown should block retrospective, got {data['action']} "
            f"reason={data.get('reason')}"
        )

    def test_recording_retrospective_marks_pattern_notified(self, profile):
        """Recording an observation with action=retrospective_reminder should update last_notified_at."""
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        # Seed qualifying pattern
        for i in range(4):
            payload = json.dumps({
                "session_id": f"retro_mark_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Verify pattern not yet notified
        before = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert before["patterns"][0]["last_notified_at"] is None

        # Record a retrospective_reminder observation
        payload = json.dumps({
            "session_id": "retro_emit",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "retrospective_reminder",
            "severity": 0.65,
            "confidence": 0.80,
            "evidence_summary": "retrospective reminder shown",
        })
        run_coach("record-observation", stdin_data=payload, profile=profile)

        # Pattern's last_notified_at should now be set
        after = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert after["patterns"][0]["last_notified_at"] is not None

        # why-reminded should now return the chain
        result = run_coach("why-reminded", profile=profile)
        assert result.returncode == 0
        assert "missing_output_contract" in result.stdout.lower() or "output" in result.stdout.lower()

    def test_recording_retrospective_does_not_increase_pattern_evidence(self, profile):
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        for i in range(4):
            payload = json.dumps({
                "session_id": f"retro_seed_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        before = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert before["patterns"][0]["evidence_count"] == 4

        reminder = json.dumps({
            "session_id": "retro_emit_no_reinforce",
            "domain": "coding",
            "issue_type": "missing_output_contract",
            "action_taken": "retrospective_reminder",
            "severity": 0.65,
            "confidence": 0.80,
            "evidence_summary": "retrospective reminder shown",
        })
        run_coach("record-observation", stdin_data=reminder, profile=profile)

        after = json.loads(run_coach("show-patterns", profile=profile).stdout)
        assert after["patterns"][0]["evidence_count"] == 4

    def test_retrospective_uses_pattern_domain_and_domain_scoped_chain(self, profile):
        """The emitted domain must match the selected pattern, not the current prompt.

        The stored explanation chain should also stay scoped to that
        pattern's domain instead of merging every domain for the issue type.
        """
        run_coach("enable", profile=profile)
        run_coach("set-memory", "on", profile=profile)

        # Higher-score writing pattern
        for i in range(4):
            payload = json.dumps({
                "session_id": f"retro_write_{i}",
                "domain": "writing",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "writing task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        # Lower-score coding pattern with the same issue_type
        for i in range(2):
            payload = json.dumps({
                "session_id": f"retro_code_{i}",
                "domain": "coding",
                "issue_type": "missing_output_contract",
                "action_taken": "post_answer_tip",
                "severity": 0.65,
                "confidence": 0.80,
                "cost_signal": "output_format_mismatch",
                "evidence_summary": "coding task lacked output format",
            })
            run_coach("record-observation", stdin_data=payload, profile=profile)

        cfg_path = Path(profile) / "config.json"
        cfg = json.loads(cfg_path.read_text())
        from datetime import datetime, timezone, timedelta
        cfg["observation_period_ends_at"] = (
            datetime.now(timezone.utc) - timedelta(days=15)
        ).isoformat()
        cfg_path.write_text(json.dumps(cfg))

        # Current prompt is tagged as coding, but the top pattern is writing.
        payload = json.dumps({
            "text": "Clean prompt with no coaching issue",
            "session_id": "retro_domain_select",
            "agent_classification": {
                "domain": "coding",
                "issue_type": None,
            },
        })
        result = run_coach("select-action", stdin_data=payload, profile=profile)
        data = json.loads(result.stdout)
        assert data["action"] == "retrospective_reminder"
        assert data["domain"] == "writing"

        # Agent records the emitted reminder using the returned payload.
        record = json.dumps({
            "session_id": "retro_domain_emit",
            "domain": data["domain"],
            "issue_type": data["issue_type"],
            "action_taken": data["action"],
            "severity": 0.65,
            "confidence": 0.80,
            "evidence_summary": "retrospective reminder shown",
        })
        run_coach("record-observation", stdin_data=record, profile=profile)

        patterns = json.loads(run_coach("show-patterns", profile=profile).stdout)["patterns"]
        writing = next(p for p in patterns if p["domain"] == "writing")
        coding = next(p for p in patterns if p["domain"] == "coding")
        assert writing["last_notified_at"] is not None
        assert coding["last_notified_at"] is None

        import sqlite3
        con = sqlite3.connect(str(Path(profile) / "coach.db"))
        raw_chain = con.execute(
            "SELECT explanation_chain FROM intervention_events "
            "WHERE surface='retrospective_reminder' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()[0]
        con.close()
        chain = json.loads(raw_chain)
        assert chain["domain"] == "writing"
        assert chain["evidence_count"] == 4
        assert chain["distinct_sessions"] == 4
