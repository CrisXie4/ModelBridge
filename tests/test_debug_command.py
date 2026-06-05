"""Tests for the ``/debug on|off`` command and the runtime debug toggle.

Uses an isolated ``MBRIDGE_HOME`` so the log file lands in a tmp dir, and a
recording rich ``Console`` to capture the command's output.
"""

from __future__ import annotations

import logging

import pytest
from rich.console import Console

from modelbridge import utils
from modelbridge.agent.commands import SlashContext, handle_slash


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    # Reset the module-level logger state so each test starts clean.
    monkeypatch.setattr(utils, "_logger_configured", False)
    monkeypatch.setattr(utils, "_debug_enabled", False)
    lg = logging.getLogger("modelbridge")
    lg.handlers.clear()
    lg.disabled = False
    return tmp_path


def _ctx() -> tuple[SlashContext, Console]:
    console = Console(record=True, width=100)
    sctx = SlashContext(
        console=console,
        session=None,
        agent_ctx=None,
        registry=None,
        model_name="dummy",
        entry=None,
        thinking_state={},
    )
    return sctx, console


# ---------------------------------------------------------------------------
# utils.set_debug / is_debug
# ---------------------------------------------------------------------------

def test_set_debug_on_enables_debug_level(home):
    assert utils.is_debug() is False
    path = utils.set_debug(True)
    assert utils.is_debug() is True
    lg = logging.getLogger("modelbridge")
    assert lg.level == logging.DEBUG
    assert lg.disabled is False
    assert path is not None and path.name == "mbridge.log"


def test_set_debug_off_mutes_logger(home):
    utils.set_debug(True)
    assert utils.set_debug(False) is None
    assert utils.is_debug() is False
    assert logging.getLogger("modelbridge").disabled is True


def test_set_debug_writes_log_file(home):
    utils.set_debug(True)
    logging.getLogger("modelbridge").debug("hello-debug")
    for h in logging.getLogger("modelbridge").handlers:
        h.flush()
    log_file = home / "logs" / "mbridge.log"
    assert log_file.exists()
    assert "hello-debug" in log_file.read_text("utf-8")


# ---------------------------------------------------------------------------
# /debug command
# ---------------------------------------------------------------------------

def test_debug_on_command(home):
    sctx, console = _ctx()
    handle_slash("/debug on", sctx)
    assert utils.is_debug() is True
    assert "已开启" in console.export_text()


def test_debug_off_command(home):
    utils.set_debug(True)
    sctx, console = _ctx()
    handle_slash("/debug off", sctx)
    assert utils.is_debug() is False
    assert "已关闭" in console.export_text()


def test_debug_no_arg_shows_state(home):
    sctx, console = _ctx()
    handle_slash("/debug", sctx)
    out = console.export_text()
    assert "off" in out and "用法" in out


def test_debug_alias_dbg(home):
    sctx, _ = _ctx()
    handle_slash("/dbg on", sctx)
    assert utils.is_debug() is True


def test_debug_unknown_arg(home):
    sctx, console = _ctx()
    handle_slash("/debug maybe", sctx)
    assert "未知参数" in console.export_text()
    assert utils.is_debug() is False
