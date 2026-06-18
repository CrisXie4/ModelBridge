"""CLI smoke tests — a regression net before splitting the giant cli.py.

Every test runs against an isolated ``MBRIDGE_HOME`` (a tmp dir), so nothing
touches the user's real ``~/.modelbridge`` and no network call is made. These
exercise command wiring / arg parsing / the help tree end-to-end through
Typer's ``CliRunner`` — exactly the surface a cli.py refactor could break.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point ModelBridge at an isolated config home and seed it via `init`."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "mbridge" in r.output.lower()


def test_root_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "init" in r.output and "route" in r.output


@pytest.mark.parametrize("sub", ["ask", "chat", "route", "model", "doctor", "config", "run"])
def test_subcommand_help(sub):
    r = runner.invoke(app, [sub, "--help"])
    assert r.exit_code == 0


def test_init_creates_files(home):
    assert (home / "config.yaml").exists()
    assert (home / "models.yaml").exists()


def test_config_show(home):
    r = runner.invoke(app, ["config", "show"])
    assert r.exit_code == 0


def test_config_upgrade_stamps_schema_version(home):
    r = runner.invoke(app, ["config", "upgrade"])
    assert r.exit_code == 0
    assert "schema_version" in (home / "config.yaml").read_text(encoding="utf-8")


def test_model_list_empty(home):
    r = runner.invoke(app, ["model", "list"])
    assert r.exit_code == 0


def test_doctor_global_runs(home):
    # A freshly-init'd home has no models, so doctor legitimately reports issues
    # (exit 1). What matters for the smoke test: it renders without crashing.
    r = runner.invoke(app, ["doctor"])
    assert "python version" in r.output
    assert r.exit_code in (0, 1)


def test_chat_dry_run_no_network(home):
    # --dry-run builds the prompt + prints estimates, never calls a provider.
    # Use the canonical `ask` command; `chat` is a deprecated alias.
    r = runner.invoke(app, ["ask", "你好", "--dry-run"])
    assert r.exit_code == 0
    assert "dry-run" in r.output


def test_run_dry_run_validates_only(home):
    # python is on the default command whitelist; --dry-run skips execution.
    r = runner.invoke(app, ["run", "python --version", "--dry-run", "--project", str(home)])
    assert r.exit_code == 0
    assert "dry-run" in r.output


def test_unknown_command_errors():
    r = runner.invoke(app, ["definitely-not-a-command"])
    assert r.exit_code != 0
