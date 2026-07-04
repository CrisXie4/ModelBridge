"""R2b CLI IA tests: profile under config; model test deprecated to doctor model.

Tests validate:
  1. Canonical paths work: config profile list / config --help lists profile
  2. Old path deprecated+hidden: profile list still works with deprecation notice;
     mbridge --help top level does NOT list profile as a group
  3. model test still works (as deprecated alias) and prints deprecation notice;
     model --help does NOT list test

CliRunner in this Typer version has NO ``mix_stderr`` kwarg — use CliRunner() plain.
"""

from __future__ import annotations

import re
import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated MBRIDGE_HOME, seeded with init."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names that Typer lists in the Commands table."""
    output = _ANSI_RE.sub("", output)
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
# 1. Canonical paths work
# ---------------------------------------------------------------------------

def test_config_profile_list_canonical(home):
    """config profile list exits cleanly (empty profile list is still exit 0)."""
    r = runner.invoke(app, ["config", "profile", "list"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "no such command" not in r.output.lower(), r.output


def test_config_help_lists_profile():
    """config --help must list profile as a subcommand."""
    r = runner.invoke(app, ["config", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    assert "profile" in listed, (
        f"Expected `profile` in `config --help` Commands table, got: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 2. R2b's profile soft-deprecated path was PHYSICALLY REMOVED in v1.2.
#    The canonical `config profile ...` is the only way in (see section 1).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["profile", "list"],
    ["profile", "add", "x"],
    ["profile", "use", "x"],
    ["profile", "show", "x"],
    ["profile", "remove", "x"],
])
def test_profile_old_path_no_such_command(args):
    """v1.2 cleanup: `mbridge profile ...` returns exit_code=2 + 'No such command'."""
    r = runner.invoke(app, args)
    assert r.exit_code == 2, (
        f"`mbridge {' '.join(args)}` should be unknown (exit_code=2) after v1.2, "
        f"got exit_code={r.exit_code}\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' after v1.2 cleanup, got:\n{r.output}"
    )


def test_root_help_hides_profile_group():
    """mbridge --help must NOT list profile as a top-level group (canonical moved under config)."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    assert "profile" not in listed, (
        f"Expected `profile` hidden from root Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 3. model test was PHYSICALLY REMOVED in v1.2 (canonical: `doctor model <name>`).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["model", "test", "--help"],
    ["model", "test", "some-name"],
])
def test_model_test_no_such_command(args):
    """v1.2 cleanup: `mbridge model test` returns exit_code=2 + 'No such command'."""
    r = runner.invoke(app, args)
    assert r.exit_code == 2, (
        f"`mbridge {' '.join(args)}` should be unknown (exit_code=2) after v1.2, "
        f"got exit_code={r.exit_code}\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' after v1.2 cleanup, got:\n{r.output}"
    )


def test_model_help_hides_test():
    """mbridge model --help must NOT list test (alias removed in v1.2)."""
    r = runner.invoke(app, ["model", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    assert "test" not in listed, (
        f"Expected `test` hidden from `model --help` Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )
