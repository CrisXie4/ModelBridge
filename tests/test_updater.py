"""Tests for the self-update support (``modelbridge.updater``) and the
version-display surface.

No real network calls: ``fetch_latest_release`` is monkeypatched and the
cache lives under an isolated ``MBRIDGE_HOME`` tmp dir.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelbridge import __version__, updater
from modelbridge.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Version single-sourcing
# ---------------------------------------------------------------------------

def _grep_version(path: Path, pattern: str) -> str:
    m = re.search(pattern, path.read_text("utf-8"))
    assert m, f"version not found in {path}"
    return m.group(1)


def test_pyproject_version_matches_dunder():
    ver = _grep_version(REPO_ROOT / "pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"')
    assert ver == __version__


def test_installer_version_matches_dunder():
    iss = REPO_ROOT / "packaging" / "installer.iss"
    ver = _grep_version(iss, r'MyAppVersion\s+"([^"]+)"')
    assert ver == __version__


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("V1.0", (1, 0)),
        ("1.2.3-rc1", (1, 2, 3)),
        ("2.0.0+build5", (2, 0, 0)),
        ("garbage", ()),
        ("", ()),
    ],
)
def test_parse_version(raw, expected):
    assert updater.parse_version(raw) == expected


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("1.0.1", "1.0.0", True),
        ("v1.0.1", "1.0.0", True),
        ("1.1.0", "1.0.9", True),
        ("2.0.0", "1.9.9", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.0", "1.0.1", False),
        ("1.0", "1.0.0", False),       # padded equal
        ("1.0.0.0", "1.0.0", False),   # padded equal
        ("garbage", "1.0.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert updater.is_newer(latest, current) is newer


# ---------------------------------------------------------------------------
# Asset selection
# ---------------------------------------------------------------------------

def _release_with(*names: str) -> updater.ReleaseInfo:
    return updater.ReleaseInfo(
        version="1.0.1",
        tag="v1.0.1",
        html_url="https://example/releases/latest",
        assets=[updater.Asset(name=n, url=f"https://dl/{n}", size=1) for n in names],
    )


def test_pick_asset_windows(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    rel = _release_with(
        "ModelBridge-Setup-1.0.1.exe",
        "mbridge-1.0.1-linux-x86_64.tar.gz",
    )
    asset = updater.pick_asset(rel)
    assert asset is not None and asset.name.endswith(".exe")


def test_pick_asset_macos_arm(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
    rel = _release_with(
        "mbridge-1.0.1-macos-x86_64.tar.gz",
        "mbridge-1.0.1-macos-arm64.tar.gz",
        "mbridge-1.0.1-linux-x86_64.tar.gz",
    )
    asset = updater.pick_asset(rel)
    assert asset is not None and "macos-arm64" in asset.name


def test_pick_asset_linux_aarch64_alias(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    monkeypatch.setattr(updater.platform, "machine", lambda: "aarch64")
    rel = _release_with(
        "mbridge-1.0.1-linux-x86_64.tar.gz",
        "mbridge-1.0.1-linux-arm64.tar.gz",
    )
    asset = updater.pick_asset(rel)
    assert asset is not None and "linux-arm64" in asset.name


def test_pick_asset_none_when_no_match(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")
    rel = _release_with("ModelBridge-Setup-1.0.1.exe")
    assert updater.pick_asset(rel) is None


# ---------------------------------------------------------------------------
# Cached check
# ---------------------------------------------------------------------------

def test_check_for_update_returns_newer(home, monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_latest_release",
        lambda *a, **k: updater.ReleaseInfo(
            version="9.9.9", tag="v9.9.9", html_url="https://x", assets=[],
        ),
    )
    rel = updater.check_for_update(force=True, current="1.0.0")
    assert rel is not None and rel.version == "9.9.9"
    # Cache file written.
    assert (home / "update_check.json").exists()


def test_check_for_update_none_when_same(home, monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_latest_release",
        lambda *a, **k: updater.ReleaseInfo(
            version="1.0.0", tag="v1.0.0", html_url="https://x", assets=[],
        ),
    )
    assert updater.check_for_update(force=True, current="1.0.0") is None


def test_check_for_update_uses_cache(home, monkeypatch):
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return updater.ReleaseInfo(
            version="2.0.0", tag="v2.0.0", html_url="https://x", assets=[],
        )

    monkeypatch.setattr(updater, "fetch_latest_release", _fake)
    # First call hits the network and caches.
    assert updater.check_for_update(current="1.0.0") is not None
    # Second call within TTL must NOT hit the network again.
    assert updater.check_for_update(current="1.0.0") is not None
    assert calls["n"] == 1


def test_check_for_update_network_failure_is_silent(home, monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", lambda *a, **k: None)
    assert updater.check_for_update(force=True, current="1.0.0") is None


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_version_flag():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert __version__ in _ANSI_RE.sub("", r.output)


def test_cli_version_command():
    """`mbridge version` was PHYSICALLY REMOVED in v1.2; canonical = --version flag."""
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 2, f"exit_code={r.exit_code}\n{r.output}"
    assert "no such command" in r.output.lower(), r.output

    # Canonical: --version flag (tests/cli CLI surface).
    r2 = runner.invoke(app, ["--version"])
    assert r2.exit_code == 0, f"exit_code={r2.exit_code}\n{r2.output}"
    assert "mbridge" in r2.output.lower()


def test_cli_update_uptodate(home, monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda *a, **k: None)
    r = runner.invoke(app, ["update"])
    assert r.exit_code == 0
    assert "最新" in r.output


def test_cli_update_source_install(home, monkeypatch):
    rel = updater.ReleaseInfo(
        version="9.9.9", tag="v9.9.9", html_url="https://x/rel", assets=[],
    )
    monkeypatch.setattr(updater, "check_for_update", lambda *a, **k: rel)
    monkeypatch.setattr(updater, "install_mode", lambda: "source")
    r = runner.invoke(app, ["update", "--yes"])
    assert r.exit_code == 0
    assert "pip install" in r.output
