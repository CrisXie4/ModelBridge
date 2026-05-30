# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ``mbridge`` CLI.

This spec is written with two goals:

1. **Onedir layout** (``COLLECT(...)``) — startup is fast (no per-run
   temp-extract) and Inno Setup can just pull the directory into the
   installer. ``--onefile`` would also work but adds 1–2 s startup that
   matters for an interactive CLI.
2. **Hidden imports** — Typer/Pydantic/httpx are PyInstaller-friendly
   *most of the time* but each has one or two modules that the static
   analyser misses. They are listed explicitly below so the bundle works
   even on a fresh Windows / mac / Linux machine.

Build:

    pyinstaller packaging/mbridge.spec --noconfirm --clean

Output: ``dist/mbridge/`` (the directory) and ``dist/mbridge/mbridge.exe``
(the launcher).
"""

from __future__ import annotations

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


# Repo root — ``mbridge.spec`` lives in ``packaging/``, the project lives
# one level up. ``SPECPATH`` is the directory containing the spec file
# (PyInstaller exposes it as a global).
PROJECT_ROOT = Path(SPECPATH).parent.resolve()
ENTRY = PROJECT_ROOT / "packaging" / "mbridge_entry.py"

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# - ``modelbridge.providers`` — every adapter is imported lazily by name
#   in registry.py. Pull them all in via collect_submodules so a missing
#   ``deepseek.py`` doesn't crash the frozen binary at first use.
# - ``pydantic`` — v2 splits internals across many modules; let
#   PyInstaller's hook contribute everything.
# - ``rich`` / ``typer`` — both pull in lazy submodules for theming and
#   completion.
# - ``httpx`` — needs ``httpcore._sync.http11`` etc. PyInstaller usually
#   finds them, but collect_submodules makes it explicit.
hiddenimports: list[str] = []
hiddenimports += collect_submodules("modelbridge")
hiddenimports += collect_submodules("modelbridge.providers")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")
hiddenimports += collect_submodules("typer")
hiddenimports += collect_submodules("click")
hiddenimports += collect_submodules("rich")
hiddenimports += collect_submodules("httpx")
hiddenimports += collect_submodules("httpcore")
hiddenimports += collect_submodules("anyio")

# ---------------------------------------------------------------------------
# Package metadata (``importlib.metadata.version("modelbridge")``)
# ---------------------------------------------------------------------------
datas: list[tuple[str, str]] = []
try:
    datas += copy_metadata("modelbridge")
except Exception:  # noqa: BLE001 — metadata may not exist in editable installs
    pass
for pkg in ("pydantic", "typer", "rich", "httpx", "click"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001
        pass


a = Analysis(
    [str(ENTRY)],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Drop heavyweight test deps if they leaked into the env.
        "pytest", "_pytest",
        "IPython", "matplotlib", "numpy", "pandas", "scipy",
        "tkinter", "test", "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)


# ``console=True`` is essential — this is a CLI tool. On Windows that
# means the launcher attaches a console; double-clicking the .exe still
# works (a console window opens, ``--help`` is printed).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mbridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="mbridge",
)
