"""Register / unregister the LocalBridge Native Messaging host.

Chrome (and Edge) launch a native host by reading a **host manifest** JSON
that declares the host's executable ``path`` and which extension(s) may talk
to it (``allowed_origins`` — Chrome forbids wildcards, so a concrete extension
id is required).

How the OS finds that manifest:

* **Windows** — a registry value under
  ``HKCU\\Software\\<vendor>\\NativeMessagingHosts\\<host name>`` whose default
  value is the absolute path to the manifest file.
* **POSIX** — the manifest is dropped into a per-browser
  ``NativeMessagingHosts/`` directory.

The host is launched via a small generated launcher (``.bat`` on Windows, a
shell script on POSIX) that runs ``<this python> -m modelbridge.bridge.host``.
Using the *current interpreter* (``sys.executable``) avoids depending on PATH
or on the ``mbridge-bridge`` console script being resolvable from Chrome's
environment.

Everything lives under ``~/.modelbridge/native_host/``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ..utils import get_app_dir
from . import HOST_NAME

# Browsers we register for, with their Windows registry vendor path and POSIX
# manifest directories (relative to $HOME).
_BROWSERS: dict[str, dict[str, str]] = {
    "chrome": {
        "win_reg": r"Software\Google\Chrome\NativeMessagingHosts",
        "linux": ".config/google-chrome/NativeMessagingHosts",
        "darwin": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
    },
    "edge": {
        "win_reg": r"Software\Microsoft\Edge\NativeMessagingHosts",
        "linux": ".config/microsoft-edge/NativeMessagingHosts",
        "darwin": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    },
}

DEFAULT_BROWSERS = ("chrome", "edge")


@dataclass
class InstallResult:
    manifest_path: Path
    launcher_path: Path
    registered: list[str]  # human-readable per-browser outcomes
    extension_id: str


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def native_host_dir() -> Path:
    return get_app_dir() / "native_host"


def manifest_path() -> Path:
    return native_host_dir() / f"{HOST_NAME}.json"


def launcher_path() -> Path:
    name = "mbridge-bridge.bat" if sys.platform == "win32" else "mbridge-bridge.sh"
    return native_host_dir() / name


def saved_extension_id_path() -> Path:
    return native_host_dir() / "extension_id.txt"


# ---------------------------------------------------------------------------
# Generated files
# ---------------------------------------------------------------------------

def _write_launcher() -> Path:
    d = native_host_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = launcher_path()
    py = sys.executable
    if sys.platform == "win32":
        # %* forwards Chrome's argv (origin + parent window handle).
        content = f'@echo off\r\n"{py}" -m modelbridge.bridge.host %*\r\n'
    else:
        content = f'#!/bin/sh\nexec "{py}" -m modelbridge.bridge.host "$@"\n'
    path.write_text(content, encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path


def _write_manifest(extension_id: str, launcher: Path) -> Path:
    d = native_host_dir()
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": HOST_NAME,
        "description": "ModelBridge LocalBridge native host",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    path = manifest_path()
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_windows(browsers: tuple[str, ...], manifest: Path) -> list[str]:
    import winreg

    out: list[str] = []
    for b in browsers:
        reg_path = _BROWSERS[b]["win_reg"] + "\\" + HOST_NAME
        try:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_WRITE)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
            winreg.CloseKey(key)
            out.append(f"{b}: HKCU\\{reg_path}")
        except OSError as e:
            out.append(f"{b}: 注册失败 ({e})")
    return out


def _register_posix(browsers: tuple[str, ...], manifest: Path) -> list[str]:
    home = Path.home()
    key = "darwin" if sys.platform == "darwin" else "linux"
    out: list[str] = []
    for b in browsers:
        dest_dir = home / _BROWSERS[b][key]
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{HOST_NAME}.json"
            dest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
            out.append(f"{b}: {dest}")
        except OSError as e:
            out.append(f"{b}: 写入失败 ({e})")
    return out


def install(extension_id: str, *, browsers: tuple[str, ...] = DEFAULT_BROWSERS) -> InstallResult:
    """Write launcher + manifest and register the host for ``browsers``."""
    extension_id = extension_id.strip()
    if not extension_id:
        raise ValueError("extension_id 不能为空")
    launcher = _write_launcher()
    manifest = _write_manifest(extension_id, launcher)
    saved_extension_id_path().write_text(extension_id, encoding="utf-8")

    if sys.platform == "win32":
        registered = _register_windows(browsers, manifest)
    else:
        registered = _register_posix(browsers, manifest)

    return InstallResult(
        manifest_path=manifest,
        launcher_path=launcher,
        registered=registered,
        extension_id=extension_id,
    )


def uninstall(*, browsers: tuple[str, ...] = DEFAULT_BROWSERS) -> list[str]:
    """Remove the registry entries / dropped manifests. Best-effort."""
    out: list[str] = []
    if sys.platform == "win32":
        import winreg

        for b in browsers:
            reg_path = _BROWSERS[b]["win_reg"] + "\\" + HOST_NAME
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, reg_path)
                out.append(f"{b}: 已删除 HKCU\\{reg_path}")
            except FileNotFoundError:
                out.append(f"{b}: 未注册 (跳过)")
            except OSError as e:
                out.append(f"{b}: 删除失败 ({e})")
    else:
        home = Path.home()
        key = "darwin" if sys.platform == "darwin" else "linux"
        for b in browsers:
            dest = home / _BROWSERS[b][key] / f"{HOST_NAME}.json"
            try:
                dest.unlink()
                out.append(f"{b}: 已删除 {dest}")
            except FileNotFoundError:
                out.append(f"{b}: 未注册 (跳过)")
            except OSError as e:
                out.append(f"{b}: 删除失败 ({e})")
    return out


def load_saved_extension_id() -> str | None:
    try:
        return saved_extension_id_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


__all__ = [
    "InstallResult",
    "DEFAULT_BROWSERS",
    "native_host_dir",
    "manifest_path",
    "launcher_path",
    "install",
    "uninstall",
    "load_saved_extension_id",
]
