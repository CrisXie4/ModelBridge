"""prompt_toolkit completer for ``/slash`` commands.

Complements :class:`~modelbridge.agent.at_completer.AtFileCompleter` (which
handles ``@file`` mentions).

Two phases of completion:

1. **Command name** — while the cursor is inside the first whitespace-delimited
   token of the line (starting with ``/``), offer ``/command`` names from the
   live registry (:func:`modelbridge.agent.commands.slash_command_help`).
2. **Subcommand / argument** — once the user types a space after the command
   name, offer context-aware completions for commands that have a known set of
   subcommands (``/think on|off|level|auto|collapse``, ``/mcp list|tools|…``,
   ``/debug on|off``, ``/auto on|off``, ``/init --force --yes``) plus model
   names for ``/model``.

The subcommand tables are module-level constants so they're trivially
testable without a TTY.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from .commands import slash_command_help


# Per-command subcommand / flag tables. Order matters — the first items are
# shown at the top of the dropdown and accept on a single Tab when there's a
# unique prefix match.
_SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
    "think": [
        ("on", "开启 thinking"),
        ("off", "关闭 thinking"),
        ("level", "设置级别 (1-10 / low / med / high / xhigh)"),
        ("auto", "重置为当前模型的默认级别"),
        ("collapse", "设置折叠阈值 (字符数)"),
    ],
    "mcp": [
        ("list", "列出 MCP servers"),
        ("tools", "列出 MCP 工具"),
        ("on", "启用某个 server"),
        ("off", "停用某个 server"),
        ("refresh", "重连 + 刷新能力目录"),
        ("read", "读取一个资源并注入会话"),
        ("prompt", "用参数填充一个 MCP prompt"),
    ],
    "debug": [("on", "开启调试日志"), ("off", "关闭调试日志")],
    "dbg": [("on", "开启调试日志"), ("off", "关闭调试日志")],
    "auto": [("on", "开启 AI 自动判断安全模式"), ("off", "关闭 auto 模式")],
    "init": [("--force", "覆盖已存在的 AGENT.md"), ("--yes", "跳过确认直接写入")],
    "update": [],
    "upgrade": [],
}

# Commands whose first argument is a model name (resolved dynamically via the
# optional model_names_provider callback).
_MODEL_ARG_COMMANDS: frozenset[str] = frozenset({"model"})


class SlashCommandCompleter(Completer):
    """Offer ``/command`` completions, then subcommands for known commands."""

    def __init__(
        self,
        *,
        model_names_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._help = slash_command_help()
        # Lazy provider so the completer doesn't trigger a config load on
        # construction (only when the user actually types `/model <prefix>`).
        self._model_names_provider = model_names_provider

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        # Only complete a leading slash command. We look at the line the
        # cursor is on so a ``/`` mid-multiline input doesn't fire.
        line_up_to_cursor = text.rsplit("\n", 1)[-1]
        if not line_up_to_cursor.startswith("/"):
            return

        body = line_up_to_cursor[1:]  # strip the leading "/"

        # Phase 1: still inside the command name (no space entered yet).
        if " " not in body:
            name_part = body.lstrip()  # tolerate "//" double-slash typos
            for cmd, desc in self._help.items():
                if not name_part or cmd.startswith(name_part):
                    yield Completion(
                        text=cmd,
                        start_position=-len(name_part),
                        display="/" + cmd,
                        display_meta=desc[:40] if desc else "",
                    )
            return

        # Phase 2: command name is finished — offer subcommands / args.
        # Split on the FIRST space only. ``body.find(" ")`` is safe because
        # we know there's at least one space (Phase 1 guard above).
        idx = body.find(" ")
        name = body[:idx].lower()
        rest = body[idx + 1:]  # everything after the first space (may be "")

        # If the user has already typed a second space, they're past the
        # first argument — we only complete the first arg, so stop.
        if " " in rest.strip():
            return
        arg_prefix = rest.lstrip()

        # Model-name completion for /model <prefix>
        if name in _MODEL_ARG_COMMANDS and self._model_names_provider is not None:
            try:
                names = self._model_names_provider() or []
            except Exception:
                names = []
            for m in names:
                if not arg_prefix or m.startswith(arg_prefix):
                    yield Completion(
                        text=m,
                        start_position=-len(arg_prefix),
                        display=m,
                        display_meta="model",
                    )
            return

        # Static subcommand table.
        subs = _SUBCOMMANDS.get(name)
        if not subs:
            return
        for token, desc in subs:
            if not arg_prefix or token.startswith(arg_prefix):
                yield Completion(
                    text=token,
                    start_position=-len(arg_prefix),
                    display=token,
                    display_meta=desc[:40] if desc else "",
                )


__all__ = ["SlashCommandCompleter"]
