"""LocalBridge — the Native Messaging host that fronts the agent engine.

The browser side-panel extension talks to this process over Chrome Native
Messaging (length-prefixed JSON on stdio). The host reuses the existing
:mod:`modelbridge` engine (providers / router / ``run_agent_turn`` /
``AgentContext``) and adds **remote browser tools** whose execution is
round-tripped back into the page.

Public surface:

* :mod:`~modelbridge.bridge.protocol` — stdio framing + message builders.
* :mod:`~modelbridge.bridge.host` — the stdio main loop (``main`` entry point).
* :mod:`~modelbridge.bridge.install` — register/unregister the host manifest.

The ``mbridge bridge ...`` CLI (see :mod:`modelbridge.bridge.cli`) wraps the
installer; Chrome itself launches ``mbridge-bridge`` (or ``mbridge bridge
run``) as the host process.
"""

from __future__ import annotations

HOST_NAME = "com.modelbridge.localbridge"

__all__ = ["HOST_NAME"]
