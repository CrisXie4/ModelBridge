"""R3a CLI IA tests: chat→ask promoted; bridge control→bridge on/off flattened.

Tests validate:
  1. ``ask --help`` exits 0; description does NOT contain "测试用".
  2. ``mbridge --help`` lists ``ask`` and does NOT list ``chat``.
  3. ``chat "..."`` (old alias) still runs (deprecated) and prints deprecation notice.
  4. ``bridge on``/``bridge off`` / ``bridge status`` exist and exit 0 on ``--help``.
  5. ``bridge --help`` lists ``on`` and ``off`` but does NOT list ``control``.
  6. ``bridge control on`` still works and prints a deprecation notice.

CliRunner in this Typer version has NO ``mix_stderr`` kwarg — use CliRunner() plain.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from modelbridge.cli import app
from modelbridge.bridge.cli import bridge_app

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated MBRIDGE_HOME, seeded with init."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# Helper: extract visible command names from a --help Commands table
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names that Typer lists in the Commands table."""
    commands: list[str] = []
    in_commands = False
    for line in output.splitlines():
        stripped = line.strip().lstrip("│").strip()
        if "Commands" in line:
            in_commands = True
            continue
        if not in_commands:
            continue
        if "Options" in line or "Arguments" in line:
            in_commands = False
            continue
        parts = stripped.split()
        if parts and parts[0].isidentifier() and parts[0].islower():
            commands.append(parts[0])
    return set(commands)


# ---------------------------------------------------------------------------
# 1. ask --help: exits 0, description has no "测试用"
# ---------------------------------------------------------------------------

def test_ask_help_exits_ok():
    r = runner.invoke(app, ["ask", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_ask_help_no_test_only_framing():
    r = runner.invoke(app, ["ask", "--help"])
    assert r.exit_code == 0, r.output
    assert "测试用" not in r.output, (
        "Expected '测试用' removed from `ask --help` description, but it still appears.\n"
        f"{r.output}"
    )


# ---------------------------------------------------------------------------
# 2. mbridge --help lists ask, does NOT list chat
# ---------------------------------------------------------------------------

def test_root_help_lists_ask():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "ask" in listed, (
        f"Expected `ask` in root Commands table, got: {listed}\n{r.output}"
    )


def test_root_help_hides_chat():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "chat" not in listed, (
        f"Expected `chat` hidden from root Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 3. chat (old alias) still works and emits deprecation notice
# ---------------------------------------------------------------------------

def test_chat_alias_help_exits_ok():
    """mbridge chat --help must exit 0 (deprecated alias still resolves)."""
    r = runner.invoke(app, ["chat", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_chat_alias_dry_run_emits_deprecation(home):
    """mbridge chat ... --dry-run must print a deprecation notice."""
    r = runner.invoke(app, ["chat", "hello", "--dry-run"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )
    assert "ask" in r.output, (
        f"Expected 'ask' mentioned in deprecation notice, got:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 4. bridge on / off / status exist
# ---------------------------------------------------------------------------

def test_bridge_on_help_exits_ok(home):
    """mbridge bridge on --help must exit 0."""
    r = runner.invoke(bridge_app, ["on", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_bridge_off_help_exits_ok(home):
    """mbridge bridge off --help must exit 0."""
    r = runner.invoke(bridge_app, ["off", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_bridge_status_help_exits_ok(home):
    """mbridge bridge status --help must exit 0."""
    r = runner.invoke(bridge_app, ["status", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_bridge_on_runs(home):
    """mbridge bridge on must exit 0 and enable control under isolated MBRIDGE_HOME."""
    r = runner.invoke(bridge_app, ["on"], env={"MBRIDGE_HOME": str(home)})
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "开启" in r.output, f"Expected '开启' in output:\n{r.output}"


def test_bridge_off_runs(home):
    """mbridge bridge off must exit 0."""
    r = runner.invoke(bridge_app, ["off"], env={"MBRIDGE_HOME": str(home)})
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


# ---------------------------------------------------------------------------
# 5. bridge --help lists on/off, does NOT list control
# ---------------------------------------------------------------------------

def test_bridge_help_lists_on_and_off():
    r = runner.invoke(bridge_app, ["--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "on" in listed, f"Expected `on` in bridge --help Commands, got: {listed}\n{r.output}"
    assert "off" in listed, f"Expected `off` in bridge --help Commands, got: {listed}\n{r.output}"


def test_bridge_help_hides_control():
    r = runner.invoke(bridge_app, ["--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "control" not in listed, (
        f"Expected `control` hidden from bridge --help Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 6. bridge control on still works and emits deprecation
# ---------------------------------------------------------------------------

def test_bridge_control_on_emits_deprecation(home):
    """mbridge bridge control on must print a deprecation notice."""
    r = runner.invoke(bridge_app, ["control", "on"], env={"MBRIDGE_HOME": str(home)})
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )
    assert "bridge on" in r.output, (
        f"Expected 'bridge on' in deprecation notice, got:\n{r.output}"
    )


def test_bridge_control_off_emits_deprecation(home):
    """mbridge bridge control off must print a deprecation notice."""
    r = runner.invoke(bridge_app, ["control", "off"], env={"MBRIDGE_HOME": str(home)})
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )
