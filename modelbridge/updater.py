"""Self-update support: check GitHub Releases for a newer ModelBridge and
fetch the right asset for the current platform.

Distribution model (see ``packaging/``): ModelBridge ships as standalone
PyInstaller binaries attached to a GitHub Release on every ``v*.*.*`` tag.
This module lets a running ``mbridge`` notice a newer release and download
the matching asset. We deliberately stop at *download + instructions* —
the user completes the actual install (we reveal the download folder and
print platform-specific steps), which keeps the flow safe and predictable
across Windows / macOS / Linux.

Everything here is best-effort: an update check must NEVER slow down or
crash the CLI, so network/parse failures degrade silently to "no update".
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import __version__
from .utils import get_app_dir

REPO = "CrisXie4/ModelBridge"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# How long a cached check stays "fresh" — keeps startup instant and avoids
# hammering the GitHub API (unauthenticated calls are rate-limited).
_CHECK_TTL_HOURS = 24
# Short timeout so a slow network never stalls REPL startup.
_HTTP_TIMEOUT = 3.0
_DOWNLOAD_TIMEOUT = 120.0


@dataclass
class Asset:
    """A single downloadable file attached to a release."""

    name: str
    url: str
    size: int = 0


@dataclass
class ReleaseInfo:
    """The bits of a GitHub Release we care about."""

    version: str          # normalised, no leading 'v' (e.g. "1.0.1")
    tag: str              # original tag (e.g. "v1.0.1")
    html_url: str
    assets: list[Asset] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

_VER_RE = re.compile(r"(\d+(?:\.\d+)*)")


def parse_version(s: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints.

    Strips a leading ``v`` and any pre-release/build suffix, keeping the
    leading dotted-numeric core (``v1.2.3-rc1`` → ``(1, 2, 3)``). Returns
    an empty tuple if nothing numeric is found.
    """
    if not s:
        return ()
    s = s.strip().lstrip("vV")
    m = _VER_RE.match(s)
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    lt, ct = parse_version(latest), parse_version(current)
    if not lt:
        return False
    n = max(len(lt), len(ct))
    lt += (0,) * (n - len(lt))
    ct += (0,) * (n - len(ct))
    return lt > ct


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_latest_release(timeout: float = _HTTP_TIMEOUT) -> ReleaseInfo | None:
    """Query the GitHub Releases API. Returns None on any error."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001 — update checks must never crash the CLI
        return None

    tag = data.get("tag_name") or ""
    if not tag:
        return None
    assets = [
        Asset(
            name=a.get("name", ""),
            url=a.get("browser_download_url", ""),
            size=int(a.get("size") or 0),
        )
        for a in (data.get("assets") or [])
        if a.get("browser_download_url")
    ]
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        html_url=data.get("html_url") or RELEASES_PAGE,
        assets=assets,
    )


# ---------------------------------------------------------------------------
# Cached check
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    return get_app_dir() / "update_check.json"


def _read_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(d: dict) -> None:
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def _release_from_cache(cache: dict) -> ReleaseInfo:
    return ReleaseInfo(
        version=cache.get("latest", ""),
        tag=cache.get("tag", f"v{cache.get('latest', '')}"),
        html_url=cache.get("html_url", RELEASES_PAGE),
        assets=[Asset(**a) for a in cache.get("assets", [])],
    )


def check_for_update(
    *,
    force: bool = False,
    current: str = __version__,
    ttl_hours: int = _CHECK_TTL_HOURS,
) -> ReleaseInfo | None:
    """Return a :class:`ReleaseInfo` if a newer version is available, else None.

    The result is cached in ``~/.modelbridge/update_check.json`` for
    ``ttl_hours`` so the network is only hit occasionally. Pass
    ``force=True`` to bypass the cache. Any failure (offline, rate-limited,
    malformed response) returns None silently.
    """
    cache = _read_cache()
    now = datetime.now(timezone.utc)

    if not force and cache.get("checked_at"):
        try:
            checked = datetime.fromisoformat(cache["checked_at"])
            fresh = (now - checked).total_seconds() < ttl_hours * 3600
        except Exception:  # noqa: BLE001
            fresh = False
        if fresh:
            latest = cache.get("latest", "")
            if latest and is_newer(latest, current):
                return _release_from_cache(cache)
            return None

    info = fetch_latest_release()
    if info is None:
        return None  # don't poison the cache on a failed fetch
    _write_cache(
        {
            "checked_at": now.isoformat(),
            "latest": info.version,
            "tag": info.tag,
            "html_url": info.html_url,
            "assets": [
                {"name": a.name, "url": a.url, "size": a.size} for a in info.assets
            ],
        }
    )
    return info if is_newer(info.version, current) else None


# ---------------------------------------------------------------------------
# Platform asset selection
# ---------------------------------------------------------------------------

_ARCH_ALIASES = {
    "x86_64": ("x86_64", "amd64", "x64"),
    "amd64": ("x86_64", "amd64", "x64"),
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
}


def pick_asset(release: ReleaseInfo) -> Asset | None:
    """Choose the release asset matching the current OS/arch, or None.

    Mirrors the artifact names produced by ``release.yml`` /
    ``packaging/``: ``ModelBridge-Setup-*.exe`` (Windows),
    ``mbridge-*-macos-<arch>.tar.gz`` / ``mbridge-*-linux-<arch>.tar.gz``.
    """
    system = platform.system().lower()       # 'windows' | 'darwin' | 'linux'
    machine = platform.machine().lower()
    named = [(a, a.name.lower()) for a in release.assets if a.name]

    if system == "windows":
        for a, n in named:
            if n.endswith(".exe"):
                return a
        return None

    plat = {"darwin": "macos", "linux": "linux"}.get(system, system)
    arches = _ARCH_ALIASES.get(machine, (machine,))

    # Prefer an exact platform + arch match …
    for a, n in named:
        if plat in n and n.endswith(".tar.gz") and any(al in n for al in arches):
            return a
    # … otherwise any tarball for this platform.
    for a, n in named:
        if plat in n and n.endswith(".tar.gz"):
            return a
    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_dir() -> Path:
    d = get_app_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_asset(
    asset: Asset,
    *,
    timeout: float = _DOWNLOAD_TIMEOUT,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Stream ``asset`` into ``~/.modelbridge/downloads/`` and return the path.

    ``progress`` (if given) is called as ``progress(bytes_done, total)``.
    Raises on network/IO error so the caller can fall back to the release
    page.
    """
    # Sanitise the asset name to a bare basename — never trust the Releases
    # API to keep it path-free. A name containing '/', '\\', '..' or a drive
    # letter must not let the write escape the downloads dir.
    safe = Path(asset.name).name
    if not safe or safe in (".", ".."):
        raise ValueError(f"unsafe asset name: {asset.name!r}")
    ddir = download_dir().resolve()
    dest = ddir / safe
    if not _is_within(dest, ddir):
        raise ValueError(f"asset would escape download dir: {asset.name!r}")
    tmp = dest.with_name(dest.name + ".part")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", asset.url) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or asset.size or 0)
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
        tmp.replace(dest)
    except BaseException:
        # Network error / timeout / Ctrl-C mid-stream: don't leave a partial
        # .part file behind to accumulate or masquerade as a finished download.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Install helpers (download + guide; we don't run the installer for the user)
# ---------------------------------------------------------------------------

def install_mode() -> str:
    """``'frozen'`` for PyInstaller binaries, ``'source'`` for pip/source."""
    return "frozen" if getattr(sys, "frozen", False) else "source"


def reveal_in_file_manager(path: Path) -> None:
    """Best-effort: open the OS file manager at ``path``'s folder."""
    try:
        system = platform.system().lower()
        if system == "windows":
            os.startfile(str(path.parent))  # type: ignore[attr-defined]  # Windows-only
        elif system == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:  # noqa: BLE001 — opening the folder is a convenience, never fatal
        pass


def install_instructions(asset_path: Path) -> str:
    """Return platform-specific, copy-pasteable install steps for a download."""
    system = platform.system().lower()
    name = asset_path.name
    if system == "windows":
        return (
            f"1. 双击运行安装器：{asset_path}\n"
            f"2. 按提示完成安装（会自动覆盖旧版本并更新 PATH）。\n"
            f"3. 重新打开终端，运行 `mbridge version` 确认。"
        )
    if system == "darwin":
        return (
            f"1. 解压：tar xzf \"{asset_path}\" -C ~/.local\n"
            f"2. 确保 ~/.local/mbridge 在 PATH 中（或替换原安装目录）。\n"
            f"3. 运行 `mbridge version` 确认。"
        )
    if system == "linux":
        return (
            f"1. 解压：tar xzf \"{asset_path}\" -C ~/.local\n"
            f"2. 确保 ~/.local/mbridge 在 PATH 中（或替换原安装目录）。\n"
            f"3. 运行 `mbridge version` 确认。"
        )
    return f"已下载：{asset_path}\n请按你的安装方式替换旧版本（文件名：{name}）。"


def source_upgrade_hint(tag: str) -> str:
    """Upgrade hint for users who installed from source / pip (not a binary)."""
    return (
        "检测到你是源码 / pip 安装，直接用 pip 升级即可：\n"
        f"  pip install --upgrade "
        f"\"git+https://github.com/{REPO}@{tag}\""
    )
