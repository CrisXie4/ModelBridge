"""Tests for numeric-field validation in load_mcp_settings().

Confirms that a bad value like ``reconnect_attempts: notanumber`` raises
``MCPConfigError`` (not a bare ValueError/TypeError) and that a valid mcp:
block loads with correct numeric values.

Sandbox strategy: set ``MBRIDGE_HOME`` to a tmp dir so get_config_path()
resolves to ``<tmp>/config.yaml``.  No init needed — we write the YAML
directly.
"""

from __future__ import annotations

import pytest

from modelbridge.mcp.config import load_mcp_settings
from modelbridge.mcp.errors import MCPConfigError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mbridge_home(tmp_path, monkeypatch):
    """Isolated MBRIDGE_HOME backed by a temp directory."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1 – bad numeric value raises MCPConfigError, not ValueError
# ---------------------------------------------------------------------------

def test_bad_reconnect_attempts_raises_mcp_config_error(mbridge_home):
    """Non-numeric reconnect_attempts must raise MCPConfigError, not ValueError."""
    (mbridge_home / "config.yaml").write_text(
        "mcp:\n"
        "  enabled: true\n"
        "  reconnect_attempts: notanumber\n",
        encoding="utf-8",
    )
    with pytest.raises(MCPConfigError):
        load_mcp_settings()


# ---------------------------------------------------------------------------
# Test 2 – a valid mcp: block loads fine with correct numeric fields
# ---------------------------------------------------------------------------

def test_valid_mcp_block_loads_numeric_fields(mbridge_home):
    """A well-formed mcp: block should load and expose correct numeric values."""
    (mbridge_home / "config.yaml").write_text(
        "mcp:\n"
        "  enabled: true\n"
        "  reconnect_attempts: 5\n"
        "  reconnect_backoff: 1.5\n"
        "  heartbeat_interval: 30.0\n"
        "  sampling:\n"
        "    max_tokens: 4096\n"
        "    max_calls: 64\n",
        encoding="utf-8",
    )
    settings = load_mcp_settings()
    assert settings.reconnect_attempts == 5
    assert settings.reconnect_backoff == 1.5
    assert settings.heartbeat_interval == 30.0
    assert settings.sampling_max_tokens == 4096
    assert settings.sampling_max_calls == 64
