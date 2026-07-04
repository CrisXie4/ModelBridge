"""R4 acceptance test — consolidated guard for the whole CLI IA refactor.

Covers:
  1. Top-level --help shows new canonical commands and hides old ones.
  2. Deprecated aliases still RESOLVE (not "No such command") and emit a warning.
  3. New canonical paths RESOLVE (exit_code == 0 for --help).

CliRunner has NO mix_stderr kwarg in this Typer version — use CliRunner() plain.
"""

from __future__ import annotations

import re
import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names from a Typer --help Commands table."""
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
    "prompt", "project", "mcp", "bridge", "init", "update",
}

EXPECTED_HIDDEN = {"chat", "cost", "cache", "profile", "patch"}


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
# Part 2: deprecated aliases
#   - the only v1.1 soft-deprecated alias still alive: ``project init``
#     (its rename target ``project rules init`` exists; the old path still
#     resolves with a deprecation notice — see README's R3a-soft note).
#   - everything else (chat / cost / cache stats / cache reset / cache clean /
#     profile list / model test / bridge control) was PHYSICALLY REMOVED in
#     v1.2 and now returns "No such command" (see ``test_cli_ia_r2a.py``).
# ---------------------------------------------------------------------------

def test_project_init_alias_resolves():
    """`mbridge project init --help` still resolves (R3a soft-deprecated, alive)."""
    r = runner.invoke(app, ["project", "init", "--help"])
    assert r.exit_code != 2, (
        f"`mbridge project init --help` returned exit_code 2 (unknown command).\n"
        f"Output:\n{r.output}"
    )


@pytest.mark.parametrize("args", [
    ["chat", "--help"],                  # was: deprecated alias for `ask`
    ["cost", "estimate", "--help"],      # was: deprecated alias for `usage cost`
    ["cache", "stats", "--help"],        # was: deprecated alias for `usage cache stats`
    ["profile", "list", "--help"],       # was: deprecated alias for `config profile list`
    ["model", "test", "--help"],         # was: deprecated alias for `doctor model`
])
def test_v12_removed_aliases_return_no_such_command(args):
    """IA v1.2 cleanup: physically-removed aliases return exit_code=2 + 'No such command'.

    These five aliases used to print a "移至 v1.2" warning and forward to the
    canonical command. After v1.2 cleanup, they return exit_code=2 with a
    "No such command" error — no soft-deprecation fallback. Guard lives here
    in addition to ``test_cli_ia_r2a`` so the acceptance suite catches the
    case independently.
    """
    r = runner.invoke(app, args)
    assert r.exit_code == 2, (
        f"`mbridge {' '.join(args)}` should be unknown (exit_code=2) after v1.2 cleanup, "
        f"got exit_code={r.exit_code}.\nOutput:\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' error after v1.2 cleanup, got:\n{r.output}"
    )


def test_v12_removed_bridge_control_on(home):
    """`mbridge bridge control on` was physically REMOVED in v1.2 (see test_cli_ia_r2a).

    Guards against accidental re-introduction of the hidden `bridge control`
    sub-app. If someone re-adds `bridge control on` as a soft-deprecated alias,
    this catches it.
    """
    r = runner.invoke(app, ["bridge", "control", "on"])
    assert r.exit_code == 2, (
        f"`mbridge bridge control on` should be unknown (exit_code=2) after v1.2 cleanup, "
        f"got exit_code={r.exit_code}.\nOutput:\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' error after v1.2 cleanup, got:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 3: new canonical paths RESOLVE (exit_code == 0 for --help)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["ask", "--help"],
    ["usage", "--help"],
    ["usage", "cost", "--help"],
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


# ---------------------------------------------------------------------------
# Part 5: UX guards — help text must include usage examples for top actions
# ---------------------------------------------------------------------------

def test_run_help_includes_examples():
    """`mbridge run --help` must show common usage examples.

    Guards the example block added to ``cli.cmd_run`` so a future rewrite
    doesn't drop the new-user guidance.
    """
    r = runner.invoke(app, ["run", "--help"])
    assert r.exit_code == 0, r.output
    out = _ANSI_RE.sub("", r.output)
    for snippet in ("pytest -x", "npm test", "--dry-run"):
        assert snippet in out, (
            f"Expected `{snippet}` in `mbridge run --help` example block, got:\n{r.output}"
        )


def test_mcp_help_includes_examples():
    """`mbridge mcp --help` must show common usage examples.

    Guards the example block added to ``mcp.cli.mcp_app`` so a future rewrite
    doesn't drop the new-user guidance.
    """
    r = runner.invoke(app, ["mcp", "--help"])
    assert r.exit_code == 0, r.output
    out = _ANSI_RE.sub("", r.output)
    for snippet in ("mcp list", "mcp tools", "mcp serve", "filesystem__list_dir"):
        assert snippet in out, (
            f"Expected `{snippet}` in `mbridge mcp --help` example block, got:\n{r.output}"
        )
