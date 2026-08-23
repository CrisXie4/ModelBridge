"""Agent loop — the heart of multi-turn + tool calls.

Two callers:

* :func:`run_agent_turn` — given a populated :class:`Session`, send one
  user turn (already appended) and **resolve every tool_call** until the
  model returns a final answer (``finish_reason != "tool_calls"``). This
  is what the REPL calls after each user input.

* :func:`run_interactive` — the REPL itself. Reads from a callable
  ``read_input`` (so the CLI can supply a rich-prompt and tests can
  feed canned inputs), prints the assistant turn, dispatches tools, and
  loops until EOF / Ctrl-D / ``/exit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..cache.affinity import session_cache_key
from ..client import get_model_entry
from ..providers import ProviderError, get_provider
from ..schemas import ChatRequest, ChatResponse
from .context import AgentContext
from .session import Session
from .tools import ToolCall, ToolRegistry, parse_tool_calls


@dataclass
class AgentResult:
    """What :func:`run_agent_turn` returns to the caller."""

    final_response: ChatResponse | None
    iterations: int
    tool_calls_executed: list[ToolCall] = field(default_factory=list)
    stopped_reason: str = "stop"  # stop | max_iters | provider_error
    error: ProviderError | None = None


def run_agent_turn(
    *,
    session: Session,
    ctx: AgentContext,
    registry: ToolRegistry,
    model_name: str,
    timeout: float = 120.0,
    # Runaway guard only: caps the number of model iterations per turn.
    # ``None`` (default) = unlimited — the model decides when to stop.
    max_iters: int | None = None,
    stream: bool = False,
    thinking: bool | None = None,
    thinking_budget: int | None = None,
    on_assistant_start: Callable[[], None] | None = None,
    on_content_delta: Callable[[str], None] | None = None,
    on_reasoning_delta: Callable[[str], None] | None = None,
    on_assistant: Callable[[ChatResponse], None] | None = None,
    on_tool_call: Callable[[ToolCall, str, bool], None] | None = None,
    on_system: Callable[[str], None] | None = None,
) -> AgentResult:
    """Run inner-loop dispatch until the model stops calling tools.

    The session is mutated in place (we append assistant + tool messages
    as we go).

    Streaming
    ---------
    Pass ``stream=True`` to receive ``on_content_delta`` / ``on_reasoning_delta``
    callbacks as the model writes its response. ``on_assistant`` still fires
    once at the end of each iteration with the fully-assembled response.

    Seamless compaction
    -------------------
    At every step boundary (each loop iteration, before the request is
    built) a pressure check runs: when usage crosses the configured
    threshold, older history is summarized/pruned in place — long tool-heavy
    turns no longer grow past the window between REPL prompts. When a
    provider rejects a request for exceeding the context window, one
    compact-then-retry is attempted before the error surfaces.
    """
    entry = get_model_entry(model_name)
    provider = get_provider(entry)
    tools_schema = registry.openai_tools()

    executed: list[ToolCall] = []
    last_response: ChatResponse | None = None
    overflow_retried = False

    i = 0
    while True:
        # Pressure check at the step boundary — the harness's "after-call
        # pressure" position: the previous iteration's tool results are all
        # durable, so compacting here never splits a tool group. Never
        # raises; notices surface via on_system when wired.
        from .compact import is_context_overflow, maybe_auto_compact

        maybe_auto_compact(session, model_name=model_name, on_system=on_system)

        i += 1
        request = ChatRequest(
            model=entry.model,
            messages=list(session.messages),  # snapshot
            tools=tools_schema if tools_schema else None,
            tool_choice="auto" if tools_schema else None,
            temperature=(entry.extra or {}).get("temperature"),
            max_tokens=(entry.extra or {}).get("max_tokens"),
            thinking=thinking,
            thinking_budget=thinking_budget,
            # Stable per-prefix affinity key: survives /model switches
            # (dropped by adapters unless the model opts in via
            # extra.cache_key_field).
            cache_key=session_cache_key(session),
        )

        if on_assistant_start is not None:
            on_assistant_start()

        try:
            if stream and hasattr(provider, "stream_chat"):
                response = _stream_one(
                    provider,
                    request,
                    timeout=timeout,
                    on_content_delta=on_content_delta,
                    on_reasoning_delta=on_reasoning_delta,
                )
            else:
                response = provider.chat(request, timeout=timeout, verbose_label="agent")
        except ProviderError as e:
            # Overflow recovery: one compact-then-retry, bounded. If
            # compaction can't help (nothing compressible / still too big)
            # the retry fails again and the original error surfaces.
            if not overflow_retried and is_context_overflow(e):
                overflow_retried = True
                if on_system is not None:
                    on_system("[上下文超限 — 自动压缩后重试]")
                maybe_auto_compact(
                    session, model_name=model_name, on_system=on_system, force=True
                )
                i -= 1  # the retry is the same iteration
                continue
            return AgentResult(
                final_response=last_response,
                iterations=i - 1,
                tool_calls_executed=executed,
                stopped_reason="provider_error",
                error=e,
            )

        last_response = response
        session.add_assistant(response)
        if on_assistant is not None:
            on_assistant(response)

        calls = parse_tool_calls(response.tool_calls)
        if not calls:
            return AgentResult(
                final_response=response,
                iterations=i,
                tool_calls_executed=executed,
                stopped_reason="stop",
            )

        # If the user interrupts (Ctrl-C) while a tool is executing, the
        # assistant message with tool_calls is already in the session — every
        # provider requires a tool message per tool_call_id on the next request
        # (DeepSeek 400s otherwise). Backfill synthetic results for whatever
        # didn't finish before re-raising, so the history stays legal.
        answered: set[str] = set()
        try:
            for call in calls:
                result = registry.dispatch(call, ctx)
                session.add_tool_result(tool_call_id=call.id, content=result.content)
                # A tool may inject extra messages (e.g. view_image attaches the
                # image as a user message the vision model reads next turn).
                for extra in (getattr(result, "extra_messages", None) or []):
                    session.messages.append(extra)
                answered.add(call.id)
                executed.append(call)
                if on_tool_call is not None:
                    on_tool_call(call, result.content, result.is_error)
        except KeyboardInterrupt:
            for call in calls:
                if call.id not in answered:
                    session.add_tool_result(
                        tool_call_id=call.id,
                        content="[用户中断了本轮，此工具调用未执行]",
                    )
            raise
        # loop continues — model may want to call more tools or finalize
        if max_iters is not None and i >= max_iters:
            return AgentResult(
                final_response=last_response,
                iterations=i,
                tool_calls_executed=executed,
                stopped_reason="max_iters",
            )


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

InputFn = Callable[[], str]


def run_interactive(
    *,
    session: Session,
    ctx: AgentContext,
    registry: ToolRegistry,
    model_name: str,
    model_resolver: Callable[[], str] | None = None,
    read_input: InputFn,
    stream: bool = False,
    command_handler: Callable[[str], "Any"] | None = None,
    thinking_state: dict[str, Any] | None = None,
    on_assistant_start: Callable[[], None] | None = None,
    on_content_delta: Callable[[str], None] | None = None,
    on_reasoning_delta: Callable[[str], None] | None = None,
    on_assistant: Callable[[ChatResponse], None] | None = None,
    on_tool_call: Callable[[ToolCall, str, bool], None] | None = None,
    on_user_echo: Callable[[str], None] | None = None,
    on_provider_error: Callable[[ProviderError], None] | None = None,
    on_system: Callable[[str], None] | None = None,
    on_turn_done: Callable[[], None] | None = None,
    timeout: float = 120.0,
    max_iters_per_turn: int | None = None,
    pending_images: dict[str, Any] | None = None,
    pending_wake: Callable[[], bool] | None = None,
) -> Session:
    """Run a persistent REPL until ``read_input`` raises EOFError.

    Inside-each-turn behaviour reuses :func:`run_agent_turn`. The caller
    decides how to render each event (the CLI uses rich; tests use noop
    callbacks).

    Slash commands
    --------------
    If ``command_handler`` is provided, any input that starts with ``/``
    is dispatched to it (with full slash text) instead of being sent to
    the model. The handler may return either:

    * a :class:`agent.commands.CommandResult` — the loop honours its
      ``exit_repl`` / ``clear_history`` flags;
    * any other truthy value — the input is considered "handled" and the
      loop just continues to the next prompt.

    The loop still recognises ``/exit`` / ``/quit`` / ``/clear`` as
    built-ins when no handler is wired, so callers can drop the
    parameter and still get the v0.3 behaviour.

    Thinking
    --------
    ``thinking_state`` is a shared dict; the keys ``enabled`` and
    ``budget`` are read on every turn and forwarded to
    :func:`run_agent_turn` so a ``/think on`` mid-session takes effect on
    the *next* request.

    Background-task wake-ups
    ------------------------
    ``pending_wake``, when provided, drains background-task notifications:
    each call pops ONE notification (already appended to the session by the
    caller) and returns True. The loop then runs a continuation turn — the
    model "rested" and is woken by the finished task. Drains happen right
    after every turn and when the user submits an empty line at the prompt
    (so a bare Enter hands finished tasks to the model immediately).

    Special user inputs (without a command handler):

    * ``/exit`` / ``/quit`` — leave the loop.
    * ``/clear``           — drop history but keep the system prompt.
    * empty line           — wake pending background tasks, else re-prompt.
    """
    state = thinking_state if thinking_state is not None else {}

    def _run_model_turn() -> None:
        """One model turn (used by user inputs AND background wake-ups)."""
        _active_model = model_resolver() if model_resolver is not None else model_name
        try:
            result = run_agent_turn(
                session=session,
                ctx=ctx,
                registry=registry,
                model_name=_active_model,
                timeout=timeout,
                max_iters=max_iters_per_turn,
                stream=stream,
                thinking=state.get("enabled"),
                thinking_budget=state.get("budget"),
                on_assistant_start=on_assistant_start,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_assistant=on_assistant,
                on_tool_call=on_tool_call,
                on_system=on_system,
            )
        except KeyboardInterrupt:
            # Ctrl-C during a turn (e.g. a tool waiting on a slow page load)
            # aborts just this turn and returns to the prompt — the REPL stays
            # alive. The model may see a partial turn; the next input continues.
            if on_system is not None:
                on_system("[已中断本轮 — 回到输入。如需让 AI 继续，再说一句即可]")
            if on_turn_done is not None:
                on_turn_done()
            return

        if result.stopped_reason == "provider_error" and result.error is not None:
            if on_provider_error is not None:
                on_provider_error(result.error)
        elif result.stopped_reason == "max_iters" and on_system is not None:
            on_system(f"[到达单轮 tool_call 上限 ({max_iters_per_turn})；模型可能未完成任务]")

        if on_turn_done is not None:
            on_turn_done()

        # Auto context compression — give the next turn headroom when usage
        # crosses the configured threshold. Self-contained: reads config,
        # measures usage, summarises older history, and notifies via
        # on_system. Never raises (see maybe_auto_compact).
        from .compact import maybe_auto_compact

        maybe_auto_compact(
            session, model_name=_active_model, on_system=on_system
        )

        # Background tasks that finished during this turn wake the model
        # right away (each drain pops one notification).
        while pending_wake is not None and pending_wake():
            if on_system is not None:
                on_system("[后台任务完成 — AI 继续处理]")
            _run_model_turn()

    if on_system is not None:
        on_system(
            f"[REPL ready · model={model_name} · cwd={ctx.cwd} · "
            f"tools={', '.join(registry.names())} · /help · /exit]"
        )
    while True:
        try:
            text = read_input()
        except (EOFError, KeyboardInterrupt):
            if on_system is not None:
                on_system("[bye]")
            return session

        text = (text or "").strip()
        if not text:
            # Empty line: hand finished background tasks to the model, else
            # just re-prompt.
            if pending_wake is not None and pending_wake():
                if on_system is not None:
                    on_system("[后台任务完成 — AI 继续处理]")
                _run_model_turn()
            continue

        # --- slash command interception ---------------------------------
        if text.startswith("/"):
            if command_handler is not None:
                result = command_handler(text)
                # CommandResult-style: react to its flags.
                exit_repl = getattr(result, "exit_repl", False)
                clear_history = getattr(result, "clear_history", False)
                if clear_history:
                    session.messages = [
                        m for m in session.messages if m.role == "system"
                    ]
                if exit_repl:
                    return session
                continue

            # Legacy built-in fallback when no handler is wired.
            if text in {"/exit", "/quit"}:
                if on_system is not None:
                    on_system("[bye]")
                return session
            if text == "/clear":
                session.messages = [m for m in session.messages if m.role == "system"]
                if on_system is not None:
                    on_system("[history cleared]")
                continue
            if on_system is not None:
                on_system(f"[unknown command: {text}]")
            continue

        if on_user_echo is not None:
            on_user_echo(text)
        # Images staged by the REPL's mention handler (``@image`` / ``@paste``
        # / inline URL) ride inline in this user turn, then the buffer clears.
        imgs = (pending_images or {}).get("images") if pending_images else None
        session.add_user(text, images=imgs or None)
        if pending_images is not None:
            pending_images["images"] = []

        _run_model_turn()


def _stream_one(
    provider,
    request: ChatRequest,
    *,
    timeout: float,
    on_content_delta: Callable[[str], None] | None,
    on_reasoning_delta: Callable[[str], None] | None,
) -> ChatResponse:
    """Drive one ``provider.stream_chat()`` call and return the assembled response.

    The generator yields ``StreamEvent`` deltas; we forward ``content`` and
    ``reasoning`` to the UI callbacks as they arrive, and pick the final
    ``done`` event's ``response`` as the canonical one to store.
    """
    final: ChatResponse | None = None
    for ev in provider.stream_chat(request, timeout=timeout):
        if ev.kind == "content" and ev.text and on_content_delta is not None:
            on_content_delta(ev.text)
        elif ev.kind == "reasoning" and ev.text and on_reasoning_delta is not None:
            on_reasoning_delta(ev.text)
        elif ev.kind == "done" and ev.response is not None:
            final = ev.response
    if final is None:
        # Provider closed the stream without emitting "done" — synthesise a
        # minimal response so the caller doesn't crash.
        from ..schemas import ChatResponse as _CR
        final = _CR(content="", provider=getattr(provider, "name", None))
    return final


__all__ = ["AgentResult", "run_agent_turn", "run_interactive"]
