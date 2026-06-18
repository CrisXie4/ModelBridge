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

import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

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
# 2. Old path deprecated+hidden
# ---------------------------------------------------------------------------

def test_profile_list_old_path_still_works(home):
    """mbridge profile list still exits 0 (deprecated but functional)."""
    r = runner.invoke(app, ["profile", "list"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "no such command" not in r.output.lower(), r.output


def test_profile_list_old_path_emits_deprecation(home):
    """mbridge profile list emits a deprecation notice (移至 or v1.2)."""
    r = runner.invoke(app, ["profile", "list"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )


def test_root_help_hides_profile_group():
    """mbridge --help must NOT list profile as a top-level group."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    assert "profile" not in listed, (
        f"Expected `profile` hidden from root Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 3. model test deprecated to doctor model
# ---------------------------------------------------------------------------

def test_model_test_help_still_works():
    """mbridge model test --help must exit 0 (alias still resolves)."""
    r = runner.invoke(app, ["model", "test", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_model_test_emits_deprecation_notice(home):
    """Invoking model test with a non-existent name still prints deprecation notice."""
    # We pass a dummy name — it will exit 2 (not found) but the deprecation
    # banner from deprecated_alias fires BEFORE the impl runs.
    r = runner.invoke(app, ["model", "test", "__nonexistent_model__"])
    # exit_code 2 = model not found; that's from the doctor-model impl, which is fine
    assert r.exit_code in (0, 1, 2, 3), f"Unexpected exit_code {r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )
    assert "doctor model" in r.output, (
        f"Expected 'doctor model' mentioned in deprecation notice, got:\n{r.output}"
    )


def test_model_help_hides_test():
    """mbridge model --help must NOT list test in the Commands table."""
    r = runner.invoke(app, ["model", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    assert "test" not in listed, (
        f"Expected `test` hidden from `model --help` Commands table, but it is listed.\n"
        f"Commands: {listed}\n{r.output}"
    )
