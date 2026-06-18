"""R2a CLI IA tests: usage group absorbs cost/budget/cache; old paths deprecated+hidden.

Tests validate:
  1. New canonical paths work: usage cost / usage budget / usage budget set / usage cache
  2. Old paths still work AND emit a deprecation notice
  3. mbridge --help no longer lists cost/budget/cache as top-level groups; lists usage
  4. usage --help lists cost / budget / cache

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
# 1. New canonical paths work
# ---------------------------------------------------------------------------

def test_usage_cost_new_path(home):
    """usage cost <prompt> exits 0 or 1 (no models registered = exit 2 is also OK)."""
    r = runner.invoke(app, ["usage", "cost", "hello"])
    # With no models: exit 2; with models: exit 0 or 1.
    # We just confirm the command resolves (not unknown-command error = not 2 due to missing cmd)
    assert r.exit_code in (0, 1, 2), f"Unexpected exit_code {r.exit_code}\n{r.output}"
    # Must NOT be "No such command"
    assert "no such command" not in r.output.lower(), r.output


def test_usage_budget_new_path(home):
    """usage budget (bare / show) exits 0."""
    r = runner.invoke(app, ["usage", "budget", "show"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_usage_budget_set_new_path(home):
    """usage budget set --monthly 50 exits 0."""
    r = runner.invoke(app, ["usage", "budget", "set", "--monthly", "50"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_usage_cache_new_path(home):
    """usage cache stats (bare) exits 0."""
    r = runner.invoke(app, ["usage", "cache", "stats"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


# ---------------------------------------------------------------------------
# 2. Old paths still work AND emit a deprecation notice
# ---------------------------------------------------------------------------

def test_old_cost_estimate_deprecated(home):
    """cost estimate still works and warns."""
    r = runner.invoke(app, ["cost", "estimate", "hello"])
    # exit 0/1/2 accepted (depends on model presence); must not be unknown-command
    assert r.exit_code in (0, 1, 2), f"exit_code={r.exit_code}\n{r.output}"
    assert "no such command" not in r.output.lower(), r.output
    # Deprecation notice must appear (stderr merged into output by CliRunner)
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )


def test_old_budget_show_deprecated(home):
    """budget show still works and warns."""
    r = runner.invoke(app, ["budget", "show"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )


def test_old_budget_set_deprecated(home):
    """budget set still works and warns."""
    r = runner.invoke(app, ["budget", "set", "--monthly", "30"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )


def test_old_cache_stats_deprecated(home):
    """cache stats still works and warns."""
    r = runner.invoke(app, ["cache", "stats"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in output, got:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 3. mbridge --help top level: no cost/budget/cache groups; usage IS listed
# ---------------------------------------------------------------------------

def test_root_help_hides_old_groups_and_shows_usage():
    """Root help must list usage but NOT cost/budget/cache as separate commands."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    # usage must be listed
    assert "usage" in listed, (
        f"Expected `usage` in root Commands table, got: {listed}\n{r.output}"
    )
    # cost/budget/cache must NOT be listed as top-level commands
    for old in ("cost", "budget", "cache"):
        assert old not in listed, (
            f"Expected `{old}` hidden from root Commands table, but it is listed.\n"
            f"Commands: {listed}\n{r.output}"
        )


# ---------------------------------------------------------------------------
# 4. usage --help lists cost / budget / cache
# ---------------------------------------------------------------------------

def test_usage_help_lists_subgroups():
    """usage --help must list cost, budget, cache."""
    r = runner.invoke(app, ["usage", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    for cmd in ("cost", "budget", "cache"):
        assert cmd in listed, (
            f"Expected `{cmd}` in `usage --help` Commands table, got: {listed}\n{r.output}"
        )
