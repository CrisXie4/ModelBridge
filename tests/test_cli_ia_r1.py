"""R1 CLI IA tests: hidden commands, stale root help.

These tests validate the zero-behavior-change round-1 refactor:
  (a) pure-debug commands are hidden from --help but still invokable
  (b) root help no longer contains the stale command enumeration

The ``deprecated_alias`` helper itself was removed once the last alias
(``project init``) was physically dropped — see
``test_cli_ia_acceptance.py::test_project_init_alias_removed``.

CliRunner in this Typer version has NO ``mix_stderr`` kwarg — just use
``CliRunner()`` and check ``.output``.
"""

from __future__ import annotations

import re
from typer.testing import CliRunner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


# ---------------------------------------------------------------------------
# Task A: hidden commands are absent from help but still invokable
# ---------------------------------------------------------------------------

def _listed_commands(output: str) -> set[str]:
    """Extract command names that Typer lists in the Commands table.

    Typer formats the Commands section as lines like:
      │ call      description ...  │
    or (without borders):
        call      description ...

    We match any line where the first non-whitespace token is a single
    lowercase word (the command name) followed by spaces/description.
    This avoids false positives from the Typer app description text.
    """
    output = _ANSI_RE.sub("", output)
    commands: set[str] = []
    in_commands = False
    for line in output.splitlines():
        stripped = line.strip().lstrip("│").strip()
        # Detect the Commands section header.
        if "Commands" in line:
            in_commands = True
            continue
        if not in_commands:
            continue
        # Stop at the next section header or an empty separator line ending the block.
        if "Options" in line or "Arguments" in line:
            in_commands = False
            continue
        # A command-table row: first token is the command name (no spaces).
        parts = stripped.split()
        if parts and parts[0].isidentifier() and parts[0].islower():
            commands.append(parts[0])
    return set(commands)


def test_mcp_help_hides_debug_commands():
    """mcp --help must not list call/ping/read/serve in the Commands table."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    listed = _listed_commands(result.output)
    # Hidden commands absent from the Commands table.
    for cmd in ("call", "ping", "read", "serve"):
        assert cmd not in listed, (
            f"Expected `{cmd}` to be hidden from `mcp --help` Commands table, "
            f"but it is listed. Commands found: {listed}\nFull output:\n{result.output}"
        )
    # Visible commands still present in the Commands table.
    for cmd in ("list", "tools"):
        assert cmd in listed, (
            f"Expected `{cmd}` to be visible in `mcp --help` Commands table, "
            f"but it is missing. Commands found: {listed}\nFull output:\n{result.output}"
        )


def test_mcp_serve_no_such_command():
    """`mcp serve` was PHYSICALLY REMOVED in v1.2 (R2a completion)."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 2, (
        f"`mcp serve` should be unknown (exit_code=2) after v1.2 cleanup, "
        f"got exit_code={result.exit_code}\n{result.output}"
    )
    assert "no such command" in result.output.lower(), (
        f"Expected 'No such command' after v1.2 cleanup, got:\n{result.output}"
    )


def test_patch_help_hides_debug_commands():
    """patch --help must not list preview/apply/rollback in the Commands table."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["patch", "--help"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    listed = _listed_commands(result.output)
    for cmd in ("preview", "apply", "rollback"):
        assert cmd not in listed, (
            f"Expected `{cmd}` to be hidden from `patch --help` Commands table, "
            f"but it is listed. Commands found: {listed}\nFull output:\n{result.output}"
        )


def test_patch_preview_still_invokable():
    """Hidden `patch preview` must still respond to --help."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["patch", "preview", "--help"])
    assert result.exit_code == 0, (
        f"Hidden `patch preview` should still be invokable, "
        f"exit_code={result.exit_code}\n{result.output}"
    )


def test_prompt_help_hides_hash_and_diff():
    """prompt --help must not list hash/diff in the Commands table."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["prompt", "--help"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    listed = _listed_commands(result.output)
    for cmd in ("hash", "diff"):
        assert cmd not in listed, (
            f"Expected `{cmd}` to be hidden from `prompt --help` Commands table, "
            f"but it is listed. Commands found: {listed}\nFull output:\n{result.output}"
        )
    # Visible commands still present in the Commands table.
    for cmd in ("list", "show"):
        assert cmd in listed, (
            f"Expected `{cmd}` to be visible in `prompt --help` Commands table, "
            f"but it is missing. Commands found: {listed}\nFull output:\n{result.output}"
        )


def test_bridge_run_hidden_from_help():
    """bridge --help must not list run in the Commands table."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["bridge", "--help"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    listed = _listed_commands(result.output)
    assert "run" not in listed, (
        f"Expected `run` to be hidden from `bridge --help` Commands table, "
        f"but it is listed. Commands found: {listed}\nFull output:\n{result.output}"
    )


def test_bridge_run_still_invokable():
    """Hidden `bridge run` must still respond to --help."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["bridge", "run", "--help"])
    assert result.exit_code == 0, (
        f"Hidden `bridge run` should still be invokable, "
        f"exit_code={result.exit_code}\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Task C: root help no longer contains stale command enumeration
# ---------------------------------------------------------------------------

def test_root_help_no_stale_enumeration():
    """Root --help must not enumerate stale command names like 'cost / budget / cache'."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    # The old stale enumeration contained this exact substring.
    assert "cost / budget / cache" not in result.output, (
        "Stale command enumeration 'cost / budget / cache' still appears in root --help"
    )


def test_root_help_still_has_basic_content():
    """Root --help must still contain useful, non-stale content."""
    from modelbridge.cli import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # The generic replacement text (or at least key terms) should be present.
    assert "mbridge" in result.output.lower()
