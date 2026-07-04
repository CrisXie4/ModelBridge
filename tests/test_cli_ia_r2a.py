"""R2a CLI IA tests (post-v1.2 cleanup): usage group absorbs cost/cache; deprecated top-level aliases REMOVED.

Tests validate:
  1. New canonical paths work: usage cost / usage cache
  2. mbridge --help no longer lists cost/cache/chat/profile as top-level groups
  3. usage --help lists cost / cache
  4. Deprecated IA aliases are GONE (chat / cost / cache / profile / model test / bridge control
     return "No such command" — not a deprecation warning)

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


def test_usage_cache_new_path(home):
    """usage cache stats (bare) exits 0."""
    r = runner.invoke(app, ["usage", "cache", "stats"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


# ---------------------------------------------------------------------------
# 2. mbridge --help top level: no IA-alias groups; usage IS listed
# ---------------------------------------------------------------------------

def test_root_help_hides_old_groups_and_shows_usage():
    """Root help must list usage but NOT cost/cache/chat/profile/budget as top-level commands.

    All five are IA-deprecated aliases that were physically removed in
    v1.2 — the canonical paths live under `usage` and `config` now.
    """
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    # usage must be listed
    assert "usage" in listed, (
        f"Expected `usage` in root Commands table, got: {listed}\n{r.output}"
    )
    # IA-deprecated top-level groups must NOT appear (all removed in v1.2)
    for old in ("cost", "cache", "chat", "profile", "budget"):
        assert old not in listed, (
            f"Expected `{old}` absent from root Commands table (IA v1.2 cleanup), but it is listed.\n"
            f"Commands: {listed}\n{r.output}"
        )


# ---------------------------------------------------------------------------
# 3. usage --help lists cost / cache (no budget — removed)
# ---------------------------------------------------------------------------

def test_usage_help_lists_subgroups():
    """usage --help must list cost and cache, NOT budget."""
    r = runner.invoke(app, ["usage", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    listed = _listed_commands(r.output)
    for cmd in ("cost", "cache"):
        assert cmd in listed, (
            f"Expected `{cmd}` in `usage --help` Commands table, got: {listed}\n{r.output}"
        )
    # budget must NOT be listed under usage (removed)
    assert "budget" not in listed, (
        f"Expected `budget` absent from `usage --help` (removed 2026-07), got: {listed}\n{r.output}"
    )


# ---------------------------------------------------------------------------
# 4. IA-deprecated aliases are GONE — they return "No such command"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["chat", "--help"],                # was: deprecated alias for `ask`
    ["cost", "estimate", "hello"],     # was: deprecated alias for `usage cost`
    ["cache", "stats"],                 # was: deprecated alias for `usage cache`
    ["cache", "reset"],                 # was: deprecated alias for `usage cache reset`
    ["cache", "clean"],                 # was: deprecated alias for `usage cache clean`
    ["profile", "list"],                # was: deprecated alias for `config profile list`
    ["model", "test", "x"],             # was: deprecated alias for `doctor model`
    ["bridge", "control", "on"],        # was: deprecated alias for `bridge on`
])
def test_ia_aliases_return_no_such_command(args):
    """IA v1.2 cleanup: top-level deprecated aliases are physically deleted.

    Each of these used to print a "移至 v1.2" warning and forward to the
    canonical command. After v1.2 cleanup, they must return exit_code=2
    with a "No such command" error — no soft-deprecation fallback.
    """
    r = runner.invoke(app, args)
    assert r.exit_code == 2, (
        f"`mbridge {' '.join(args)}` should be unknown (exit_code=2), got {r.exit_code}.\n"
        f"Output:\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' error, got:\n{r.output}"
    )
