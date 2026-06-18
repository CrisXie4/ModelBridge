"""R4 acceptance test — consolidated guard for the whole CLI IA refactor.

Covers:
  1. Top-level --help shows new canonical commands and hides old ones.
  2. Deprecated aliases still RESOLVE (not "No such command") and emit a warning.
  3. New canonical paths RESOLVE (exit_code == 0 for --help).

CliRunner has NO mix_stderr kwarg in this Typer version — use CliRunner() plain.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names from a Typer --help Commands table."""
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
        if parts and (parts[0].isidentifier() and parts[0].islower() or "-" in parts[0]):
            commands.append(parts[0])
    return set(commands)


def _resolves(args: list[str]) -> bool:
    """Return True iff the command RESOLVES (exit_code != 2 for unknown-command)."""
    r = runner.invoke(app, args)
    # Typer returns exit_code 2 for "No such command" / missing required arg.
    # A resolvable command/group returns 0 (or sometimes 1 on runtime failure,
    # but NEVER 2 for "unknown command" if the route exists).
    # We allow 0 and 1 (real command exists but failed due to missing state);
    # 2 means the routing itself failed (unknown command/group).
    return r.exit_code != 2


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated MBRIDGE_HOME, seeded with init."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# Part 1: top-level --help contains new commands and hides old ones
# ---------------------------------------------------------------------------

EXPECTED_VISIBLE = {
    "ask", "edit", "run", "route", "doctor", "usage", "config", "model",
    "prompt", "project", "mcp", "bridge", "init", "update", "version",
}

EXPECTED_HIDDEN = {"chat", "cost", "budget", "cache", "profile", "patch"}


def test_top_level_help_visible_commands():
    """mbridge --help must list all canonical commands."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    missing = EXPECTED_VISIBLE - listed
    assert not missing, (
        f"Expected commands missing from `mbridge --help` Commands table: {missing}\n"
        f"Commands found: {listed}\n{r.output}"
    )


def test_top_level_help_hides_old_commands():
    """mbridge --help must NOT list deprecated old-name commands."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    leaked = EXPECTED_HIDDEN & listed
    assert not leaked, (
        f"Old commands unexpectedly visible in `mbridge --help` Commands table: {leaked}\n"
        f"Commands found: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 2: deprecated aliases RESOLVE and warn
# ---------------------------------------------------------------------------

# --- 2a. RESOLVE checks (not "No such command") ---

@pytest.mark.parametrize("args", [
    ["chat", "--help"],
    ["cost", "estimate", "--help"],
    ["budget", "show", "--help"],
    ["cache", "stats", "--help"],
    ["profile", "list", "--help"],
    ["model", "test", "--help"],
    ["project", "init", "--help"],
])
def test_deprecated_alias_resolves(args):
    """Deprecated aliases must RESOLVE (exit_code != 2)."""
    r = runner.invoke(app, args)
    assert r.exit_code != 2, (
        f"`mbridge {' '.join(args)}` returned exit_code 2 (unknown command).\n"
        f"Output:\n{r.output}"
    )


def test_deprecated_bridge_control_on_resolves(home):
    """mbridge bridge control on must RESOLVE (exit_code != 2)."""
    r = runner.invoke(app, ["bridge", "control", "on"])
    assert r.exit_code != 2, (
        f"`mbridge bridge control on` returned exit_code 2 (unknown command).\n"
        f"Output:\n{r.output}"
    )


# --- 2b. Deprecation WARNING checks ---
# Pick aliases that are safe to run hermetically with a seeded MBRIDGE_HOME.

def test_chat_alias_warns(home):
    """mbridge chat ... must emit a deprecation warning."""
    # Invoke with --help so it doesn't need a model. --help on the alias
    # won't fire the wrapper; invoke without --help using a safe no-network path.
    # 'mbridge chat --route' without a prompt causes a usage error (exit 2);
    # We use the option that terminates immediately without network: --show-prompt
    # combined with a dummy prompt.  If the alias fires, the warning appears.
    r = runner.invoke(app, ["chat", "hi", "--show-prompt"], env={"MBRIDGE_HOME": str(home)})
    # exit code may be non-zero if no model is registered; what matters is the warning text.
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in `mbridge chat` output:\n{r.output}"
    )


def test_cost_estimate_alias_warns(home):
    """mbridge cost estimate ... must emit a deprecation warning."""
    r = runner.invoke(app, ["cost", "estimate", "test prompt"], env={"MBRIDGE_HOME": str(home)})
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in `mbridge cost estimate` output:\n{r.output}"
    )


def test_profile_list_alias_warns(home):
    """mbridge profile list must emit a deprecation warning."""
    r = runner.invoke(app, ["profile", "list"], env={"MBRIDGE_HOME": str(home)})
    assert "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation notice in `mbridge profile list` output:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 3: new canonical paths RESOLVE (exit_code == 0 for --help)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["ask", "--help"],
    ["usage", "--help"],
    ["usage", "cost", "--help"],
    ["usage", "budget", "--help"],
    ["usage", "cache", "--help"],
    ["config", "--help"],
    ["config", "profile", "--help"],
    ["config", "profile", "list", "--help"],
    ["doctor", "--help"],
    ["doctor", "route", "--help"],
    ["bridge", "--help"],
    ["bridge", "on", "--help"],
    ["project", "--help"],
    ["project", "rules", "--help"],
    ["project", "rules", "init", "--help"],
])
def test_new_canonical_path_resolves(args):
    """New canonical paths must resolve (exit_code == 0 for --help)."""
    r = runner.invoke(app, args)
    assert r.exit_code == 0, (
        f"`mbridge {' '.join(args)}` returned exit_code={r.exit_code} (expected 0).\n"
        f"Output:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 4: patch group is hidden from top-level help but still works
# ---------------------------------------------------------------------------

def test_patch_hidden_from_top_level_help():
    """patch must NOT appear in `mbridge --help` Commands table."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "patch" not in listed, (
        f"`patch` unexpectedly visible in `mbridge --help` Commands table.\n"
        f"Commands found: {listed}\n{r.output}"
    )


def test_patch_rollback_still_works():
    """mbridge patch rollback --help must exit 0 (hidden but still invokable)."""
    r = runner.invoke(app, ["patch", "rollback", "--help"])
    assert r.exit_code == 0, (
        f"`mbridge patch rollback --help` returned exit_code={r.exit_code}.\n"
        f"Output:\n{r.output}"
    )
