"""R3b CLI IA tests:
  1. project rules init — canonical AGENT.md generator; project init removed.
  2. doctor route — canonical route-self-test; route test deprecated.
  3. edit --undo — rollback without diff generation.

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
# Helper: extract visible command names from a --help Commands table
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names that Typer lists in the Commands table."""
    output = _ANSI_RE.sub("", output)
    commands: list[str] = []
    in_commands = False
    for line in output.splitlines():
        stripped = line.strip().lstrip("│").strip()  # strip │
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


# ---------------------------------------------------------------------------
# Part 1: project rules init (canonical); project init removed
# ---------------------------------------------------------------------------

def test_project_rules_help_exits_ok():
    """project rules --help must exit 0."""
    r = runner.invoke(app, ["project", "rules", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_project_rules_init_help_exits_ok():
    """project rules init --help must exit 0."""
    r = runner.invoke(app, ["project", "rules", "init", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    # Confirm it accepts AGENT.md generation options
    assert "--force" in r.output or "force" in r.output.lower(), (
        f"Expected --force in project rules init --help:\n{r.output}"
    )


def test_project_init_is_removed():
    """`project init` was physically removed (was a deprecated alias for `project rules init`)."""
    r = runner.invoke(app, ["project", "init", "--help"])
    assert r.exit_code == 2, (
        f"`project init` should be unknown after removal, got exit_code={r.exit_code}\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' for removed `project init`, got:\n{r.output}"
    )


def test_project_help_does_not_list_init():
    """project --help must NOT list `init` as a top-level command."""
    r = runner.invoke(app, ["project", "--help"])
    assert r.exit_code == 0, r.output
    listed = _listed_commands(r.output)
    assert "init" not in listed, (
        f"Expected `init` hidden from project --help Commands, but found it listed.\n"
        f"Commands: {listed}\n{r.output}"
    )


def test_project_rules_lists_init_subcommand():
    """project rules --help must list `init` as a subcommand."""
    r = runner.invoke(app, ["project", "rules", "--help"])
    assert r.exit_code == 0, r.output
    assert "init" in r.output, (
        f"Expected 'init' in project rules --help output:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 2: doctor route / route test (deprecated hint)
# ---------------------------------------------------------------------------

def test_doctor_route_help_exits_ok():
    """doctor route --help must exit 0."""
    r = runner.invoke(app, ["doctor", "route", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"


def test_doctor_help_lists_route():
    """doctor --help should list `route` subcommand."""
    r = runner.invoke(app, ["doctor", "--help"])
    assert r.exit_code in (0, 1), r.output  # doctor runs env check without subcommand
    assert "route" in r.output, (
        f"Expected 'route' in doctor --help output:\n{r.output}"
    )


def test_route_test_emits_deprecation_hint(home, monkeypatch):
    """mbridge route test must print a deprecation hint to stderr/output."""
    # Intercept _run_route_test so it returns immediately rather than calling model.
    import modelbridge.cli as cli_mod

    def _fake_run_route_test(mode):
        cli_mod.console.print("[dim]route test (mocked)[/dim]")

    monkeypatch.setattr(cli_mod, "_run_route_test", _fake_run_route_test)

    r = runner.invoke(app, ["route", "test"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "doctor route" in r.output or "移至" in r.output or "v1.2" in r.output, (
        f"Expected deprecation hint mentioning 'doctor route' or v1.2 in output:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Part 3: edit --undo
# ---------------------------------------------------------------------------

def test_edit_undo_help_exits_ok():
    """edit --help must exit 0 and list --undo as an option."""
    r = runner.invoke(app, ["edit", "--help"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "--undo" in _ANSI_RE.sub("", r.output), (
        f"Expected '--undo' in edit --help output:\n{r.output}"
    )


def test_edit_undo_does_not_require_request():
    """edit --undo must not require the request argument (no model call)."""
    # Without a real backup this exits with code 0 (nothing to rollback) or 5 (guard).
    # What matters: it must NOT fail with "Missing argument REQUEST".
    r = runner.invoke(app, ["edit", "--undo", "--yes"])
    combined = r.output
    assert "Missing argument" not in combined, (
        f"edit --undo should not require REQUEST argument:\n{combined}"
    )
    # Must not try to call the model / generate a diff
    assert "build_edit_messages" not in combined
    assert "LLM" not in combined


def test_edit_undo_no_backup_exits_cleanly(home):
    """edit --undo in a fresh project with no backup must exit 0 gracefully."""
    r = runner.invoke(app, ["edit", "--undo", "--yes", "--project", str(home)])
    assert r.exit_code in (0, 5), (
        f"Expected exit 0 (no backup) or 5 (guard), got {r.exit_code}:\n{r.output}"
    )
    # Should NOT have attempted to generate a diff
    assert "diff" not in r.output.lower() or "没有" in r.output, (
        f"edit --undo should not generate diff:\n{r.output}"
    )


def test_edit_undo_with_backup(home):
    """edit --undo on a project with a prior backup must attempt rollback."""
    from modelbridge.editor import create_backup

    # Create a dummy backup
    dummy_file = home / "dummy.txt"
    dummy_file.write_text("original content", encoding="utf-8")
    create_backup(
        home,
        user_request="test request",
        patch_text="--- dummy.txt\n+++ dummy.txt\n",
        files_to_save={"dummy.txt": "original content"},
        label="edit",
    )
    # Simulate the file being changed
    dummy_file.write_text("changed content", encoding="utf-8")

    r = runner.invoke(app, ["edit", "--undo", "--yes", "--project", str(home)])
    # Should perform rollback and exit 0 (or 6 if failures, but not 2/5 errors)
    assert r.exit_code in (0, 6), (
        f"Expected exit 0 or 6 from rollback, got {r.exit_code}:\n{r.output}"
    )
    # Should mention rollback output
    assert "回滚" in r.output or "rollback" in r.output.lower() or "restored" in r.output.lower(), (
        f"Expected rollback result in output:\n{r.output}"
    )
