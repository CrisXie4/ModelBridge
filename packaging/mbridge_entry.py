"""PyInstaller entry point for ``mbridge``.

PyInstaller's frozen binaries need a real ``.py`` script with a normal
``if __name__ == "__main__":`` block — they don't follow the
``[project.scripts]`` shim that ``pip install`` materialises. This file
exists solely so that ``mbridge.spec`` can target a single ``.py`` file
and produce a clean ``mbridge.exe`` / ``mbridge`` binary.

Keep this file tiny — every import here gets traced into the frozen
bundle. Module-level side effects in the package import path
(``modelbridge.cli`` runs Typer's decorators) already register every
command, so calling ``app()`` is enough.
"""

from __future__ import annotations

import multiprocessing


def main() -> None:
    # Required when freezing on Windows; harmless elsewhere. Without this,
    # any code path that fork-imports the bundle (rare for a CLI, but
    # rich's threading uses it on some setups) can spawn endless child
    # processes when running the frozen .exe.
    multiprocessing.freeze_support()

    from modelbridge.cli import app
    app()


if __name__ == "__main__":
    main()
