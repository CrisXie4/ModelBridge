"""Typer CLI entry point for ModelBridge.

Primary command: ``mbridge``. Aliases: ``modelbridge``.

Subcommands:

* ``mbridge init``                      — create ``~/.modelbridge/``
* ``mbridge model init|add``            — interactive model registration
* ``mbridge model list``                — rich table of registered models
* ``mbridge model test NAME``           — connectivity test (+ --verbose)
* ``mbridge model remove NAME``         — delete an entry
* ``mbridge ask "..."``                 — one-shot probe / pipeline use
                                          (+ --route / --auto / --mode / --fallback)
* ``mbridge doctor``                    — environment check
* ``mbridge doctor model NAME``         — single-model probe (+ --tools, --verbose)
* ``mbridge doctor all``                — bulk doctor over every model
* ``mbridge route "..."``               — show which level/model a prompt routes to (+ --mode)
* ``mbridge route test``                — run built-in 8-prompt suite
* ``mbridge cost estimate "..."``       — estimate per-model cost for a prompt
* ``mbridge budget show|set``           — REMOVED in 2026-07 (was: read/set monthly + daily spend budget)
* ``mbridge cache stats|reset|clean``   — prefix-cache statistics
* ``mbridge profile add|list|use|show|remove`` — named bundles of default_model + routing.levels
* ``mbridge config show|upgrade``       — view / re-emit ~/.modelbridge/config.yaml
* ``mbridge version [--check]``         — print version (optionally check for updates)
* ``mbridge update``                    — check for and download a newer release
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__, updater
from .cache import (
    extract_cache_tokens,
    load_cache_stats,
    record_hit,
    record_miss,
    record_prefix_observation,
    reset_cache_stats,
)
from .cli_compat import deprecated_alias
from .cli_console import console, err_console
from .client import ChatError, chat_once
from .config import (
    ConfigError,
    activate_profile,
    find_model,
    find_profile,
    init_app_dir,
    list_profiles,
    load_app_config,
    load_models_file,
    remove_model,
    remove_profile,
    save_app_config,
    upsert_model,
    upsert_profile,
)
from .cost import (
    CostEstimate,
    PricingNotFound,
    estimate_cost,
    estimate_tokens,
    get_pricing,
)
from .doctor import (
    next_steps_for_global,
    run_doctor_all,
    run_global_doctor,
    run_model_doctor,
)
from .models import (
    Capabilities,
    ModelEntry,
    ModelLevel,
    ProfileEntry,
    ProviderType,
    RoutingLevels,
    TransportType,
)
from .provider_profiles import PROFILES, get_profile
from .providers import ProviderError, get_provider
from .router import (
    LLMClassifyError,
    RouteResult,
    escalate_after_failure,
    route as route_prompt,
)
from .schemas import ChatRequest, text_of
from . import images
from .agent import (
    AgentContext,
    ApprovalDecision,
    PathPolicy,
    Session,
    run_interactive,
)
from .agent.commands import SlashContext, handle_slash
from .agent.tools import build_default_registry
from .skills.wiring import wire_skills
from .agent.ui import (
    AssistantStream,
    compute_turn_stats,
    render_reasoning_meter,
    render_tool_bubble,
    render_user_bubble,
    status_bar_text,
)
from .utils import (
    get_app_dir,
    get_config_path,
    get_logger,
    get_logs_dir,
    get_models_path,
    mask_secret,
)
from .prompt import (
    PREFIX_SECTIONS,
    PromptBuilder,
    PromptBuildResult,
    discover_rule_files,
)
from .project import (
    FileContext,
    ProjectSummary,
    SelectionResult,
    generate_agent_md,
    read_files,
    scan_project_cached,
    select_files,
    write_agent_md,
)
from .context import (
    DEFAULT_MAX_CONTEXT_CHARS,
    ContextPlan,
    plan as plan_context,
)
from .editor import (
    ApplyResult,
    DiffParseError,
    ExtractedDiff,
    ParsedDiff,
    SafetyVerdict,
    apply_diff,
    build_edit_messages,
    create_backup,
    extract_diff,
    guard_paths,
    guard_project_root,
    latest_backup,
    mark_deletions,
    parse_unified_diff,
    render_unified_diff,
    rollback as patch_rollback,
)
from .executor import (
    CommandPolicy,
    CommandRejected,
    ParsedError,
    parse_output,
    run_command,
)


app = typer.Typer(
    name="mbridge",
    help=(
        "ModelBridge — 国产模型优先的 AI Agent 兼容 CLI。\n\n"
        "直接运行 `mbridge` 进入持续会话 (AI 可读 / 写 / 编辑文件)。\n"
        "管理类操作走子命令；运行 `mbridge --help` 查看全部，`mbridge <命令> --help` 看子命令。\n\n"
        "起步：`mbridge init` → `mbridge model init` → `mbridge` (进入 REPL)。"
    ),
    epilog=(
        "💛 赞助 by 6哥API (https://6geapi.com) —— AI 大模型中转站，"
        "一个 Key 调用 GPT / Claude / Gemini / DeepSeek 等海内外模型，OpenAI 兼容。感谢支持！"
    ),
    invoke_without_command=True,
    add_completion=True,
)
model_app = typer.Typer(
    name="model",
    help="模型管理 (init / add / list / test / remove)。",
    no_args_is_help=True,
)
doctor_app = typer.Typer(
    name="doctor",
    help="环境与模型自检 (mbridge doctor / doctor model NAME / doctor all)。",
    invoke_without_command=True,
)

# ---------------------------------------------------------------------------
# usage group — cost (estimate) + cache (stats/reset/clean).
# (IA v1.2 cleanup: deprecated top-level `cost` / `cache` / `profile` / `chat`
# aliases are GONE — no hidden add_typer, no deprecated_alias wrappers. Use
# the canonical paths: `mbridge usage cost`, `mbridge usage cache stats`,
# `mbridge config profile ...`.)
# ---------------------------------------------------------------------------
usage_app = typer.Typer(
    name="usage",
    help="用量与成本查询：费用估算、缓存统计。",
    no_args_is_help=True,
)
_usage_cache_app = typer.Typer(
    name="cache",
    help="缓存统计 (mbridge usage cache / usage cache reset)。",
    no_args_is_help=True,
)
profile_app = typer.Typer(
    name="profile",
    help="配置切换 (add / list / use / show / remove)。一个 profile = 一组 default_model + 各 level 的模型映射。",
    no_args_is_help=True,
)
app.add_typer(model_app, name="model")
app.add_typer(doctor_app, name="doctor")
app.add_typer(usage_app, name="usage")
usage_app.add_typer(_usage_cache_app, name="cache")

prompt_app = typer.Typer(
    name="prompt",
    help="提示词与规则文件管理 (list / show / edit / set-system / reset)。",
    no_args_is_help=True,
)
project_app = typer.Typer(
    name="project",
    help="项目扫描与 AGENT.md 生成 (scan / rules / rules init)。",
    no_args_is_help=True,
)
project_rules_app = typer.Typer(
    name="rules",
    help="规则文件查看与生成 (rules / rules init)。",
    invoke_without_command=True,
)
project_app.add_typer(project_rules_app, name="rules")
app.add_typer(prompt_app, name="prompt")
app.add_typer(project_app, name="project")

patch_app = typer.Typer(
    name="patch",
    help="Patch 预览 / 应用 / 回滚 (preview / apply / rollback)。",
    no_args_is_help=True,
)
app.add_typer(patch_app, name="patch", hidden=True)

# MCP client subcommands live in their own module to avoid an import cycle.
from .mcp.cli import mcp_app  # noqa: E402

app.add_typer(mcp_app, name="mcp")

# Browser side-panel Native Messaging host subcommands.
from .bridge.cli import bridge_app  # noqa: E402

app.add_typer(bridge_app, name="bridge")

# Skill management subcommands.
from .skills.cli import skill_app  # noqa: E402

app.add_typer(skill_app, name="skill")


# ---------------------------------------------------------------------------
# Root: `mbridge` with no subcommand → interactive agent REPL
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"ModelBridge (mbridge) v{__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="显示版本号并退出。",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="模型名 (默认 config.yaml 中的 default_model)。",
    ),
    cwd: Optional[Path] = typer.Option(
        None, "--cwd", help="agent 的工作目录 (默认当前目录)。所有 read/write 都受此限制。",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="自动同意全部 write/edit/bash 操作。"),
    allow_bash: bool = typer.Option(
        False, "--allow-bash",
        help="启用 run_bash 工具。默认关闭。即使启用，每条命令仍会请求确认 (除非加 --yes)。",
    ),
    max_iters: int = typer.Option(
        20, "--max-iters",
        help="单轮 user 输入内 tool_call 的最多次数 (避免无限循环)。",
    ),
    save_session: bool = typer.Option(
        True, "--save/--no-save",
        help="是否把会话写到 ~/.modelbridge/sessions/。",
    ),
    system: Optional[str] = typer.Option(
        None, "--system", "-s", help="可选 system prompt。",
    ),
    timeout: float = typer.Option(120.0, "--timeout", help="每次模型请求的超时秒数。"),
) -> None:
    """无子命令时进入持续会话 (Claude-Code 风格)。"""
    if ctx.invoked_subcommand is not None:
        # A subcommand will run (init / model / doctor / ...). Don't start REPL.
        return
    _run_repl(
        model=model,
        cwd=cwd,
        yes=yes,
        allow_bash=allow_bash,
        max_iters=max_iters,
        save_session=save_session,
        system=system,
        timeout=timeout,
    )


def _run_repl(
    *,
    model: Optional[str],
    cwd: Optional[Path],
    yes: bool,
    allow_bash: bool,
    max_iters: int,
    save_session: bool,
    system: Optional[str],
    timeout: float,
) -> None:
    # 1. Resolve model
    try:
        cfg = load_app_config()
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    # Mutable holder so ``/model`` can swap the active model mid-session.
    # ``model_name`` is kept as a local alias for the initial value, but
    # any code that needs the *current* name should call ``_active_model()``
    # or read ``model_holder[0]``.
    model_holder: list[str] = [model or cfg.default_model or ""]
    model_name = model_holder[0]

    def _active_model() -> str:
        return model_holder[0]

    def _set_active_model(new_name: str) -> None:
        """Switch the active model; re-sync thinking_state to its profile.

        Raises ``ValueError`` (caught by /model handler) if the model is
        not registered. We do NOT clear session history — multi-turn
        reasoning invariants for Kimi-thinking / MiMo / DeepSeek-reasoner
        require the prior turns to stay intact.
        """
        from .agent.thinking import profile_for
        new_entry = find_model(new_name)
        if new_entry is None:
            mf = load_models_file()
            names = [m.name for m in mf.models]
            hint = f" 可用: {', '.join(names)}" if names else " (models.yaml 是空的)"
            raise ValueError(f"模型 {new_name!r} 不在 models.yaml。{hint}")
        model_holder[0] = new_name
        # Re-sync thinking to the new model's profile. If it doesn't have
        # one, disable thinking (the next /think on will warn accordingly).
        profile = profile_for(new_name)
        if profile is not None:
            thinking_state["enabled"] = True
            thinking_state["level"] = profile.default_level
            thinking_state["budget"] = profile.budget_for_level(profile.default_level)
        else:
            thinking_state["enabled"] = False
            thinking_state["level"] = None
            thinking_state["budget"] = None

    # Fallback: default_model not set / not in models.yaml. If exactly one
    # model is configured, just use it — and persist that choice so the
    # next `mbridge` run is silent.
    if not model_holder[0] or find_model(model_holder[0]) is None:
        try:
            mf = load_models_file()
        except ConfigError as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from e

        if len(mf.models) == 0:
            err_console.print(
                "[red]models.yaml 还没有任何模型。[/red]\n"
                "运行 `mbridge model init` 添加一个 (会自动设为 default_model)。"
            )
            raise typer.Exit(code=2)
        if len(mf.models) == 1 and not model:
            sole = mf.models[0].name
            note = (
                f"找不到 default_model {model_name!r}，"
                if model_name and find_model(model_name) is None
                else "未设置 default_model，"
            )
            console.print(f"[dim]{note}已自动使用唯一可用模型 [bold]{sole}[/bold]。[/dim]")
            model_holder[0] = sole
            # Persist so next time it's silent.
            try:
                cfg.default_model = sole
                save_app_config(cfg)
            except (ConfigError, OSError):
                pass
        else:
            names = ", ".join(m.name for m in mf.models)
            err_console.print(
                f"[red]找不到模型 {_active_model()!r}。[/red]  "
                f"可用模型: {names}\n"
                f"试试 `mbridge -m <name>`，或编辑 ~/.modelbridge/config.yaml 把 "
                "default_model 改成上面其中一个。"
            )
            raise typer.Exit(code=2)

    # 2. Build path policy + context
    cwd_resolved = (cwd or Path.cwd()).resolve()
    try:
        policy = PathPolicy.from_config(extra_cwd=cwd_resolved)
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    approval = _make_approval(yes=yes)
    _repl_entry = find_model(model_name) if model_name else None
    _model_is_vision = bool(getattr(getattr(_repl_entry, "capabilities", None), "vision", False))
    agent_ctx = AgentContext(
        policy=policy, cwd=cwd_resolved, approve=approval, allow_bash=allow_bash,
        model_is_vision=_model_is_vision,
    )
    registry = build_default_registry(include_bash=allow_bash)

    # Browser tools are always available: the agent can read/operate the active
    # web page by relaying to the side-panel extension via the LocalBridge host.
    # When the linkage isn't ready (control off / panel closed) the tools simply
    # return a friendly "not connected" error if the model tries to use them —
    # nothing else is affected. Write tools confirm via the same terminal
    # approval as file writes (ctx.confirm).
    from .agent.tools.browser_tools import build_browser_registry
    from .bridge.control import RemoteBrowserBridge

    for tool in build_browser_registry(include_write=True).tools.values():
        registry.register(tool)

    # Sub-agent tool — lets the AI spin up focused sub-agents for complex tasks.
    # User confirmation is required (y / N / a=always); "always" is persisted.
    from .agent.tools.subagent_tool import SpawnSubagentTool
    registry.register(SpawnSubagentTool())

    # Computer control tools: mouse, keyboard, screenshot, and inject_js.
    from .agent.tools.computer_control_tools import build_computer_registry
    for tool in build_computer_registry().tools.values():
        registry.register(tool)

    browser_bridge = RemoteBrowserBridge()
    agent_ctx.browser_bridge = browser_bridge
    ok, reason = browser_bridge.available()
    if ok:
        console.print("[dim]网页控制: 已连接侧边栏 (read_page / click / fill / navigate…)[/dim]")
    else:
        console.print(
            f"[dim]网页控制: 未连接 ({reason})。"
            f"开启: `mbridge bridge on` + 打开侧边栏。[/dim]"
        )

    # 2b. MCP — connect configured servers and fold their tools into the same
    # registry. Failure to connect a server is isolated and never blocks the
    # REPL; the manager is torn down in the `finally` below.
    mcp_manager = None
    try:
        from .mcp import MCPManager, is_enabled, register_mcp_tools

        if is_enabled():
            mcp_manager = MCPManager.from_config(verbose=False)
            mcp_manager.connect_all()
            n = register_mcp_tools(registry, mcp_manager)
            mcp_manager.start_heartbeat()  # no-op unless mcp.heartbeat_interval > 0
            failed = mcp_manager.connect_errors
            if n:
                console.print(f"[dim]MCP: 接入 {n} 个工具"
                              f"{f'，{len(failed)} 个 server 连接失败' if failed else ''}"
                              f" · /mcp 管理[/dim]")
            for sid, err in failed.items():
                err_console.print(f"[yellow]MCP server {sid} 连接失败: {err.message}[/yellow]")
    except Exception as e:  # noqa: BLE001 — MCP must never block the REPL
        err_console.print(f"[yellow]MCP 初始化跳过: {e}[/yellow]")
        mcp_manager = None

    # 3. Session + system prompt
    #
    # We build the system message via PromptBuilder so the REPL's prefix
    # matches what ``mbridge ask`` / ``mbridge prompt hash`` produce —
    # rules.md, AGENT.md, and the project summary all land in the same
    # stable 8-section order. ``cwd`` is intentionally NOT included in
    # the message (it varies across machines and would kill prefix-cache
    # hits); it lives in ``session.metadata`` for diagnostics only.
    session = Session(model_name=model_name)

    sys_prompt_text = system or _default_system_prompt(allow_bash=allow_bash)
    try:
        sys_prompt_text = wire_skills(registry, sys_prompt_text, project_path=cwd_resolved)
    except Exception as e:  # noqa: BLE001 — skills must never block the REPL
        err_console.print(f"[yellow]跳过 skills 加载: {e}[/yellow]")
    prompt_builder = PromptBuilder().with_system_prompt(sys_prompt_text).with_project(cwd_resolved)

    repl_prefix_hash = ""
    repl_rules_hash = ""
    repl_summary_hash = ""
    repl_summary_reason = ""
    try:
        summary, cache_check = scan_project_cached(cwd_resolved)
        prompt_builder = prompt_builder.with_project_summary(
            summary.to_markdown(),
            file_tree_hash=summary.file_tree_hash,
        )
        repl_summary_reason = cache_check.reason
    except Exception:  # noqa: BLE001 — scan should never block the REPL
        pass

    initial = prompt_builder.build()
    if initial.messages and initial.messages[0].role == "system":
        session.add_system(text_of(initial.messages[0].content) or sys_prompt_text)
    else:
        session.add_system(sys_prompt_text)

    repl_prefix_hash = initial.prompt_prefix_hash
    repl_rules_hash = initial.rules_hash
    repl_summary_hash = initial.project_summary_hash

    # Tell the cache layer about the prefix we're committing to for this
    # REPL session. Each new mbridge invocation counts as one observation;
    # drift here means rules.md / system.md / project changed between
    # sessions (NOT within a session — within a session the prefix is
    # held in session.messages[0] and never rebuilt).
    record_prefix_observation(
        prefix_hash=repl_prefix_hash,
        section_hashes=initial.section_hashes,
    )

    session.metadata = {
        "cwd": str(cwd_resolved),
        "allow_bash": allow_bash,
        "yes": yes,
        "tools": registry.names(),
        "policy": policy.describe(),
        "prompt_prefix_hash": repl_prefix_hash,
        "rules_hash": repl_rules_hash,
        "project_summary_hash": repl_summary_hash,
        "summary_cache": repl_summary_reason,
    }

    # 4. Banner
    cache_line = f"summary   : {repl_summary_reason or '(none)'}"
    console.print(
        Panel.fit(
            f"[bold]ModelBridge agent REPL[/bold]  [dim]v{__version__}[/dim]\n"
            f"model     : {model_name}\n"
            f"cwd       : {cwd_resolved}\n"
            f"tools     : {', '.join(registry.names())}\n"
            f"approval  : {'自动同意 (--yes)' if yes else '每次询问'}\n"
            f"allow_bash: {allow_bash}\n"
            f"policy    : {policy.describe()}\n"
            f"prefix    : {repl_prefix_hash or '(empty)'}\n"
            f"{cache_line}\n\n"
            f"[yellow]💛 感谢 [bold]6哥API[/bold] 赞助[/yellow]  [cyan]https://6geapi.com[/cyan]\n"
            f"[dim]   AI 大模型中转站 · 一个 Key 调用 GPT / Claude / Gemini / DeepSeek 等海内外模型 · OpenAI 兼容[/dim]\n\n"
            f"[dim]/help 命令 · @文件名 引用文件(实时补全, 内容注入本轮) · "
            f"/exit 退出 · Ctrl-D[/dim]",
            title="mbridge",
            border_style="cyan",
        )
    )

    # 4b. Update check (cached, non-blocking, best-effort). If a newer
    # version exists we show a one-line notice; the user can type 同意
    # at the prompt (or run /update) to download it.
    update_state: dict[str, Any] = {"release": None}
    try:
        rel = updater.check_for_update()
    except Exception:  # noqa: BLE001 — never let an update check block the REPL
        rel = None
    if rel is not None:
        update_state["release"] = rel
        console.print(
            f"[yellow]🔔 发现新版本 [bold]v{rel.version}[/bold]"
            f"（当前 v{__version__}）。输入 [bold]同意[/bold] 下载更新，"
            f"或用 [bold]/update[/bold]。[/yellow]"
        )

    _AGREE_WORDS = {"同意", "更新", "升级", "update", "upgrade", "yes", "y"}

    # --- @file 提及：惰性文件索引 + prompt_toolkit 实时补全 -------------
    #
    # 索引在首次需要时才构建（扫描一次，整段 REPL 复用），构建失败绝不阻断
    # REPL：补全静默关闭、@提及按普通文字处理。
    _index_state: dict[str, Any] = {"index": None, "built": False}

    def _get_file_index():
        if not _index_state["built"]:
            _index_state["built"] = True
            try:
                from .project.file_index import FileIndex
                _index_state["index"] = FileIndex.build(cwd_resolved)
            except Exception:  # noqa: BLE001 — 索引失败绝不阻断 REPL
                _index_state["index"] = None
        return _index_state["index"]

    # 只在交互式 TTY 且 prompt_toolkit 可用时启用实时下拉补全；否则回退到
    # console.input（提交后再解析 @提及），保证管道 / 哑终端仍可用。
    _pt_session: Any = None
    try:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import ThreadedCompleter
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings

            from .agent.at_completer import AtFileCompleter

            # Ctrl+O toggles thinking display mode (full ↔ collapse).
            # We capture the binding on thinking_state which is captured
            # by reference; toggle is visible to the next AssistantStream
            # created by on_assistant_start.
            _pt_bindings = KeyBindings()

            @_pt_bindings.add("c-o")
            def _toggle_thinking_display(event) -> None:
                thinking_state["show_full"] = not thinking_state.get("show_full", False)
                mode = "[green]▣ 全显[/green]" if thinking_state["show_full"] else "[dim]▢ 折叠[/dim]"
                # Print the new state on its own line so the next prompt
                # redraw leaves a visible breadcrumb in the scrollback.
                console.print(f"  thinking display: {mode}  [dim](Ctrl+O 切换)[/dim]")

            # ThreadedCompleter runs the (lazy) index build + per-keystroke
            # scan off the UI thread, so the first '@' (full os.walk) and broad
            # queries on a big repo don't freeze the prompt.
            _pt_session = PromptSession(
                completer=ThreadedCompleter(AtFileCompleter(_get_file_index)),
                complete_while_typing=True,
                history=InMemoryHistory(),
                key_bindings=_pt_bindings,
            )
    except Exception:  # noqa: BLE001 — prompt_toolkit 不可用就回退
        _pt_session = None

    def _read_raw() -> str:
        nonlocal _pt_session
        if _pt_session is not None:
            from prompt_toolkit.formatted_text import HTML
            # prompt_toolkit 自行管理光标 / 重绘，无需下面 console.input 那套
            # cp936 光标修正；EOFError / KeyboardInterrupt 向上抛给 loop。
            try:
                return _pt_session.prompt(HTML("<ansigreen><b>you ❯</b></ansigreen> "))
            except (EOFError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001 — 伪 TTY(Git Bash/MSYS) / console
                # 运行时才暴露的 console 错误：永久禁用补全，落到 console.input。
                _pt_session = None
                console.print(
                    f"[dim]· 实时补全不可用，已回退普通输入 ({type(e).__name__})[/dim]"
                )
        # console.input 回退路径 —— 先把光标顶到行首：
        #
        # 上一轮结尾 ``on_turn_done`` 用 ``status_bar_text`` 打状态栏
        # (no_wrap=True + overflow="ellipsis")。Windows Terminal cp936 下截断
        # 可能把光标留在行中，使 ``console.input`` 的提示叠到状态栏上、与回显
        # 串行。硬 CR+LF 保证从第 1 列新起一行。
        try:
            console.file.write("\r\n")
            console.file.flush()
        except Exception:  # noqa: BLE001 — never let UI hygiene crash input
            pass
        return console.input("[bold green]you ❯[/bold green] ")

    # Images staged by @image / @paste / inline-URL for the *next* user turn;
    # consumed (and cleared) by run_interactive when it builds the message.
    pending_turn: dict[str, Any] = {"images": []}

    def _apply_mentions(text: str) -> bool:
        """注入文本附件 + 收集图像块到 pending_turn；带图但模型非 vision 时拦下本轮。

        返回 True=可继续发送；False=被 vision 门禁拦下（read_input 改返回空串跳过）。
        """
        from .agent.mentions import (
            collect_image_blocks,
            inject_file_mentions,
            resolve_mentions,
        )
        from .project.file_index import FileIndex

        index = _get_file_index()
        if index is not None:
            resolved = inject_file_mentions(text, index, session, project_root=cwd_resolved)
        else:
            # 无项目索引：@paste / 内联图片 URL 仍可用（不依赖文件索引）。
            resolved = resolve_mentions(
                text, FileIndex(root=cwd_resolved, entries=[]), project_root=cwd_resolved
            )

        text_atts = [a for a in resolved.attachments if a.kind != "image"]
        if text_atts:
            names = "、".join(
                a.relpath + ("/" if a.kind == "dir" else "") for a in text_atts
            )
            console.print(
                f"[dim]📎 已把 {len(text_atts)} 项作为上下文附加: {names}[/dim]"
            )
        if resolved.unresolved:
            miss = "、".join(resolved.unresolved)
            console.print(f"[dim]· 未匹配的 @提及（按普通文字处理）: {miss}[/dim]")

        img_blocks = collect_image_blocks(resolved)
        if img_blocks:
            entry = find_model(model_name)
            try:
                images.ensure_vision(
                    has_images=True,
                    model_is_vision=bool(getattr(entry.capabilities, "vision", False)) if entry else False,
                    model_name=_active_model() or "(未指定)",
                    available_vision=_vision_model_names(),
                )
            except images.ImageError as e:
                console.print(f"[red]{e}[/red]")
                pending_turn["images"] = []
                return False
            pending_turn["images"] = img_blocks
            console.print(f"[dim]🖼 已内联 {len(img_blocks)} 张图片到本轮消息[/dim]")
        return True

    def read_input() -> str:
        try:
            text = _read_raw()
        except UnicodeDecodeError:
            return ""
        # If an update is pending, a bare 同意 / yes triggers the download
        # instead of being sent to the model. Any other input clears the
        # pending state so the prompt doesn't keep hijacking 同意 forever.
        if update_state["release"] is not None and text.strip().lower() in _AGREE_WORDS:
            rel = update_state["release"]
            update_state["release"] = None
            _run_update_flow(rel)
            return ""  # re-prompt
        # 兑现上面注释：任何其它非空输入都清除待更新态，避免很久以后用户
        # 真心说一句"同意/yes"被误当成更新确认而触发下载。
        if update_state["release"] is not None and text.strip():
            update_state["release"] = None
        # @file 提及：把被提及文件的内容作为本轮上下文注入会话（在 loop 追加
        # 用户原话之前），@路径 本身仍原样保留在用户可见消息里。斜杠命令跳过。
        stripped = text.strip()
        if stripped and not stripped.startswith("/"):
            try:
                if not _apply_mentions(text):
                    return ""  # vision 门禁拦下本轮：跳过这次输入
            except Exception:  # noqa: BLE001 — 提及解析绝不阻断输入
                pass
        return text

    # State carried across callbacks within a single REPL turn.
    turn_state: dict[str, Any] = {
        "stream": None,           # AssistantStream | None
        "saw_reasoning": False,
        "iterations": 0,
        "last_response": None,
    }

    def on_user_echo(text: str) -> None:
        # Render the user input as a right-aligned green bubble so the
        # transcript looks like a chat.
        render_user_bubble(console, text)

    def on_assistant_start() -> None:
        # Open a new live left-side bubble for this iteration's assistant turn.
        if turn_state["stream"] is not None:
            try:
                turn_state["stream"].__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        s = AssistantStream(
            console,
            model_name=_active_model(),
            show_full=bool(thinking_state.get("show_full", False)),
            collapse_threshold=int(thinking_state.get("collapse_threshold", 800)),
        )
        s.__enter__()
        turn_state["stream"] = s
        turn_state["saw_reasoning"] = False

    def on_content_delta(text: str) -> None:
        s = turn_state["stream"]
        if s is not None:
            s.append_content(text)

    def on_reasoning_delta(text: str) -> None:
        s = turn_state["stream"]
        if s is not None:
            s.append_reasoning(text)
            turn_state["saw_reasoning"] = True

    def on_assistant(resp) -> None:
        # Close the live bubble (it prints its final static panel).
        s = turn_state["stream"]
        if s is not None:
            try:
                s.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            turn_state["stream"] = None
        turn_state["iterations"] += 1
        turn_state["last_response"] = resp
        # Reasoning content meter (token count for what we just received).
        if resp.reasoning_content:
            render_reasoning_meter(console, reasoning_text=resp.reasoning_content)
        # Provider-reported cache hit/miss → ``mbridge cache stats``.
        entry = find_model(model_name)
        if entry is not None:
            try:
                _record_cache_outcome(entry, resp)
            except Exception:  # noqa: BLE001 — stats never block the REPL
                pass

    def on_tool_call(call, result_content: str) -> None:
        # The agent's own write tools mutate the tree mid-session; drop the
        # cached file index so a newly created/renamed file is @-mentionable
        # on the next prompt instead of silently failing to resolve.
        if call.name in {"write_file", "str_replace"}:
            _index_state["built"] = False
        args_preview = ", ".join(
            f"{k}={_short_repr(v)}" for k, v in call.arguments.items() if not k.startswith("_")
        )
        render_tool_bubble(
            console,
            tool_name=call.name,
            args_preview=args_preview,
            body=_fold_tool_body(result_content),
        )

    def on_provider_error(err) -> None:
        # Make sure any live bubble is closed first so the error panel doesn't fight with it.
        s = turn_state["stream"]
        if s is not None:
            try:
                s.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            turn_state["stream"] = None
        _print_provider_error(err)

    def on_system(text: str) -> None:
        console.print(f"[dim]{text}[/dim]")

    # Status line is printed inline at the end of each turn (right above the
    # next `you ❯` prompt). We tried the DECSTBM sticky-footer approach but
    # it conflicted with the streaming bubble + console.input cursor on
    # Windows Terminal — the saved/restored cursor positions drifted as the
    # scroll region scrolled, causing the status text to land on the input
    # line and panel borders to collide with the assistant header. Inline
    # print is cross-platform-stable and visually "right above the prompt"
    # which is functionally what users want.
    def _print_status_bar() -> None:
        entry = find_model(model_name)
        if entry is None:
            return
        stats = compute_turn_stats(
            entry=entry,
            messages=session.messages,
            last_response=turn_state["last_response"],
            iterations=turn_state["iterations"] or 1,
        )
        # Status bar comes from ``status_bar_text`` which sets ``no_wrap=True``
        # + ``overflow="ellipsis"`` (needed by StickyFooter). For inline
        # printing those attrs are actively harmful — when the bar exceeds
        # the terminal width on a narrow window, the ellipsis path can
        # leave the cursor mid-line and break the next ``you ❯`` prompt.
        # Flip both to wrap-friendly here so the bar lays out naturally.
        bar = status_bar_text(stats, model_name=_active_model())
        bar.no_wrap = False
        bar.overflow = "fold"
        console.print(bar)

    def on_turn_done() -> None:
        _print_status_bar()
        turn_state["iterations"] = 0
        turn_state["last_response"] = None

    # Mutable state shared with the slash-command dispatcher.
    # Initialize from the active model's thinking profile so /think
    # has a sensible default on the first turn (no manual `on` needed).
    thinking_state: dict[str, Any] = {
        "enabled": False,
        "level": None,
        "budget": None,
        "collapse_threshold": 800,
        "show_full": False,
    }
    try:
        from .agent.thinking import profile_for
        init_entry = find_model(model_name)
        if init_entry is not None:
            init_profile = profile_for(init_entry.name)
            if init_profile is not None:
                thinking_state["enabled"] = True
                thinking_state["level"] = init_profile.default_level
                thinking_state["budget"] = init_profile.budget_for_level(
                    init_profile.default_level
                )
    except Exception:  # noqa: BLE001 — never let init crash the REPL
        pass

    def _command_handler(text: str):
        # Rebuild SlashContext on every call so `/think on` mutations
        # land in the same `thinking_state` dict the loop reads next turn,
        # and so a `/model` swap mid-session is visible to subsequent
        # commands. ``_active_model()`` reads the live holder, not the
        # initial ``model_name`` alias.
        active = _active_model()
        entry = find_model(active)
        sctx = SlashContext(
            console=console,
            session=session,
            agent_ctx=agent_ctx,
            registry=registry,
            model_name=active,
            entry=entry,
            thinking_state=thinking_state,
            project_path=cwd_resolved,
            mcp_manager=mcp_manager,
            on_model_change=_set_active_model,
        )
        return handle_slash(text, sctx)

    # 5. Run
    try:
        run_interactive(
            session=session,
            ctx=agent_ctx,
            registry=registry,
            model_name=_active_model(),
            model_resolver=_active_model,  # live read each turn for /model
            read_input=read_input,
            stream=True,
            command_handler=_command_handler,
            thinking_state=thinking_state,
            on_assistant_start=on_assistant_start,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            on_assistant=on_assistant,
            on_tool_call=on_tool_call,
            on_user_echo=on_user_echo,
            on_provider_error=on_provider_error,
            on_system=on_system,
            on_turn_done=on_turn_done,
            timeout=timeout,
            max_iters_per_turn=max_iters,
            pending_images=pending_turn,
        )
    finally:
        if mcp_manager is not None:
            mcp_manager.shutdown()
        if browser_bridge is not None:
            browser_bridge.close()
        if save_session and len(session.messages) > 1:
            path = session.save(label=f"repl_{model_name}")
            if path is not None:
                console.print(f"[dim]session saved → {path}[/dim]")


def _make_approval(*, yes: bool):
    """Return an approval callback used by tools.

    ``--yes`` short-circuits to YES; otherwise we render a small panel and
    use rich's prompt with y / N / a (always) / o (auto) options.

    When ``save_pattern`` is passed (non-None), the user picked "always" and
    the callback persists the pattern to disk for permanent approval.

    When ``auto=True`` is passed, a tiny LLM judges safety first — if safe
    the action proceeds automatically, otherwise it falls through to the
    manual prompt.
    """
    import json as _json

    _APPROVED_PATH = get_app_dir() / "approved_patterns.json"

    def _load_approved() -> dict:
        if _APPROVED_PATH.exists():
            try:
                return _json.loads(_APPROVED_PATH.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_pattern_to_disk(pattern: str, label: str) -> None:
        try:
            patterns = _load_approved()
            patterns[pattern] = label
            _APPROVED_PATH.parent.mkdir(parents=True, exist_ok=True)
            _APPROVED_PATH.write_text(
                _json.dumps(patterns, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            console.print(f"[dim]已永久批准此模式（保存至 {_APPROVED_PATH.name}）。"
                          " 以后同类请求将自动通过。[/dim]")
        except Exception as e:
            console.print(f"[dim]永久批准保存失败: {e}[/dim]")

    def _auto_judge(*, tool: str, summary: str, detail: str) -> tuple[bool, str]:
        """Call a tiny model to judge if this action is safe to auto-approve.

        Returns (is_safe, reason).
        """
        from modelbridge.providers import get_provider
        from modelbridge.config import load_app_config, load_models_file
        from modelbridge.client import find_model

        prompt = (
            f"判断以下操作是否安全。分析后先给出理由，再给出结论「安全」或「不安全」。\n"
            f"工具: {tool}\n操作: {summary}\n详情: {detail[:300]}"
        )
        try:
            cfg = load_app_config()
            # Try to find a tiny/cheap model for fast safety judgement
            models_file = load_models_file()
            tiny_model = None
            for m in models_file.models:
                if getattr(m, "level", None) in ("tiny", "cheap") or "tiny" in m.name.lower():
                    tiny_model = m
                    break
            if tiny_model is None and cfg.default_model:
                tiny_model = find_model(cfg.default_model)

            if tiny_model is None:
                return False, "(无法找到可用模型)"

            entry = find_model(tiny_model.name)
            if entry is None:
                return False, "(无法解析模型配置)"

            provider = get_provider(entry)
            from modelbridge.schemas import ChatMessage, ChatRequest
            resp = provider.chat(ChatRequest(model=entry.model, messages=[
                ChatMessage(role="user", content=prompt)
            ]), timeout=15.0)
            content = resp.content or ""
            is_safe = "安全" in content and "不安全" not in content
            # Fold long reason for terminal display
            reason = content.strip() if len(content) <= 200 else (
                content.strip()[:200] + "…"
            )
            return is_safe, reason
        except Exception as e:
            return False, f"(AI 判断失败: {e})"

    if yes:
        def _yes(*, tool: str, summary: str, detail: str = "",  # noqa: ARG001
                  save_pattern: str | None = None, auto: bool = False):
            return ApprovalDecision.YES
        return _yes

    def _ask(*, tool: str, summary: str, detail: str = "",
             save_pattern: str | None = None, auto: bool = False):
        # ── Auto-judge phase ───────────────────────────────────────────────
        if auto:
            console.print("[dim]AI safety check...[/dim]")
            is_safe, reason = _auto_judge(tool=tool, summary=summary, detail=detail)
            console.print(f"[dim]  reason: {reason}[/dim]")
            if is_safe:
                console.print("[green]  -> safe, auto-approved[/green]")
                return ApprovalDecision.YES
            console.print("[yellow]  -> not safe, requires manual confirm[/yellow]")
            # falls through to human prompt

        console.print(Panel(
            f"[bold]{summary}[/bold]\n\n{detail}",
            title=f"批准 · {tool}",
            border_style="yellow",
        ))
        choices = ["y", "n", "a", "o"] if auto else ["y", "n", "a"]
        prompt_str = (
            r"执行?  \[y]es / \[N]o / \[a]lways / \[o]auto(AI判断)"
            if auto else r"执行?  \[y]es / \[N]o / \[a]lways"
        )
        choice = Prompt.ask(
            prompt_str,
            choices=choices,
            default="n",
            show_choices=False,
        ).lower()
        if choice == "y":
            return ApprovalDecision.YES
        if choice == "a":
            if save_pattern:
                _save_pattern_to_disk(save_pattern, summary)
            return ApprovalDecision.ALWAYS
        if choice == "o" and auto:
            # user chose to always auto-judge for this class of actions
            if save_pattern:
                _save_pattern_to_disk(save_pattern + ":auto", f"{summary} [auto]")
            return ApprovalDecision.ALWAYS
        return ApprovalDecision.NO

    return _ask


def _short_repr(v) -> str:
    s = repr(v) if not isinstance(v, str) else v
    if len(s) > 60:
        s = s[:60] + "…"
    return s


def _fold_tool_body(content: str, *, max_lines: int = 6, max_chars: int = 400) -> str:
    """Fold a long tool result for terminal display.

    The model still receives the full content (it lives in the session); this
    only keeps the REPL readable when a tool like ``read_page`` returns a whole
    page of text.
    """
    content = content or ""
    lines = content.splitlines()
    head_lines = lines[:max_lines]
    head = "\n".join(head_lines)
    truncated_chars = len(head) > max_chars
    if truncated_chars:
        head = head[:max_chars].rstrip() + "…"
    hidden = len(lines) - len(head_lines)
    if hidden > 0 or truncated_chars:
        note_bits = []
        if hidden > 0:
            note_bits.append(f"折叠 {hidden} 行")
        note_bits.append(f"共 {len(content)} 字符，模型已获取完整内容")
        head += f"\n[{' · '.join(note_bits)}]"
    return head


def _default_system_prompt(*, allow_bash: bool) -> str:
    """Built-in system prompt used by the REPL when no override is supplied.

    IMPORTANT: this text is part of the **stable prompt prefix** — it must
    never include the working directory, machine path, hostname, timestamp,
    or any other value that varies across sessions. Such content would
    invalidate the provider prefix-cache for every call. ``cwd`` is exposed
    to tools via :class:`AgentContext` and lives in ``session.metadata`` for
    debugging, not in the messages.
    """
    bash_line = "" if not allow_bash else (
        "- run_bash(command): 在 cwd 中执行 shell 命令。默认 30 秒超时。\n"
    )
    browser_block = (
        "\n你还能操作用户当前浏览器标签页 (通过侧边栏插件):\n"
        "- read_page(): 读当前网页标题/URL/正文 (总结、问答前先调用)。\n"
        "- get_selection(): 读用户选中的文本。\n"
        "- query_dom(selector) / extract(selector, attr): 用 CSS 选择器定位元素、取文本或属性。\n"
        "- click(selector) / fill(selector, value) / navigate(url): 操作页面 (会请求用户确认)。\n"
        "需要网页信息或要操作网页时主动调用这些工具，不要凭空猜测页面内容。\n"
        "若工具返回「未启用 / 未连接」，告诉用户运行 `mbridge bridge on` 并打开浏览器侧边栏。\n"
    )
    return (
        "你是 ModelBridge 嵌入的编程助手 (类似 Claude Code)。"
        "你可以读、写、编辑项目文件，必要时也可以调用 shell。\n\n"
        "可用工具:\n"
        "- read_file(path): 读取项目内文件 (path 相对于工作目录)。\n"
        "- list_dir(path): 列出目录条目。\n"
        "- write_file(path, content): 覆盖/创建文件 (每次会请求用户确认)。\n"
        "- str_replace(path, old_str, new_str): 精确替换 (要求 old_str 在文件中唯一出现)。\n"
        f"{bash_line}"
        f"{browser_block}"
        "\n"
        "原则:\n"
        "1. 修改前先读取相关文件，确认上下文再动手；不要凭空写代码。\n"
        "2. 改动尽量用 str_replace 而不是 write_file，避免覆盖未读过的内容。\n"
        "3. 如果工具调用失败，分析错误，调整参数后再尝试；不要陷入死循环。\n"
        "4. 任务完成或不确定时，给用户清晰的简短结论。\n"
    )


# ---------------------------------------------------------------------------
# version / init
# ---------------------------------------------------------------------------

@app.command("update")
def cmd_update(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="跳过确认，直接下载。",
    ),
) -> None:
    """检查并下载新版本 (下载后给出安装指引)。"""
    console.print("正在检查更新…")
    rel = updater.check_for_update(force=True)
    if rel is None:
        console.print("[green]已是最新版本。[/green]")
        return
    console.print(
        f"[yellow]发现新版本 [bold]v{rel.version}[/bold]（当前 v{__version__}）。[/yellow]"
    )
    if not yes:
        if not Confirm.ask("现在下载更新?", default=True):
            console.print(f"[dim]已跳过。手动下载：{rel.html_url}[/dim]")
            return
    _run_update_flow(rel)


def _run_update_flow(rel: "updater.ReleaseInfo") -> None:
    """Download the platform asset for ``rel`` and print install guidance.

    Shared by ``mbridge update``, the REPL ``同意`` shortcut and ``/update``.
    We download only — the user runs the installer / extracts the tarball,
    following the printed steps. Any failure falls back to the release page.
    """
    # Source / pip installs can't consume the binary assets — point at pip.
    if updater.install_mode() == "source":
        console.print(updater.source_upgrade_hint(rel.tag))
        console.print(f"[dim]或在此查看 Release：{rel.html_url}[/dim]")
        return

    asset = updater.pick_asset(rel)
    if asset is None:
        console.print(
            "[yellow]没有找到适配当前平台的安装包。[/yellow]\n"
            f"请到 Release 页面手动下载：{rel.html_url}"
        )
        return

    console.print(f"正在下载 [bold]{asset.name}[/bold] …")
    try:
        with console.status("下载中…", spinner="dots"):
            path = updater.download_asset(asset)
    except Exception as e:  # noqa: BLE001 — fall back to the release page
        console.print(
            f"[red]下载失败：{e}[/red]\n请手动下载：{rel.html_url}"
        )
        return

    console.print(f"[green]✓ 已下载到：[bold]{path}[/bold][/green]")
    console.print(
        Panel.fit(
            updater.install_instructions(path),
            title=f"安装 v{rel.version}",
            border_style="green",
        )
    )
    updater.reveal_in_file_manager(path)


@app.command("init")
def cmd_init(
    force: bool = typer.Option(
        False, "--force", "-f", help="覆盖 ~/.modelbridge/ 已有的 config.yaml / models.yaml。",
    ),
) -> None:
    """初始化 ~/.modelbridge/ 配置目录。"""
    try:
        result = init_app_dir(force=force)
    except OSError as e:
        err_console.print(f"[red]初始化失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(
        Panel.fit(f"配置目录：[bold]{get_app_dir()}[/bold]", title="mbridge init")
    )
    for name, created in result.items():
        if created:
            console.print(f"  [green]✓[/green] 已写入 {name}")
        else:
            console.print(f"  [yellow]·[/yellow] 已存在，跳过 {name}  (用 --force 覆盖)")
    console.print("  [green]✓[/green] logs/ 已就绪")
    console.print()
    console.print("下一步：[bold]mbridge model init[/bold] 添加你的第一个模型。")


# ---------------------------------------------------------------------------
# ask  (was: chat — R3a rename)
# ---------------------------------------------------------------------------


def _print_chat_dry_run(result, model_opt: Optional[str]) -> None:
    """Print target model + token/cost estimate for ``chat --dry-run``."""
    target = model_opt or load_app_config().default_model
    entry = find_model(target) if target else None
    text = "\n".join(text_of(m.content) for m in result.messages)
    n_tokens = estimate_tokens(text)

    lines = [
        f"target model  : {target or '[red](未指定)[/red]'}",
        f"messages      : {len(result.messages)}",
        f"prompt tokens≈: {n_tokens}",
    ]
    if entry is not None:
        try:
            p = get_pricing(entry)
            if entry.capabilities.local or p.input_per_1m <= 0:
                lines.append("input cost≈   : [green]0 (local / free)[/green]")
            else:
                cost = n_tokens / 1_000_000 * p.input_per_1m
                lines.append(
                    f"input cost≈   : {cost:.6f} {p.currency} (仅输入, 不含输出)"
                )
        except PricingNotFound:
            lines.append("input cost≈   : [dim]pricing 未知[/dim]")
    console.print(Panel.fit("\n".join(lines), title="dry-run (未实际调用)", border_style="yellow"))


def _vision_model_names() -> list[str]:
    """已注册模型里 capabilities.vision=true 的名字（给 vision 门禁的提示用）。"""
    try:
        mf = load_models_file()
        return [m.name for m in mf.models if getattr(m.capabilities, "vision", False)]
    except Exception:  # noqa: BLE001 — 提示用途，失败给空表即可
        return []


@app.command(
    "ask",
    help=(
        "对模型发起一次单轮请求（非交互，可用于脚本 / 管道）。\n"
        "加 --route / --auto 自动路由；--fallback 失败升级重试。"
    ),
)
def cmd_ask(
    prompt: str = typer.Argument(..., help="要发送给模型的内容。"),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="模型名 (默认使用 config.yaml 中的 default_model)。",
    ),
    system: Optional[str] = typer.Option(None, "--system", "-s", help="可选 system prompt。"),
    timeout: float = typer.Option(60.0, "--timeout", help="请求超时秒数。"),
    thinking: Optional[bool] = typer.Option(
        None, "--thinking/--no-thinking",
        help="对 Qwen 等支持显式 thinking 开关的 provider 启用 thinking。",
    ),
    thinking_budget: Optional[int] = typer.Option(
        None, "--thinking-budget", help="thinking token 上限 (provider 视支持情况而定)。",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="输出诊断细节并保存 raw 响应。"),
    use_route: bool = typer.Option(
        False, "--route",
        help="使用 router 自动选择模型 (覆盖 --model)。",
    ),
    auto: bool = typer.Option(
        False, "--auto",
        help="`--route` 的别名。",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="路由模式：economy / balanced / powerful (仅与 --route / --auto 一起用)。",
    ),
    fallback: bool = typer.Option(
        False, "--fallback",
        help="调用失败时按 routing.fallback.max_upgrade_steps 升级重试。",
    ),
    project: Optional[Path] = typer.Option(
        None, "--project", "-p",
        help="项目目录 — 自动加载 AGENT.md / CLAUDE.md / .cursorrules 等项目规则，"
             "并把扫描出的 project summary 一起注入 prompt。",
    ),
    show_prompt: bool = typer.Option(
        False, "--show-prompt",
        help="不调用模型，只把 PromptBuilder 组装出的 sections 打印出来 (调试用)。",
    ),
    show_files: bool = typer.Option(
        False, "--show-files",
        help="显示项目文件选择结果 (文件 / reason / 行数)，仍然继续调用模型。",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="不调用模型：打印组装后的 prompt + 目标模型 + 预估 token/费用 "
             "(等价 --show-prompt 再加估算)。",
    ),
    image: Optional[list[str]] = typer.Option(
        None, "--image",
        help="附加图片（本地路径或 http(s):// / data: URL，可重复）。需模型 capabilities.vision=true。",
    ),
    max_context: int = typer.Option(
        DEFAULT_MAX_CONTEXT_CHARS, "--max-context",
        help="prompt 总字符上限 (rules+summary+files+user)。超出时按优先级截断。",
    ),
) -> None:
    """与模型单轮对话。"""
    logger = get_logger()

    if image and (use_route or auto):
        err_console.print(
            "[red]--image 暂不支持与 --route/--auto 同用；请显式 --model 指定 vision 模型。[/red]"
        )
        raise typer.Exit(code=2)

    if (use_route or auto) and model:
        err_console.print(
            "[red]--route / --auto 与 --model 互斥；去掉 --model 让路由器选。[/red]"
        )
        raise typer.Exit(code=2)

    if dry_run and (use_route or auto):
        err_console.print(
            "[yellow]--dry-run 与 --route/--auto 一起用时，请改用 "
            "`mbridge route \"...\"` 预览路由 (它本就不调用最终模型)。[/yellow]"
        )
        raise typer.Exit(code=2)

    # ----- default path: always build via PromptBuilder ------------------
    # ``mbridge ask`` always assembles its prompt through the canonical
    # PromptBuilder so the stable prefix order is the same here as in the
    # REPL and ``mbridge prompt hash``. Without --project we just skip
    # the project scan + file selection (no project_rules / summary /
    # project_files in the result); with --project we add them.
    if not (use_route or auto):
        image_blocks: list[dict] = []
        if image:
            for arg in image:
                try:
                    image_blocks.append(images.resolve_image_arg(arg))
                except images.ImageError as e:
                    err_console.print(f"[red]{e}[/red]")
                    raise typer.Exit(code=2)
        builder = PromptBuilder().with_user_request(prompt, images=image_blocks)
        if system:
            builder = builder.with_system_prompt(system)
        summary: Optional[ProjectSummary] = None
        selection: Optional[SelectionResult] = None
        ctx_plan: Optional[ContextPlan] = None
        file_contexts: list[FileContext] = []

        if project is not None:
            summary, cache_check = scan_project_cached(project)
            builder = builder.with_project(project).with_project_summary(
                summary.to_markdown(),
                file_tree_hash=summary.file_tree_hash,
            )

            # Phase 5: pick + read relevant files, then plan within budget.
            selection = select_files(prompt, summary)
            file_contexts = read_files(
                selection.files, project_root=project,
            )
            # Estimate overhead from current builder state for budget planning.
            preview = builder.build()
            rules_chars = (
                len(preview.sections.get("global_rules", ""))
                + len(preview.sections.get("project_rules", ""))
            )
            system_chars = len(preview.sections.get("core_system", ""))
            summary_chars = len(preview.sections.get("project_summary", ""))
            ctx_plan = plan_context(
                file_contexts,
                user_query=prompt,
                rules_chars=rules_chars,
                system_chars=system_chars,
                project_summary_chars=summary_chars,
                max_chars=max_context,
            )
            builder = builder.with_project_files(ctx_plan.kept_files)

        result = builder.build()

        if show_files and selection is not None:
            _print_selected_files(selection, ctx_plan, file_contexts)

        if show_prompt or dry_run:
            # ``--show-prompt`` / ``--dry-run`` are inspection-only — do NOT
            # count them against prefix observations, that would pollute
            # hit-rate diagnostics.
            _print_prompt_assembly(result)
            if dry_run:
                _print_chat_dry_run(result, model)
            return

        # We're about to actually hit the provider — log this as a cache
        # observation so ``mbridge cache stats`` can see prefix stability.
        record_prefix_observation(
            prefix_hash=result.stable_prefix_hash,
            section_hashes=result.section_hashes,
        )

        # Wire the assembled messages into a chat call.
        # We bypass chat_once (which builds its own minimal messages) and
        # talk to the provider directly so the full PromptBuilder output
        # survives.
        target_model = model or load_app_config().default_model
        if not target_model:
            err_console.print("[red]未指定 model 且没有 default_model。[/red]")
            raise typer.Exit(code=2)
        entry = find_model(target_model)
        if entry is None:
            err_console.print(f"[red]找不到模型 '{target_model}'。[/red]")
            raise typer.Exit(code=2)
        if image_blocks:
            try:
                images.ensure_vision(
                    has_images=True,
                    model_is_vision=bool(getattr(entry.capabilities, "vision", False)),
                    model_name=entry.name,
                    available_vision=_vision_model_names(),
                )
            except images.ImageError as e:
                err_console.print(f"[red]{e}[/red]")
                raise typer.Exit(code=2)
        provider = get_provider(entry)
        req = ChatRequest(
            model=entry.model,
            messages=result.messages,
            temperature=(entry.extra or {}).get("temperature"),
            max_tokens=(entry.extra or {}).get("max_tokens"),
            thinking=thinking,
            thinking_budget=thinking_budget,
        )
        verbose_label = "chat_project" if project is not None else "chat"
        try:
            resp = provider.chat(req, timeout=timeout, save_raw=verbose, verbose_label=verbose_label)
        except ProviderError as e:
            _print_provider_error(e)
            raise typer.Exit(code=3) from e

        title = (
            f"[bold cyan]{entry.name}[/bold cyan] · {entry.provider.value} · "
            f"{resp.elapsed_ms}ms · prefix={result.prompt_prefix_hash}"
        )
        console.print(Panel(resp.content or "[dim](empty)[/dim]", title=title, border_style="cyan"))
        _record_cache_outcome(entry, resp)

        meta_parts = [
            f"rules sources: {', '.join(sum((v for v in result.sources.values()), [])) or '(none)'}",
            f"total_chars={result.total_chars}",
        ]
        if result.truncated:
            meta_parts.append("[yellow]rules truncated[/yellow]")
        if ctx_plan is not None and (ctx_plan.dropped_files or ctx_plan.truncated_files):
            meta_parts.append("[yellow]context truncated to fit model limits[/yellow]")
        console.print("[dim]" + "  · ".join(meta_parts) + "[/dim]")

        if verbose:
            if selection is not None:
                _print_selected_files(selection, ctx_plan, file_contexts)
            _print_verbose(entry, resp)
        elif resp.reasoning_content:
            console.print(f"[dim]reasoning_content: {len(resp.reasoning_content)} 字符 (加 -v 查看)[/dim]")
        logger.info("chat ok model=%s elapsed=%dms prefix=%s", entry.name, resp.elapsed_ms, result.prompt_prefix_hash)
        return

    if use_route or auto:
        _chat_with_routing(
            prompt,
            system=system,
            timeout=timeout,
            thinking=thinking,
            thinking_budget=thinking_budget,
            verbose=verbose,
            mode=mode,
            fallback=fallback,
        )
        return

    # The two branches above both ``return``. The original ``chat_once``
    # fallback (which built minimal [system?, user] messages and bypassed
    # the stable prefix) is gone — every ``mbridge ask`` invocation now
    # flows through PromptBuilder so DeepSeek/Qwen prefix caching has a
    # chance to fire.


def _record_cache_outcome(entry: ModelEntry, resp) -> None:
    """Read provider-reported cache hit/miss from ``resp.usage`` and persist.

    No-op when the provider doesn't report cache info (older providers,
    local models, or just no cached prefix this turn). When there IS a
    hit, the saved cost is estimated from this model's input pricing:
    DeepSeek / Qwen / Kimi all bill cached tokens at ~25 % of the
    non-cached rate, so the savings = ``cached_tokens × input_rate × 0.75``.
    Inaccurate by provider for sure, but useful as a directional figure
    in ``mbridge cache stats``.
    """
    hit, miss = extract_cache_tokens(getattr(resp, "usage", None))
    if hit <= 0 and miss <= 0:
        return  # provider didn't report any cache info — leave stats alone
    if hit > 0:
        saved_cost = 0.0
        try:
            pricing = get_pricing(entry)
            if pricing is not None:
                full = pricing.cost(input_tokens=hit, output_tokens=0)
                saved_cost = full * 0.75
        except Exception:  # noqa: BLE001 — pricing not configured is fine
            pass
        record_hit(saved_tokens=hit, saved_cost=saved_cost)
    else:
        record_miss()


def _chat_with_routing(
    prompt: str,
    *,
    system: Optional[str],
    timeout: float,
    thinking: Optional[bool],
    thinking_budget: Optional[int],
    verbose: bool,
    mode: Optional[str],
    fallback: bool,
) -> None:
    """Implements `mbridge ask --route [--fallback]`."""
    logger = get_logger()
    try:
        result = route_prompt(prompt, mode=mode, use_llm=True)
    except LLMClassifyError as e:
        err_console.print(
            f"[red]LLM 路由分级失败：[/red]{e}\n"
            "[yellow]提示：--route 现在用最低层 (tiny) 模型做分类。"
            "确认 routing.levels.tiny（或 default_model）指向一个可达模型。[/yellow]"
        )
        raise typer.Exit(code=2) from e
    _print_route_result(result, verbose=verbose)

    if not result.chosen_model:
        err_console.print(
            "[red]路由器未能解析到任何模型。检查 routing.levels / models.yaml。[/red]"
        )
        raise typer.Exit(code=2)

    cur_model = result.chosen_model
    cur_level = result.chosen_level
    attempts_used = 0

    while True:
        try:
            entry, resp = chat_once(
                prompt,
                model_name=cur_model,
                system=system,
                timeout=timeout,
                thinking=thinking,
                thinking_budget=thinking_budget,
                save_raw=verbose,
                verbose_label="chat_route",
            )
        except ChatError as e:
            if not fallback:
                err_console.print(f"[red]{e}[/red]")
                raise typer.Exit(code=2) from e
            esc = escalate_after_failure(
                cur_level or ModelLevel.CHEAP,
                reason=str(e),
                attempts_used=attempts_used,
            )
            if not esc.escalated:
                err_console.print(f"[red]{e}[/red]")
                err_console.print(
                    f"[yellow]fallback 终止: {esc.step.note}[/yellow]"
                )
                raise typer.Exit(code=2) from e
            # esc.escalated is True here → chosen_model / chosen_level are set.
            assert esc.chosen_model is not None and esc.chosen_level is not None
            console.print(
                f"[yellow]{cur_model} 调用失败 ({e})；已自动升级到 "
                f"[bold]{esc.chosen_model}[/bold] (level={esc.chosen_level.value}). 重试中…[/yellow]"
            )
            cur_model = esc.chosen_model
            cur_level = esc.chosen_level
            attempts_used += 1
            continue
        except ProviderError as e:
            if not fallback:
                _print_provider_error(e)
                raise typer.Exit(code=3) from e
            esc = escalate_after_failure(
                cur_level or ModelLevel.CHEAP,
                reason=e.message or "provider error",
                attempts_used=attempts_used,
            )
            if not esc.escalated:
                _print_provider_error(e)
                err_console.print(
                    f"[yellow]fallback 终止: {esc.step.note}[/yellow]"
                )
                raise typer.Exit(code=3) from e
            # esc.escalated is True here → chosen_model / chosen_level are set.
            assert esc.chosen_model is not None and esc.chosen_level is not None
            console.print(
                f"[yellow]{cur_model} 调用失败 ({e.message})；已自动升级到 "
                f"[bold]{esc.chosen_model}[/bold] (level={esc.chosen_level.value}). 重试中…[/yellow]"
            )
            cur_model = esc.chosen_model
            cur_level = esc.chosen_level
            attempts_used += 1
            continue

        # Success
        logger.info(
            "chat-route ok model=%s elapsed=%dms attempts=%d",
            entry.name, resp.elapsed_ms, attempts_used + 1,
        )
        title = (
            f"[bold cyan]{entry.name}[/bold cyan] · {entry.provider.value} · "
            f"{resp.elapsed_ms}ms · routed"
        )
        console.print(
            Panel(resp.content or "[dim](empty)[/dim]", title=title, border_style="cyan")
        )
        if verbose:
            _print_verbose(entry, resp)
        elif resp.reasoning_content:
            console.print(
                f"[dim]reasoning_content: {len(resp.reasoning_content)} 字符 (加 -v 查看)[/dim]"
            )
        return


def _print_verbose(entry: ModelEntry, resp) -> None:
    provider = get_provider(entry)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("provider", entry.provider.value)
    table.add_row("base_url", entry.base_url)
    table.add_row("model", entry.model)
    table.add_row("endpoint", provider.chat_endpoint())
    table.add_row("api key", mask_secret(provider.api_key))
    table.add_row("latency", f"{resp.elapsed_ms} ms")
    table.add_row("finish_reason", str(resp.finish_reason))
    table.add_row("usage", str(resp.usage))
    table.add_row(
        "reasoning_content",
        f"{len(resp.reasoning_content)} 字符" if resp.reasoning_content else "(none)",
    )
    table.add_row("raw saved", str(get_logs_dir()))
    console.print(Panel(table, title="verbose", border_style="blue"))
    if resp.reasoning_content:
        console.print(
            Panel(resp.reasoning_content, title="reasoning_content", border_style="magenta")
        )


# ---------------------------------------------------------------------------
# model init / add — SIMPLIFIED via provider profiles
# ---------------------------------------------------------------------------

# Display order: most-used providers first, then locals, then misc.
_PROVIDER_DISPLAY_ORDER: list[ProviderType] = [
    ProviderType.DEEPSEEK,
    ProviderType.QWEN,
    ProviderType.KIMI,
    ProviderType.MIMO,
    ProviderType.GLM,
    ProviderType.MINIMAX,
    ProviderType.OPENAI,
    ProviderType.OLLAMA,
    ProviderType.VLLM,
    ProviderType.LMSTUDIO,
    ProviderType.OPENAI_COMPATIBLE,
    ProviderType.CUSTOM,
]


def _pick_provider() -> ProviderType:
    """Show numbered menu of provider presets; return the chosen ProviderType."""
    table = Table(title="选择 provider 预设", show_lines=False)
    table.add_column("#", style="bold")
    table.add_column("provider")
    table.add_column("base_url", overflow="fold")
    table.add_column("local")
    table.add_column("备注", overflow="fold")
    for i, pt in enumerate(_PROVIDER_DISPLAY_ORDER, start=1):
        prof = PROFILES[pt]
        table.add_row(
            str(i),
            f"[bold]{prof.label}[/bold] ({pt.value})",
            prof.base_url,
            "[green]✓[/green]" if prof.is_local else "[dim]·[/dim]",
            prof.notes or "",
        )
    console.print(table)
    choice = Prompt.ask(
        f"[bold]输入序号 (1-{len(_PROVIDER_DISPLAY_ORDER)})[/bold]",
        default="1",
    ).strip()
    try:
        idx = int(choice)
    except ValueError:
        idx = 1
    idx = max(1, min(idx, len(_PROVIDER_DISPLAY_ORDER)))
    return _PROVIDER_DISPLAY_ORDER[idx - 1]


def _prompt_model_id(prof) -> str:
    examples = prof.model_examples or []
    suggestion = examples[0] if examples else ""
    hint = ""
    if examples:
        hint = "  [dim]示例：" + " / ".join(examples) + "[/dim]"
        console.print(hint)
    model_id = Prompt.ask("[bold]模型 ID[/bold]", default=suggestion).strip()
    while not model_id:
        model_id = Prompt.ask("[red]模型 ID 不能为空[/red]").strip()
    return model_id


def _prompt_name(default_name: str) -> str:
    name = Prompt.ask("[bold]模型显示名称[/bold] (CLI 里用这个名字)", default=default_name).strip()
    while not name:
        name = Prompt.ask("[red]名称不能为空[/red]").strip()
    if find_model(name) is not None:
        if not Confirm.ask(f"模型 '{name}' 已存在，是否覆盖?", default=False):
            raise typer.Exit(code=0)
    return name


def _prompt_api_key_cloud(prof) -> tuple[str, str | None]:
    """Return (api_key_literal, api_key_env)."""
    suggested_env = prof.api_key_env or ""
    env_var = Prompt.ask(
        "[bold]API Key 环境变量名[/bold] (推荐：用环境变量更安全；留空 = 不用)",
        default=suggested_env,
    ).strip()
    api_key_env: str | None = env_var or None

    if api_key_env and os.environ.get(api_key_env):
        console.print(f"[green]检测到环境变量 {api_key_env}，调用时会自动读取。[/green]")
        return "", api_key_env

    api_key = Prompt.ask(
        "[bold]API Key[/bold] (不会回显；留空则之后必须设置环境变量)",
        password=True,
        default="",
    )
    if api_key:
        console.print(
            "[yellow]提示：API key 会加密存储 (keyring / 本地加密)，不会明文写入 "
            "models.yaml。更推荐改用 api_key_env 环境变量，完全不落盘。[/yellow]"
        )
    return api_key, api_key_env


def _interactive_add() -> ModelEntry:
    """The new simplified flow:

    1. Pick provider preset.
    2. Enter model id (1 prompt).
    3. Enter API Key — env var preferred (1 prompt; skipped if env exists).
    4. (Optional) name override + advanced.

    Local models keep the longer flow (base_url, no-key allowed, capability prompts).
    """
    console.print(
        Panel.fit(
            "添加一个模型。默认会从 provider 预设里填好 base_url / capabilities。\n"
            "本地模型 (Ollama / vLLM / LM Studio) 会问得更细一些。",
            title="mbridge model init",
            border_style="cyan",
        )
    )

    provider = _pick_provider()
    prof = get_profile(provider)

    # base_url — for cloud presets we keep the default; for local we confirm.
    if prof.is_local:
        base_url = Prompt.ask(
            "[bold]base_url[/bold] (本地服务地址)", default=prof.base_url
        ).strip()
    else:
        base_url = prof.base_url
        console.print(f"[dim]base_url = {base_url}  (按预设)[/dim]")

    model_id = _prompt_model_id(prof)

    # name — default to model_id (most users want them equal).
    default_name = model_id.replace(":", "-").replace("/", "-")
    name = _prompt_name(default_name)

    # API Key
    if prof.is_local:
        api_key, api_key_env = "EMPTY", None
        console.print("[dim]本地模型，api_key = EMPTY[/dim]")
    else:
        api_key, api_key_env = _prompt_api_key_cloud(prof)

    # Capabilities — start from profile, only ask deeper questions for local.
    caps = Capabilities(**prof.default_capabilities.model_dump())
    level = prof.default_level

    if prof.is_local:
        console.print("\n[bold]本地模型能力声明 (y/n)：[/bold]")
        caps.tools = Confirm.ask("  支持 tool calls?", default=False)
        caps.json = Confirm.ask("  支持 JSON mode?", default=False)
        caps.reasoning = Confirm.ask("  支持 thinking / reasoning?", default=False)
        if caps.reasoning:
            caps.reasoning_content_back = True
        # level for local defaults to tiny — let user override.
        level_str = Prompt.ask(
            "[bold]模型等级[/bold]",
            choices=[lvl.value for lvl in ModelLevel],
            default=level.value,
        )
        level = ModelLevel(level_str)
    else:
        if Confirm.ask("\n是否要进一步调整能力 / 等级? (默认按预设)", default=False):
            level_str = Prompt.ask(
                "[bold]模型等级[/bold]",
                choices=[lvl.value for lvl in ModelLevel],
                default=level.value,
            )
            level = ModelLevel(level_str)
            caps.tools = Confirm.ask("  支持 tool calls?", default=caps.tools)
            caps.json = Confirm.ask("  支持 JSON mode?", default=caps.json)
            caps.reasoning = Confirm.ask("  支持 thinking / reasoning?", default=caps.reasoning)
            if caps.reasoning:
                caps.reasoning_content_back = Confirm.ask(
                    "  tool_calls 时必须回传 reasoning_content? (MiMo / 部分 Kimi 是)",
                    default=caps.reasoning_content_back,
                )

    if prof.notes:
        console.print(f"[dim]备注：{prof.notes}[/dim]")

    return ModelEntry(
        name=name,
        provider=provider,
        type=TransportType.OPENAI_COMPATIBLE,
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=api_key,
        model=model_id,
        level=level,
        capabilities=caps,
        extra={} if prof.is_local else {"temperature": 0.3, "max_tokens": 4096},
    )


@model_app.command("init")
def cmd_model_init() -> None:
    """交互式添加模型 (推荐入口)。"""
    _do_model_add()


@model_app.command("add")
def cmd_model_add() -> None:
    """交互式添加模型 (`model init` 的别名)。"""
    _do_model_add()


def _do_model_add() -> None:
    if not get_config_path().exists() or not get_models_path().exists():
        if Confirm.ask("尚未执行 `mbridge init`，是否现在初始化?", default=True):
            init_app_dir(force=False)
        else:
            err_console.print("[red]请先执行 `mbridge init`。[/red]")
            raise typer.Exit(code=1)

    try:
        entry = _interactive_add()
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消。[/yellow]")
        raise typer.Exit(code=130)  # noqa: B904

    try:
        replaced = upsert_model(entry)
    except (ConfigError, OSError) as e:
        err_console.print(f"[red]保存失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    verb = "更新" if replaced else "添加"
    console.print(
        f"\n[green]✓[/green] 已{verb}模型 [bold]{entry.name}[/bold]，"
        f"写入 {get_models_path()}"
    )

    # Self-heal default_model: if the configured default doesn't (or no
    # longer) refers to a real model, point it at the entry we just
    # saved. Stops users from hitting "找不到模型 'deepseek-chat'" right
    # after a successful `mbridge model init`.
    try:
        cfg = load_app_config()
        default_ok = bool(cfg.default_model and find_model(cfg.default_model))
        if not default_ok:
            old = cfg.default_model
            cfg.default_model = entry.name
            save_app_config(cfg)
            console.print(
                f"[dim]config.default_model = {entry.name}"
                + (f"  (之前是 {old!r}，但 models.yaml 里没有)" if old else "")
                + "[/dim]"
            )
    except (ConfigError, OSError) as e:
        # Self-heal is opportunistic — never block the add.
        console.print(f"[yellow]提示：更新 config.default_model 失败：{e}[/yellow]")

    console.print(
        "下一步：\n"
        f"  [bold]mbridge doctor model {entry.name}[/bold]\n"
        f"  [bold]mbridge[/bold]   (进入持续会话)"
    )


# ---------------------------------------------------------------------------
# model list / test / remove
# ---------------------------------------------------------------------------

@model_app.command("list")
def cmd_model_list() -> None:
    """列出全部已注册模型。"""
    try:
        mf = load_models_file()
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    if not mf.models:
        console.print("[yellow]尚未配置任何模型。运行 `mbridge model init` 添加。[/yellow]")
        return

    table = Table(title=f"models ({len(mf.models)})", show_lines=False)
    table.add_column("name", style="bold cyan")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("level")
    table.add_column("base_url", overflow="fold")
    table.add_column("local")
    table.add_column("tools")
    table.add_column("reasoning")
    table.add_column("cache")

    def yn(b: bool) -> str:
        return "[green]✓[/green]" if b else "[dim]·[/dim]"

    for m in mf.models:
        table.add_row(
            m.name, m.provider.value, m.model, m.level.value, m.base_url,
            yn(m.capabilities.local), yn(m.capabilities.tools),
            yn(m.capabilities.reasoning), yn(m.capabilities.cache),
        )
    console.print(table)


# IA v1.2 cleanup: `mbridge model test` is GONE (was: deprecated alias for `doctor model`)
# — use `mbridge doctor model <name>`. See the cleaner explanation at cli.py:2132.


@model_app.command("remove")
def cmd_model_remove(
    name: str = typer.Argument(..., help="要删除的模型名。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认。"),
) -> None:
    """删除一个模型。"""
    if not yes and not Confirm.ask(f"确认删除模型 '{name}' ?", default=False):
        raise typer.Exit(code=0)
    if remove_model(name):
        console.print(f"[green]✓[/green] 已删除 '{name}'。")
    else:
        err_console.print(f"[yellow]'{name}' 不存在。[/yellow]")
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@doctor_app.callback()
def cmd_doctor_default(ctx: typer.Context) -> None:
    """运行环境自检 (无子命令时)。"""
    if ctx.invoked_subcommand is not None:
        return
    results = run_global_doctor()
    table = Table(title="mbridge doctor", show_lines=False)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    table.add_column("hint", overflow="fold")
    for r in results:
        table.add_row(
            r.name,
            "[green]OK[/green]" if r.ok else "[red]FAIL[/red]",
            r.detail,
            r.hint or "",
        )
    console.print(table)
    for line in next_steps_for_global(results):
        console.print(f"[bold]→[/bold] {line}")
    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@doctor_app.command("model")
def cmd_doctor_model(
    name: str = typer.Argument(..., help="模型名 (见 `mbridge model list`)。"),
    test_tools: bool = typer.Option(
        False, "--tools", help="额外做一次 tool_calls 测试 (capabilities.tools=true 才会真测)。",
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="超时秒数。"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="输出诊断细节并保存 raw 响应。"),
) -> None:
    """对单个模型做完整探测。"""
    entry = find_model(name)
    if entry is None:
        err_console.print(f"[red]找不到模型 '{name}'。[/red]")
        raise typer.Exit(code=2)

    report = run_model_doctor(
        entry, test_tools=test_tools, save_raw=verbose, timeout=timeout
    )

    table = Table(title=f"doctor: {name} ({entry.provider.value})", show_lines=False)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    table.add_column("hint", overflow="fold")
    for r in report.results:
        table.add_row(
            r.name,
            "[green]OK[/green]" if r.ok else "[red]FAIL[/red]",
            r.detail,
            r.hint or "",
        )
    console.print(table)

    summary_color = "green" if report.status == "OK" else "red"
    console.print(
        f"[{summary_color}]{report.status}[/{summary_color}]  "
        f"chat_ok={report.chat_ok}  json_ok={report.json_ok}  "
        f"tools_ok={report.tools_ok}  has_reasoning={report.has_reasoning}"
    )
    if verbose:
        console.print(f"[dim]raw 已保存到 {get_logs_dir()}[/dim]")
    if report.status != "OK":
        raise typer.Exit(code=1)


# IA v1.2 cleanup: `mbridge model test` is GONE — use `mbridge doctor model <name>`.


@doctor_app.command("all")
def cmd_doctor_all(
    test_tools: bool = typer.Option(False, "--tools", help="对每个支持 tools 的模型做工具调用测试。"),
    timeout: float = typer.Option(20.0, "--timeout", help="单模型超时秒数。"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="保存 raw 响应。"),
) -> None:
    """对所有模型逐个测试。"""
    reports = run_doctor_all(test_tools=test_tools, save_raw=verbose, timeout=timeout)
    if not reports:
        console.print("[yellow]models.yaml 中没有模型。[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title="doctor all", show_lines=False)
    table.add_column("name", style="bold cyan")
    table.add_column("provider")
    table.add_column("level")
    table.add_column("chat")
    table.add_column("json")
    table.add_column("tools")
    table.add_column("reasoning")
    table.add_column("latency")
    table.add_column("status")
    table.add_column("hints", overflow="fold")

    def tri(v):
        if v is True:
            return "[green]✓[/green]"
        if v is False:
            return "[red]✗[/red]"
        return "[dim]·[/dim]"

    for r in reports:
        table.add_row(
            r.name, r.provider, r.level,
            tri(r.chat_ok),
            tri(r.json_ok),
            tri(r.tools_ok),
            tri(r.has_reasoning),
            f"{r.chat_latency_ms} ms" if r.chat_latency_ms is not None else "-",
            ("[green]OK[/green]" if r.status == "OK" else "[red]FAIL[/red]"),
            ("\n".join(r.hints))[:300],
        )
    console.print(table)
    if any(r.status != "OK" for r in reports):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

# NOTE: _run_route_test is defined below (forward ref resolved at call time).
@doctor_app.command("route")
def cmd_doctor_route(
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="路由模式：economy / balanced / powerful (默认读 config.yaml routing.mode)。",
    ),
) -> None:
    """跑内置 8 题路由验证集，确认路由配置正确。"""
    _run_route_test(mode)


def _print_route_trace(result: RouteResult) -> None:
    """Print a verbose step-by-step routing trace."""
    profile = result.profile
    steps: list[tuple[str, str]] = []

    # Helper: strip the "LLM 分级 (模型 xxx): " boilerplate prefix.
    def _strip_llm_prefix(r: str) -> str:
        marker = "): "
        idx = r.find(marker)
        return r[idx + len(marker):] if idx != -1 else r

    # Step 1: LLM classifier verdict
    for r in profile.reasons:
        if r.startswith("LLM 分级"):
            steps.append(("① LLM 分级器", _strip_llm_prefix(r)))
            break
    else:
        # keyword classifier path
        for r in profile.reasons:
            if r.startswith("命中 "):
                steps.append(("① 关键词分类", r))
                break
        else:
            steps.append(("① 分类器", profile.reasons[0] if profile.reasons else "无"))

    # Step 2: caller-fact floors
    floors = [r for r in profile.reasons
              if any(k in r for k in ("has_files", "wants_edit", "wants_tools", "risk_level=high", "previous_failures"))]
    if floors:
        steps.append(("② 显式信号约束", " | ".join(floors)))

    # Step 3: mode shift
    if result.mode_note:
        steps.append(("③ 模式偏移", result.mode_note.replace("mode=", "mode=")))

    # Step 4: fallback walk
    chain = result.fallback.chain
    tried: list[str] = []
    for lvl, mdl, status in chain:
        lvl_s = lvl.value if lvl else "(default)"
        if status == "OK":
            tried.append(f"[green]✓ {lvl_s} → {mdl}[/green]")
        else:
            tried.append(f"[red]✗ {lvl_s} → {status}[/red]")
    steps.append(("④ 回退链", " → ".join(tried) if tried else "(无回退)"))

    trace = Table(title="routing trace (--verbose)", show_lines=False)
    trace.add_column("step", style="dim", width=14)
    trace.add_column("detail")
    for step, detail in steps:
        trace.add_row(step, detail)
    console.print(trace)


def _print_route_result(result: RouteResult, verbose: bool = False) -> None:
    """Render a RouteResult into the standard route panel + tables.

    ``verbose=True`` adds the LLM classifier's own reasoning and the full
    per-step routing trace (mode shift, caller-fact floors, fallback walk).
    """
    profile = result.profile
    chosen_model_str = (
        f"[bold green]{result.chosen_model}[/bold green]"
        if result.chosen_model
        else "[red]未解析到模型[/red]"
    )

    cache_str = "[dim]·[/dim]"
    entry = find_model(result.chosen_model) if result.chosen_model else None
    if entry is not None:
        cache_str = (
            "[green]supported[/green]"
            if entry.capabilities.cache
            else "[yellow]unknown / disabled[/yellow]"
        )

    cost_band = "[dim]unknown[/dim]"
    if entry is not None:
        try:
            p = get_pricing(entry)
            rate = (p.input_per_1m + p.output_per_1m) / 2
            if entry.capabilities.local or rate <= 0:
                cost_band = "[green]极低 (local)[/green]"
            elif rate < 3:
                cost_band = "[green]低[/green]"
            elif rate < 12:
                cost_band = "[yellow]中[/yellow]"
            else:
                cost_band = "[red]高[/red]"
        except PricingNotFound:
            pass

    # ---- Risk badge -------------------------------------------------------
    risk_style = {
        "low": "green", "medium": "yellow", "high": "red"
    }.get(profile.risk_level, "dim")
    risk_badge = f"[{risk_style}]{profile.risk_level}[/]"

    # ---- Level arrow (shows mode shift if any) ----------------------------
    raw_level = result.decision.level
    final_level = result.chosen_level or raw_level
    if result.mode_note:
        level_str = f"[bold]{raw_level.value}[/bold]  ─[yellow]▶[/yellow]  [bold cyan]{final_level.value}[/bold cyan]"
    elif final_level != raw_level:
        level_str = f"[bold]{raw_level.value}[/bold]  ─▶  [bold cyan]{final_level.value}[/bold cyan]"
    else:
        level_str = f"[bold]{final_level.value}[/bold]"

    header_lines = [
        f"[dim]▶ routing · {result.mode} mode[/dim]",
        f"[bold]task_type  :[/bold] {profile.task_type}   [bold]complexity:[/bold] {profile.complexity}   [bold]risk:[/bold] {risk_badge}",
        f"[bold]level      :[/bold] {level_str}",
        f"[bold]model      :[/bold] {chosen_model_str}   [dim]cache: {cache_str}[/dim]   cost: {cost_band}",
    ]

    # In verbose mode: surface the LLM classifier's own reasoning front-and-centre.
    llm_reason = ""
    if verbose:
        for r in profile.reasons:
            if r.startswith("LLM 分级"):
                llm_reason = r
                break

    if llm_reason and verbose:
        # Strip the "LLM 分级 (模型 xxx): " prefix to show just the useful part.
        prefix = "LLM 分级 ("
        if prefix in llm_reason:
            llm_reason = llm_reason.split(prefix, 1)[1]
            colon_idx = llm_reason.find("): ")
            if colon_idx != -1:
                llm_reason = llm_reason[colon_idx + 3:]
        header_lines.append(f"[dim]  └─ AI 分级理由：[/dim][italic]{llm_reason}[/italic]")
    elif llm_reason:
        header_lines.append(f"[dim]  └─ AI: {llm_reason}[/dim]")

    header_lines.append(f"[dim]tokens≈ {estimate_tokens(result.prompt)}[/dim]")

    console.print(Panel.fit("\n".join(header_lines), title="route", border_style="cyan"))

    # ---- Verbose: step-by-step routing trace ------------------------------
    if verbose:
        _print_route_trace(result)


_ROUTE_TEST_PROMPTS: list[str] = [
    "什么是 Python 的 list？",
    "解释一下这个报错是什么意思：TypeError unsupported operand",
    "帮我写一个 FastAPI hello world",
    "帮我修复这个项目里的登录 bug",
    "分析整个项目架构并提出重构建议",
    "检查这个项目有没有安全漏洞",
    "使用 MCP 工具读取 GitHub issue 并修复",
    "这个模型为什么 400 了？",
]


def _run_route_test(mode: Optional[str]) -> None:
    console.print(
        f"[dim]route test 用最低层 (tiny) 模型对 {len(_ROUTE_TEST_PROMPTS)} 条 prompt "
        "逐一做实际分类调用（消耗少量 API 额度）。[/dim]"
    )
    table = Table(title=f"route test (mode={mode or 'config-default'})", show_lines=False)
    table.add_column("#", style="dim")
    table.add_column("question", overflow="fold")
    table.add_column("task_type")
    table.add_column("complexity")
    table.add_column("level")
    table.add_column("model", style="bold cyan")
    table.add_column("reasons", overflow="fold")

    any_unresolved = False
    for i, q in enumerate(_ROUTE_TEST_PROMPTS, start=1):
        try:
            result = route_prompt(q, mode=mode, use_llm=True)
        except LLMClassifyError as e:
            any_unresolved = True
            table.add_row(
                str(i), q, "[red]分级失败[/red]", "-", "-",
                "[red](error)[/red]", str(e)[:120],
            )
            continue
        model_str = result.chosen_model or "[red](unresolved)[/red]"
        if not result.chosen_model:
            any_unresolved = True
        table.add_row(
            str(i),
            q,
            result.profile.task_type,
            result.profile.complexity,
            result.decision.level.value,
            model_str,
            ("; ".join(result.profile.reasons))[:120],
        )
    console.print(table)
    if any_unresolved:
        err_console.print(
            "[yellow]部分 prompt 分级失败或没能解析到模型。确认最低层 (tiny) "
            "模型可达，并配置好 routing.levels / models.yaml。[/yellow]"
        )


@app.command(
    "route",
    help=(
        "路由分析：用最低层 (tiny) 模型把一段 prompt 分级、选模型 "
        "(会对 tiny 模型发一次分类调用，不会调用最终选中的模型)。\n"
        "特殊值 `mbridge route test` 跑内置 8 题验证路由配置。"
    ),
)
def cmd_route(
    prompt: str = typer.Argument(
        ..., help="要被路由的 prompt；填 `test` 跑内置测试集。",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="路由模式：economy / balanced / powerful (默认读 config.yaml routing.mode)。",
    ),
    explain: bool = typer.Option(
        True, "--explain/--no-explain", help="是否输出路由理由与回退链。",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="输出详细路由推理过程：LLM 分级理由、每步决策trace。",
    ),
) -> None:
    """对一段 prompt 输出推荐模型与原因 (分级会调用最低层 tiny 模型)。"""
    if prompt.strip().lower() == "test":
        err_console.print(
            "[yellow]⚠ `mbridge route test` 已移至 `mbridge doctor route`，将在 v1.2 移除。[/yellow]"
        )
        _run_route_test(mode)
        return

    # M6 — capability awareness: when MCP servers are configured & enabled,
    # tell the classifier tools are in play (nudges agent-task levels up).
    try:
        from .mcp import is_enabled as _mcp_enabled

        wants_mcp = _mcp_enabled()
    except Exception:  # noqa: BLE001 — routing must work without MCP config
        wants_mcp = False

    try:
        result: RouteResult = route_prompt(prompt, mode=mode, use_llm=True, wants_mcp=wants_mcp)
    except LLMClassifyError as e:
        err_console.print(
            f"[red]LLM 路由分级失败：[/red]{e}\n"
            "[yellow]提示：mbridge route 现在用最低层 (tiny) 模型做分类。"
            "确认 routing.levels.tiny（或 default_model）指向一个可达模型，"
            "并已配置好 API key / 本地服务。[/yellow]"
        )
        raise typer.Exit(1)
    _print_route_result(result, verbose=verbose)

    if explain and not verbose:
        # Verbose mode already includes the trace table inside _print_route_result.
        reasons_table = Table(title="classifier reasons", show_lines=False)
        reasons_table.add_column("#", style="dim")
        reasons_table.add_column("reason")
        for i, r in enumerate(result.profile.reasons, start=1):
            reasons_table.add_row(str(i), r)
        console.print(reasons_table)

        chain_table = Table(title="fallback chain", show_lines=False)
        chain_table.add_column("level")
        chain_table.add_column("configured model")
        chain_table.add_column("status")
        for lvl, mdl, status in result.fallback.chain:
            chain_table.add_row(
                lvl.value if lvl is not None else "(default)",
                mdl or "[dim]·[/dim]",
                ("[green]" + status + "[/green]") if status == "OK" else status,
            )
        console.print(chain_table)

    if not result.chosen_model:
        err_console.print(
            "[red]未能解析到任何模型。请在 config.yaml 配置 routing.levels，"
            "或确保至少有一个模型在 models.yaml 中。[/red]"
        )
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------

@usage_app.command("cost")
def cmd_cost_estimate(
    prompt: str = typer.Argument(..., help="要估算的 prompt。"),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help="指定模型名 (默认对每个已注册模型分别估算)。",
    ),
    expected_output: Optional[int] = typer.Option(
        None, "--expected-output",
        help="预估输出 token 数 (默认按模型 extra.max_tokens / 1024 取小)。",
    ),
) -> None:
    """估算一次请求的成本 (基于本地 token 估计 + 价格表，不实际调用)。"""
    mf = load_models_file()
    targets: list[ModelEntry] = []
    if model:
        m = find_model(model)
        if m is None:
            err_console.print(f"[red]模型 '{model}' 不在 models.yaml。[/red]")
            raise typer.Exit(code=2)
        targets = [m]
    else:
        if not mf.models:
            err_console.print(
                "[yellow]models.yaml 中没有模型。请先 `mbridge model init`。[/yellow]"
            )
            raise typer.Exit(code=2)
        targets = list(mf.models)

    table = Table(title=f"cost estimate · {estimate_tokens(prompt)} input tokens", show_lines=False)
    table.add_column("model", style="bold cyan")
    table.add_column("provider")
    table.add_column("in tok")
    table.add_column("out tok")
    table.add_column("rate (in/out per 1M)")
    table.add_column("cost", justify="right")
    table.add_column("source", style="dim")

    rows = 0
    for entry in targets:
        try:
            est: CostEstimate = estimate_cost(
                entry, prompt=prompt, expected_output_tokens=expected_output
            )
            table.add_row(
                entry.name,
                entry.provider.value,
                str(est.input_tokens),
                str(est.output_tokens),
                f"{est.pricing.input_per_1m}/{est.pricing.output_per_1m}",
                f"{est.cost:.4f} {est.currency}",
                est.pricing.source,
            )
            rows += 1
        except PricingNotFound as e:
            table.add_row(
                entry.name,
                entry.provider.value,
                "-", "-", "-",
                "[red]N/A[/red]",
                f"[red]{e}[/red]",
            )
    console.print(table)
    if rows == 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

@_usage_cache_app.command("stats")
def cmd_cache_stats(
    project: Optional[Path] = typer.Option(
        None, "--project", "-p",
        help="项目目录 — 给了就和当前 prompt 比对，告诉你跟上次相比哪一段漂了。",
    ),
) -> None:
    """显示上下文缓存命中统计 + prefix 稳定性诊断。"""
    cfg = load_app_config()
    s = load_cache_stats()
    lines = [
        f"strategy             : {s.strategy}",
        f"enabled (config)     : {cfg.cache.enabled}",
        "",
        "[bold]provider cache (per token)[/bold]",
        f"  hits               : {s.hits}",
        f"  misses             : {s.misses}",
        f"  total              : {s.total}",
        f"  hit_rate           : {s.hit_rate * 100:.1f} %",
        f"  saved_tokens       : {s.saved_tokens:,}",
        f"  estimated_savings  : {s.estimated_savings:.4f} {s.currency}",
        "",
        "[bold]stable prefix stability[/bold]",
        f"  observations       : {s.prefix_observations}",
        f"  drifts             : {s.prefix_drift_count}",
        f"  prefix_stability   : {s.prefix_stability * 100:.1f} %",
        f"  last prefix_hash   : {s.last_prefix_hash or '(none yet)'}",
        f"  last observed at   : {s.last_prefix_observed_at or '(never)'}",
    ]

    # If --project is given, build a fresh prompt and compare each section
    # hash against the last persisted observation — gives a precise
    # "this is what changed" report without the user having to run
    # ``prompt diff`` manually.
    if project is not None and s.last_section_hashes:
        try:
            from .prompt import PromptBuilder
            builder = PromptBuilder().with_project(project).with_user_request(
                "<NEXT_USER_REQUEST>"
            )
            try:
                from .project import scan_project_cached
                summary, _ = scan_project_cached(project)
                builder = builder.with_project_summary(
                    summary.to_markdown(),
                    file_tree_hash=summary.file_tree_hash,
                )
            except Exception:  # noqa: BLE001
                pass
            current = builder.build()
            drifted: list[str] = []
            for name, h in current.section_hashes.items():
                prev = s.last_section_hashes.get(name, "")
                if name not in PREFIX_SECTIONS:
                    continue  # only show drift for sections that affect cache
                if prev and prev != h:
                    drifted.append(f"{name}  ({prev} → {h})")
            lines.append("")
            lines.append(f"[bold]now vs last (project={project.resolve()})[/bold]")
            lines.append(f"  current prefix_hash: {current.stable_prefix_hash}")
            if current.stable_prefix_hash == s.last_prefix_hash:
                lines.append("  [green]✓ prefix matches last observation — provider cache should hit[/green]")
            elif drifted:
                lines.append("  [yellow]prefix drifted — these sections changed:[/yellow]")
                for d in drifted:
                    lines.append(f"    · {d}")
            else:
                lines.append("  [yellow]prefix drifted but no PREFIX_SECTIONS changed —[/yellow]")
                lines.append("  [yellow](possibly empty-vs-nonempty section flip or last data was incomplete)[/yellow]")
        except Exception as e:  # noqa: BLE001
            lines.append("")
            lines.append(f"[red]diagnostic failed: {e}[/red]")

    if s.total == 0:
        lines.append("")
        lines.append(
            "[dim]hits/misses 为 0 是因为 provider 真实 cache stats 还未接入；"
            "prefix_stability 仍可读 —— 它告诉你 prompt 前缀是否稳定。[/dim]"
        )
    console.print(Panel("\n".join(lines), title="cache stats", border_style="cyan"))


@_usage_cache_app.command("reset", hidden=True)
def cmd_cache_reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认。"),
) -> None:
    """清零缓存统计。"""
    if not yes and not Confirm.ask("确认清零缓存统计?", default=False):
        raise typer.Exit(code=0)
    s = reset_cache_stats()
    console.print(f"[green]✓[/green] 缓存统计已重置 (strategy={s.strategy})。")


@_usage_cache_app.command("clean", hidden=True)
def cmd_cache_clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认。"),
) -> None:
    """`cache reset` 的别名 — 清零缓存统计。"""
    cmd_cache_reset(yes=yes)


# ---------------------------------------------------------------------------
# R2a: deprecated aliases (old paths → new canonical paths under `usage`)
# ---------------------------------------------------------------------------
# The impl functions are now canonically registered under usage_app /
# _usage_cache_app (decorators above). The old apps (cost_app / cache_app)
# only keep hidden deprecating aliases.

# IA v1.2 cleanup: `mbridge cost estimate` / `mbridge cache stats|reset|clean`
# aliases are GONE — use `mbridge usage cost` and `mbridge usage cache ...`.


# ---------------------------------------------------------------------------
# profile (named bundles of default_model + routing.levels)
# ---------------------------------------------------------------------------

_LEVEL_KEYS = ["tiny", "cheap", "coder", "agent", "expert"]


def _ensure_models_available() -> list[ModelEntry]:
    try:
        mf = load_models_file()
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    if not mf.models:
        err_console.print(
            "[red]models.yaml 中还没有模型。先 `mbridge model init` 添加再创建 profile。[/red]"
        )
        raise typer.Exit(code=2)
    return list(mf.models)


def _prompt_level_model(level: str, model_names: list[str], current: str | None) -> str | None:
    """Prompt the user to pick a model for one routing level. '' = leave empty."""
    choices = ["-"] + model_names  # '-' means leave empty
    default = current if current in model_names else "-"
    console.print(f"[dim]可选：{', '.join(model_names)}  (输入 - 表示该 level 留空)[/dim]")
    pick = Prompt.ask(
        f"[bold]{level}[/bold] 用哪个模型",
        choices=choices,
        default=default,
        show_choices=False,
    ).strip()
    if pick == "-" or not pick:
        return None
    return pick


@profile_app.command("add")
def cmd_profile_add(
    name: str = typer.Argument(..., help="profile 名称 (例: daily / cheap / coder)。"),
    from_active: bool = typer.Option(
        False, "--from-active",
        help="以当前激活 profile (或顶层 default_model / routing.levels) 为初始默认值。",
    ),
) -> None:
    """交互式创建 / 更新一个 profile。"""
    name = name.strip()
    if not name:
        err_console.print("[red]profile 名称不能为空。[/red]")
        raise typer.Exit(code=2)

    models = _ensure_models_available()
    model_names = [m.name for m in models]

    existing = find_profile(name)
    if existing is not None:
        if not Confirm.ask(f"profile '{name}' 已存在，覆盖?", default=False):
            raise typer.Exit(code=0)

    # Initial defaults: existing profile > active/top-level config > first model
    try:
        cfg = load_app_config()
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    if existing is not None:
        seed_default = existing.default_model
        seed_levels = existing.levels.model_dump()
    elif from_active:
        seed_default = cfg.default_model
        seed_levels = cfg.routing.levels.model_dump()
    else:
        seed_default = cfg.default_model if cfg.default_model in model_names else model_names[0]
        seed_levels = {k: None for k in _LEVEL_KEYS}

    console.print(
        Panel.fit(
            f"创建 profile [bold]{name}[/bold]\n"
            f"可用模型: {', '.join(model_names)}",
            title="mbridge profile add",
            border_style="cyan",
        )
    )

    default_default = seed_default if seed_default in model_names else model_names[0]
    default_model = Prompt.ask(
        "[bold]default_model[/bold] (空 prompt / chat / REPL 都用它)",
        choices=model_names,
        default=default_default,
        show_choices=False,
    ).strip()

    console.print("\n[bold]路由 levels 映射[/bold] (回车保留默认，输入 - 表示该 level 留空)：")
    levels_kwargs: dict[str, str | None] = {}
    for lvl in _LEVEL_KEYS:
        levels_kwargs[lvl] = _prompt_level_model(lvl, model_names, seed_levels.get(lvl))

    profile = ProfileEntry(
        default_model=default_model,
        levels=RoutingLevels(**levels_kwargs),
    )

    try:
        replaced = upsert_profile(name, profile)
    except (ConfigError, OSError) as e:
        err_console.print(f"[red]保存失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    verb = "更新" if replaced else "添加"
    console.print(f"\n[green]✓[/green] 已{verb} profile [bold]{name}[/bold]。")
    console.print(
        "下一步：[bold]mbridge profile use " + name + "[/bold] 切换到该配置。"
    )


@profile_app.command("list")
def cmd_profile_list() -> None:
    """列出全部 profile，标记当前激活。"""
    profiles, active = list_profiles()
    if not profiles:
        console.print(
            "[yellow]尚未配置任何 profile。运行 `mbridge profile add <name>` 创建。[/yellow]"
        )
        return

    table = Table(title=f"profiles ({len(profiles)})", show_lines=False)
    table.add_column("active", justify="center")
    table.add_column("name", style="bold cyan")
    table.add_column("default_model")
    for lvl in _LEVEL_KEYS:
        table.add_column(lvl)

    for pname, p in profiles.items():
        levels = p.levels.model_dump()
        table.add_row(
            "[green]●[/green]" if pname == active else "",
            pname,
            p.default_model or "[dim]·[/dim]",
            *[(levels.get(lvl) or "[dim]·[/dim]") for lvl in _LEVEL_KEYS],
        )
    console.print(table)
    if active is None:
        console.print(
            "[dim]当前没有激活的 profile。`mbridge profile use <name>` 激活。[/dim]"
        )


@profile_app.command("use")
def cmd_profile_use(
    name: str = typer.Argument(..., help="要激活的 profile 名。"),
) -> None:
    """切换激活的 profile，把它的内容写入顶层 default_model / routing.levels。"""
    try:
        profile = activate_profile(name)
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    except OSError as e:
        err_console.print(f"[red]保存失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    levels = profile.levels.model_dump()
    levels_desc = ", ".join(
        f"{lvl}={levels[lvl]}" for lvl in _LEVEL_KEYS if levels.get(lvl)
    ) or "(无 level 映射)"
    console.print(
        Panel.fit(
            f"已切换到 profile [bold]{name}[/bold]\n"
            f"default_model: [bold green]{profile.default_model or '(未设置)'}[/bold green]\n"
            f"levels       : {levels_desc}",
            title="✓ ok",
            border_style="green",
        )
    )


@profile_app.command("show")
def cmd_profile_show(
    name: Optional[str] = typer.Argument(
        None, help="要查看的 profile 名 (留空 = 当前激活的)。",
    ),
) -> None:
    """查看一个 profile 的详细内容。"""
    profiles, active = list_profiles()
    target = name or active
    if target is None:
        err_console.print(
            "[red]没有激活的 profile，请显式传入名称：`mbridge profile show <name>`。[/red]"
        )
        raise typer.Exit(code=2)
    profile = profiles.get(target)
    if profile is None:
        err_console.print(
            f"[red]profile '{target}' 不存在。可用：{', '.join(profiles) or '(无)'}[/red]"
        )
        raise typer.Exit(code=2)

    levels = profile.levels.model_dump()
    lines = [
        f"name          : {target}" + ("  [green](active)[/green]" if target == active else ""),
        f"default_model : {profile.default_model or '[dim]·[/dim]'}",
        "levels        :",
    ]
    for lvl in _LEVEL_KEYS:
        lines.append(f"  {lvl:<7s} : {levels.get(lvl) or '[dim]·[/dim]'}")
    console.print(Panel("\n".join(lines), title="profile", border_style="cyan"))


@profile_app.command("remove")
def cmd_profile_remove(
    name: str = typer.Argument(..., help="要删除的 profile 名。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认。"),
) -> None:
    """删除一个 profile (不能删当前激活的)。"""
    if not yes and not Confirm.ask(f"确认删除 profile '{name}' ?", default=False):
        raise typer.Exit(code=0)
    try:
        ok = remove_profile(name)
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    except OSError as e:
        err_console.print(f"[red]保存失败：{e}[/red]")
        raise typer.Exit(code=1) from e
    if ok:
        console.print(f"[green]✓[/green] 已删除 profile '{name}'。")
    else:
        err_console.print(f"[yellow]profile '{name}' 不存在。[/yellow]")
        raise typer.Exit(code=2)


# IA v1.2 cleanup: `mbridge profile add|list|use|show|remove` aliases are GONE —
# use `mbridge config profile add|list|use|show|remove`.


# ---------------------------------------------------------------------------
# config — show / upgrade
# ---------------------------------------------------------------------------

config_app = typer.Typer(
    name="config",
    help="查看 / 升级 config.yaml (show / upgrade)。",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")
# R2b: profile is the canonical home under config
config_app.add_typer(profile_app, name="profile")


@config_app.command("show")
def cmd_config_show() -> None:
    """打印 config.yaml 的完整内容。"""
    try:
        cfg = load_app_config()
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    import yaml as _yaml

    body = _yaml.safe_dump(
        cfg.model_dump(mode="json"),
        allow_unicode=True, sort_keys=False, default_flow_style=False,
    )
    console.print(Panel(body, title=str(get_config_path()), border_style="cyan"))


@config_app.command("upgrade")
def cmd_config_upgrade() -> None:
    """把 config.yaml 缺失的字段补齐 (重新写回带默认值的版本)。"""
    path = get_config_path()
    if not path.exists():
        err_console.print(
            "[red]config.yaml 不存在。先运行 `mbridge init`。[/red]"
        )
        raise typer.Exit(code=2)

    try:
        cfg = load_app_config()
    except ConfigError as e:
        err_console.print(f"[red]config.yaml 无法解析：{e}[/red]")
        raise typer.Exit(code=2) from e

    try:
        save_app_config(cfg)
    except OSError as e:
        err_console.print(f"[red]写入失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(
        f"[green]✓[/green] config.yaml 已升级 (缺失字段已补齐, "
        f"schema_version={cfg.schema_version})：{path}"
    )


# ---------------------------------------------------------------------------
# edit / patch (phase 6)
# ---------------------------------------------------------------------------


def _render_diff_panel(diff_text: str, *, title: str = "diff") -> None:
    """Render a unified diff with colour-coded +/- lines."""
    if not diff_text.strip():
        console.print(Panel("[dim](empty diff)[/dim]", title=title, border_style="dim"))
        return
    lines: list[str] = []
    for raw in diff_text.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            lines.append(f"[bold]{raw}[/bold]")
        elif raw.startswith("@@"):
            lines.append(f"[cyan]{raw}[/cyan]")
        elif raw.startswith("+"):
            lines.append(f"[green]{raw}[/green]")
        elif raw.startswith("-"):
            lines.append(f"[red]{raw}[/red]")
        else:
            lines.append(f"[dim]{raw}[/dim]")
    console.print(Panel("\n".join(lines), title=title, border_style="cyan"))


def _check_diff_safety(parsed: ParsedDiff, project_root: Path) -> list[SafetyVerdict]:
    paths: list[str] = []
    for fd in parsed.files:
        if fd.is_creation:
            paths.append(fd.new_path)
        elif fd.is_deletion:
            paths.append(fd.old_path)
        else:
            paths.append(fd.new_path)
    return guard_paths(paths, project_root)


def _print_safety_table(verdicts: list[SafetyVerdict]) -> bool:
    """Return True if every path is safe."""
    table = Table(title="安全检查", show_lines=False)
    table.add_column("path", style="bold")
    table.add_column("verdict")
    table.add_column("reason", overflow="fold")
    all_ok = True
    for v in verdicts:
        if v.ok:
            table.add_row(v.path, "[green]ok[/green]", "")
        else:
            all_ok = False
            table.add_row(v.path, "[red]REFUSED[/red]", v.reason)
    console.print(table)
    return all_ok


def _print_apply_result(result: ApplyResult) -> None:
    table = Table(title=f"apply result (dry_run={result.dry_run})", show_lines=False)
    table.add_column("path", style="bold")
    table.add_column("op")
    table.add_column("status")
    table.add_column("hunks")
    table.add_column("reason", overflow="fold")
    for f in result.files:
        if f.status == "ok":
            status = "[green]ok[/green]"
        elif f.status == "failed":
            status = "[red]failed[/red]"
        else:
            status = f.status
        table.add_row(
            f.path, f.operation, status,
            f"{f.hunks_applied}/{f.hunks_total}" if f.hunks_total else "-",
            f.reason,
        )
    console.print(table)


def _save_generated_patch(project_root: Path, patch_text: str) -> Path:
    """Persist a freshly generated patch under .modelbridge/patches/<ts>.patch."""
    patches_dir = project_root / ".modelbridge" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d_%H%M%S")
    target = patches_dir / f"{ts}.patch"
    suffix = 1
    while target.exists():
        target = patches_dir / f"{ts}_{suffix}.patch"
        suffix += 1
    target.write_text(patch_text, encoding="utf-8")
    return target


def _gather_originals(
    parsed: ParsedDiff, project_root: Path,
) -> dict[str, str | None]:
    """Return {rel_path: original_content_or_None} for every file in the diff."""
    out: dict[str, str | None] = {}
    for fd in parsed.files:
        rel = fd.effective_path
        if fd.is_creation:
            out[rel] = None  # marker: file did not exist
            continue
        path = project_root / rel
        if path.is_file():
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except OSError:
                out[rel] = ""
        else:
            out[rel] = ""
    return out


@app.command(
    "edit",
    help=(
        "让模型生成 unified diff 修改项目代码 (不直接改文件，按 diff 走 review→apply→backup→rollback 链路)。\n"
        "加 --undo 可回滚上一次应用，无需 request 参数。"
    ),
)
def cmd_edit(
    request: Optional[str] = typer.Argument(None, help="自然语言描述你想做的修改（--undo 时可省略）。"),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="项目目录 (默认当前目录)。",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="模型名 (默认 config.yaml default_model)。",
    ),
    timeout: float = typer.Option(120.0, "--timeout", help="请求超时秒数。"),
    max_context: int = typer.Option(
        DEFAULT_MAX_CONTEXT_CHARS, "--max-context",
        help="prompt 总字符上限，超出时截断次要文件。",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="只生成 + 展示 diff，不写文件、不备份。",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="跳过最终确认，但安全检查与备份照常进行。",
    ),
    show_files: bool = typer.Option(
        False, "--show-files",
        help="额外展示选中文件列表。",
    ),
    save_raw: bool = typer.Option(False, "--save-raw", help="保存模型原始响应。"),
    undo: bool = typer.Option(False, "--undo", help="回滚上一次 edit/patch 应用（不生成新 diff）。"),
) -> None:
    """生成 → 校验 → 展示 → 确认 → 备份 → 应用。加 --undo 回滚上一次应用。"""
    project_root = project.expanduser().resolve()

    # --undo: skip diff generation and run rollback
    if undo:
        _do_rollback(project_root, yes=yes)
        return

    logger = get_logger()
    if not project_root.is_dir():
        err_console.print(f"[red]project 路径不是目录: {project_root}[/red]")
        raise typer.Exit(code=2)

    if not request:
        err_console.print("[red]请提供修改请求，或使用 --undo 回滚上一次应用。[/red]")
        raise typer.Exit(code=2)

    root_verdict = guard_project_root(project_root)
    if not root_verdict.ok:
        err_console.print(f"[red]{root_verdict.reason}[/red]")
        raise typer.Exit(code=5)

    # 1) Build prompt + run scan/select/read
    ep = build_edit_messages(request, project_root=project_root, max_context=max_context)
    if show_files:
        from .project import SelectionResult, SelectedFile
        # Reconstruct a SelectionResult for the printer (we kept only paths
        # in ep, not the full SelectedFile objects).
        sel = SelectionResult(
            files=[SelectedFile(path=p, reason="(selected)", score=0) for p in ep.selected_paths],
        )
        _print_selected_files(sel, None, ep.file_contexts)

    # 2) Pick model
    target_model = model or load_app_config().default_model
    if not target_model:
        err_console.print("[red]未指定 model 且没有 default_model。[/red]")
        raise typer.Exit(code=2)
    entry = find_model(target_model)
    if entry is None:
        err_console.print(f"[red]找不到模型 '{target_model}'。[/red]")
        raise typer.Exit(code=2)

    # 3) Call model
    provider = get_provider(entry)
    req = ChatRequest(
        model=entry.model,
        messages=ep.messages,
        temperature=(entry.extra or {}).get("temperature"),
        max_tokens=(entry.extra or {}).get("max_tokens", 4096),
    )
    console.print(
        f"[dim]edit · model={entry.name} · files={len(ep.file_contexts)} "
        f"· prefix={ep.prompt_result.prompt_prefix_hash}[/dim]"
    )
    try:
        resp = provider.chat(
            req, timeout=timeout, save_raw=save_raw, verbose_label="edit",
        )
    except ProviderError as e:
        _print_provider_error(e)
        raise typer.Exit(code=3) from e

    logger.info("edit model=%s elapsed=%dms", entry.name, resp.elapsed_ms)

    # 4) Extract diff from response
    extracted: ExtractedDiff = extract_diff(resp.content or "")
    if extracted.needs_human:
        console.print(Panel(
            extracted.extra_text or "模型认为该需求需要人工决策。",
            title="need-human-decision",
            border_style="yellow",
        ))
        raise typer.Exit(code=0)
    if not extracted.diff_text:
        err_console.print(Panel(
            f"[red]未能从模型响应中抽到 unified diff。[/red]\n\n原始响应:\n{(resp.content or '')[:800]}",
            title="extract failed", border_style="red",
        ))
        raise typer.Exit(code=4)

    # 5) Parse + safety
    try:
        parsed = parse_unified_diff(extracted.diff_text)
    except DiffParseError as e:
        err_console.print(f"[red]diff 解析失败: {e}[/red]")
        _render_diff_panel(extracted.diff_text, title="模型输出 (无效)")
        raise typer.Exit(code=4) from e

    verdicts = _check_diff_safety(parsed, project_root)
    safe = _print_safety_table(verdicts)
    if not safe:
        err_console.print(
            "[red]存在路径未通过安全检查，拒绝应用 patch。请改 prompt 或绕开这些路径再试。[/red]"
        )
        raise typer.Exit(code=5)

    # 6) Show diff
    _render_diff_panel(render_unified_diff(parsed), title="proposed diff")

    # 7) Persist the diff regardless
    patch_path = _save_generated_patch(project_root, render_unified_diff(parsed))
    console.print(f"[dim]patch saved → {patch_path}[/dim]")

    if dry_run:
        console.print("[yellow]--dry-run: 不写入任何文件。[/yellow]")
        return

    # 8) Confirm
    if not yes:
        if not Confirm.ask("确认应用此 patch?", default=False):
            console.print("[dim]已取消。[/dim]")
            return

    # 9) Backup originals BEFORE applying
    originals = _gather_originals(parsed, project_root)
    backup_rec = create_backup(
        project_root,
        user_request=request,
        patch_text=render_unified_diff(parsed),
        files_to_save=originals,
        label="edit",
    )

    # 10) Apply
    result = apply_diff(parsed, project_root=project_root, dry_run=False)
    _print_apply_result(result)

    # Record any deletions for the backup so rollback can restore them.
    if result.deleted:
        deleted_originals = {
            f.path: f.original_text or ""
            for f in result.files if f.status == "ok" and f.operation == "delete"
        }
        mark_deletions(backup_rec, result.deleted, deleted_originals)

    if result.any_failure:
        err_console.print(
            "[yellow]部分文件应用失败。已保留备份，可用 `mbridge patch rollback` 回滚已成功部分。[/yellow]"
        )
        raise typer.Exit(code=6)

    console.print(
        f"[green]✓[/green] patch applied. backup={backup_rec.dir.relative_to(project_root)}"
    )


@app.command(
    "run",
    help=(
        "在项目目录内安全执行一条 shell 命令。\n\n"
        "常用示例:\n"
        "    mbridge run pytest -x              # 跑测试\n"
        "    mbridge run \"pytest -k smoke -v\"   # 多选项 + 引号\n"
        "    mbridge run \"python script.py\"    # 跑脚本\n"
        "    mbridge run --dry-run \"npm test\"  # 仅校验 + 解析报错\n\n"
        "命令必须命中白名单 (pytest/python/npm/go/cargo …)，"
        "禁止任何 shell 元字符 (;|&> 等)。失败时自动解析 traceback / pytest / Node / 编译错误。"
    ),
)
def cmd_run(
    command: str = typer.Argument(..., help="要执行的命令，例如 \"pytest -x\"。"),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="项目目录 (默认当前目录)。",
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", help="超时秒数 (默认 30, 最大 600)。",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="只校验命令白名单与项目路径安全，不实际执行。",
    ),
) -> None:
    """Phase-7 子集 A：安全执行 + 错误解析。

    退出码：
    * 0 / 命令本身的退出码 — 透传，方便和 shell 串联
    * 2 — project 路径非目录
    * 4 — 命令被 :class:`CommandPolicy` 拒绝
    * 5 — project 不在 ``security.allowed_project_dirs`` 内
    """
    project_root = project.expanduser().resolve()
    if not project_root.is_dir():
        err_console.print(f"[red]project 路径不是目录: {project_root}[/red]")
        raise typer.Exit(code=2)

    root_verdict = guard_project_root(project_root)
    if not root_verdict.ok:
        err_console.print(f"[red]{root_verdict.reason}[/red]")
        raise typer.Exit(code=5)

    try:
        policy = CommandPolicy.from_config()
        policy.validate(command)
    except CommandRejected as e:
        err_console.print(f"[red]命令被拒绝：{e.reason}[/red]")
        raise typer.Exit(code=4) from e

    timeout = max(1.0, min(timeout, 600.0))

    if dry_run:
        console.print(Panel.fit(
            f"command : {command}\n"
            f"cwd     : {project_root}\n"
            f"timeout : {timeout:.0f}s\n"
            "[green]✓ 通过白名单与路径安全校验，未执行 (--dry-run)。[/green]",
            title="dry-run", border_style="yellow",
        ))
        return

    console.print(
        f"[dim]run · cwd={project_root} · timeout={timeout:.0f}s[/dim]"
    )
    result = run_command(command, cwd=project_root, timeout=timeout)

    if result.stdout:
        console.print(result.stdout.rstrip())
    if result.stderr:
        err_console.print(result.stderr.rstrip())

    status_color = "green" if result.exit_code == 0 else "red"
    trunc = ", truncated" if result.truncated else ""
    tmo = ", timeout" if result.timed_out else ""
    console.print(
        f"\\[exit={result.exit_code}, {result.duration_ms}ms{trunc}{tmo}]",
        style=status_color,
    )

    if result.exit_code != 0:
        errors = parse_output(result)
        if errors:
            _print_parsed_errors(errors)

    raise typer.Exit(code=result.exit_code if result.exit_code >= 0 else 1)


def _print_parsed_errors(errors: list[ParsedError], *, limit: int = 10) -> None:
    """Render up to ``limit`` :class:`ParsedError` rows as a rich table."""
    table = Table(title="解析到的错误", show_lines=False, header_style="bold")
    table.add_column("type", style="cyan", no_wrap=True)
    table.add_column("location")
    table.add_column("message", overflow="fold")
    for err in errors[:limit]:
        loc = ""
        if err.file:
            loc = err.file + (f":{err.line}" if err.line is not None else "")
        table.add_row(err.type, loc, err.message)
    console.print(table)
    if len(errors) > limit:
        console.print(f"[dim]... 还有 {len(errors) - limit} 条未展示[/dim]")


@patch_app.command("preview", hidden=True)
def cmd_patch_preview(
    patch_file: Path = typer.Argument(..., help="要预览的 .patch / .diff 文件。"),
    project: Path = typer.Option(
        Path("."), "--project", "-p",
        help="项目目录 — 用于安全检查 (路径合法性)。",
    ),
) -> None:
    """解析并展示一份 patch，包含安全检查与文件列表，但不应用。"""
    if not patch_file.is_file():
        err_console.print(f"[red]找不到 patch 文件: {patch_file}[/red]")
        raise typer.Exit(code=2)
    text = patch_file.read_text(encoding="utf-8")
    try:
        parsed = parse_unified_diff(text)
    except DiffParseError as e:
        err_console.print(f"[red]diff 解析失败: {e}[/red]")
        _render_diff_panel(text, title=str(patch_file))
        raise typer.Exit(code=4) from e

    table = Table(title="patch 中的文件", show_lines=False)
    table.add_column("#", style="dim")
    table.add_column("path", style="bold")
    table.add_column("operation")
    table.add_column("hunks")
    for i, fd in enumerate(parsed.files, start=1):
        if fd.is_creation:
            op = "[green]create[/green]"
        elif fd.is_deletion:
            op = "[red]delete[/red]"
        else:
            op = "modify"
        table.add_row(str(i), fd.effective_path, op, str(len(fd.hunks)))
    console.print(table)

    project_root = project.expanduser().resolve()
    verdicts = _check_diff_safety(parsed, project_root)
    safe = _print_safety_table(verdicts)
    if not safe:
        console.print("[yellow]风险提示：上述路径未通过安全检查，patch apply 会被拒绝。[/yellow]")

    _render_diff_panel(render_unified_diff(parsed), title=str(patch_file.name))


@patch_app.command("apply", hidden=True)
def cmd_patch_apply(
    patch_file: Path = typer.Argument(..., help="要应用的 .patch / .diff 文件。"),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="项目目录 (默认当前目录)。",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过最终确认。"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="只演练匹配过程，不真正写文件。",
    ),
) -> None:
    """从磁盘上的 patch 文件应用一段已存在的 diff。"""
    if not patch_file.is_file():
        err_console.print(f"[red]找不到 patch 文件: {patch_file}[/red]")
        raise typer.Exit(code=2)
    text = patch_file.read_text(encoding="utf-8")
    project_root = project.expanduser().resolve()
    if not project_root.is_dir():
        err_console.print(f"[red]project 路径不是目录: {project_root}[/red]")
        raise typer.Exit(code=2)

    root_verdict = guard_project_root(project_root)
    if not root_verdict.ok:
        err_console.print(f"[red]{root_verdict.reason}[/red]")
        raise typer.Exit(code=5)

    try:
        parsed = parse_unified_diff(text)
    except DiffParseError as e:
        err_console.print(f"[red]diff 解析失败: {e}[/red]")
        raise typer.Exit(code=4) from e

    verdicts = _check_diff_safety(parsed, project_root)
    if not _print_safety_table(verdicts):
        err_console.print("[red]存在路径未通过安全检查，拒绝应用。[/red]")
        raise typer.Exit(code=5)

    _render_diff_panel(render_unified_diff(parsed), title=str(patch_file.name))

    if dry_run:
        result = apply_diff(parsed, project_root=project_root, dry_run=True)
        _print_apply_result(result)
        console.print("[yellow]--dry-run: 未实际写入。[/yellow]")
        return

    if not yes:
        if not Confirm.ask("确认应用此 patch?", default=False):
            console.print("[dim]已取消。[/dim]")
            return

    originals = _gather_originals(parsed, project_root)
    backup_rec = create_backup(
        project_root,
        user_request=f"patch apply {patch_file.name}",
        patch_text=text,
        files_to_save=originals,
        label="apply",
    )
    result = apply_diff(parsed, project_root=project_root, dry_run=False)
    _print_apply_result(result)
    if result.deleted:
        deleted_originals = {
            f.path: f.original_text or ""
            for f in result.files if f.status == "ok" and f.operation == "delete"
        }
        mark_deletions(backup_rec, result.deleted, deleted_originals)

    if result.any_failure:
        err_console.print(
            "[yellow]部分文件应用失败。已保留备份，可用 `mbridge patch rollback` 回滚已成功部分。[/yellow]"
        )
        raise typer.Exit(code=6)

    console.print(
        f"[green]✓[/green] patch applied. backup={backup_rec.dir.relative_to(project_root)}"
    )


def _do_rollback(project_root: Path, yes: bool = False) -> None:
    """Shared rollback logic used by ``patch rollback`` and ``edit --undo``."""
    root_verdict = guard_project_root(project_root)
    if not root_verdict.ok:
        err_console.print(f"[red]{root_verdict.reason}[/red]")
        raise typer.Exit(code=5)

    record = latest_backup(project_root)
    if record is None:
        console.print("[yellow]没有可回滚的 backup。[/yellow]")
        raise typer.Exit(code=0)

    console.print(Panel.fit(
        f"timestamp    : {record.timestamp}\n"
        f"user_request : {record.user_request[:120]}\n"
        f"modified     : {record.modified}\n"
        f"created      : {record.created}\n"
        f"deleted      : {record.deleted}",
        title=f"待回滚: {record.dir.name}",
        border_style="yellow",
    ))
    if not yes and not Confirm.ask("确认回滚?", default=False):
        console.print("[dim]已取消。[/dim]")
        return

    rb = patch_rollback(project_root)
    if rb.backup is None:
        console.print("[yellow]没有可回滚的 backup。[/yellow]")
        return

    table = Table(title="rollback 结果", show_lines=False)
    table.add_column("kind")
    table.add_column("paths", overflow="fold")
    if rb.restored:
        table.add_row("[green]restored[/green]", "\n".join(rb.restored))
    if rb.re_deleted:
        table.add_row("[blue]re_deleted[/blue]", "\n".join(rb.re_deleted))
    if rb.failures:
        table.add_row(
            "[red]failed[/red]",
            "\n".join(f"{p}: {r}" for p, r in rb.failures),
        )
    console.print(table)
    if rb.failures:
        raise typer.Exit(code=6)
    console.print(f"[green]✓[/green] 已回滚 backup {rb.backup.dir.name}")


@patch_app.command("rollback", hidden=True)
def cmd_patch_rollback(
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="项目目录 (默认当前目录)。",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认。"),
) -> None:
    """回滚最近一次 patch 应用。"""
    _do_rollback(project.expanduser().resolve(), yes=yes)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def _print_provider_error(e: ProviderError) -> None:
    lines = [f"[red]✗ {e.message}[/red]"]
    if e.provider:
        lines.append(f"[dim]provider   = {e.provider}[/dim]")
    if e.status_code is not None:
        lines.append(f"[dim]status_code = {e.status_code}[/dim]")
    if e.error_type:
        lines.append(f"[dim]error_type = {e.error_type}[/dim]")
    if e.hint:
        lines.append(f"[yellow]hint:[/yellow] {e.hint}")
    if e.raw:
        raw_str = e.raw if isinstance(e.raw, str) else str(e.raw)
        snippet = raw_str if len(raw_str) <= 400 else raw_str[:400] + "…"
        lines.append(f"[dim]raw:[/dim] {snippet}")
    err_console.print(Panel("\n".join(lines), title="provider error", border_style="red"))


# ===========================================================================
# Phase-4: prompt + project
# ===========================================================================


def _print_prompt_assembly(result: PromptBuildResult) -> None:
    """Pretty-print a :class:`PromptBuildResult` (used by --show-prompt)."""
    console.print(Panel.fit(
        f"prefix_hash = [bold]{result.prompt_prefix_hash}[/bold]   "
        f"rules_hash = {result.rules_hash}   "
        f"summary_hash = {result.project_summary_hash}\n"
        f"total_chars = {result.total_chars}   "
        f"truncated = {result.truncated}",
        title="prompt assembly", border_style="cyan",
    ))
    table = Table(title="sections", show_lines=False)
    table.add_column("#", style="dim")
    table.add_column("section", style="bold")
    table.add_column("chars")
    table.add_column("sources", overflow="fold")
    table.add_column("preview", overflow="fold")
    for i, (name, chars, head) in enumerate(result.section_summary(), start=1):
        srcs = ", ".join(result.sources.get(name, [])) or "[dim]·[/dim]"
        preview = head if len(head) <= 80 else head[:80] + "…"
        table.add_row(str(i), name, str(chars), srcs, preview)
    console.print(table)
    if result.warnings:
        console.print("[yellow]warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  · {w}")


def _print_selected_files(
    selection: SelectionResult,
    plan: Optional[ContextPlan],
    file_contexts: list[FileContext],
) -> None:
    """Pretty-print Phase-5 file selection + budget outcome."""
    topic_str = ", ".join(selection.topics) if selection.topics else "(none)"
    token_str = ", ".join(selection.query_tokens) if selection.query_tokens else "(none)"
    console.print(Panel.fit(
        f"topics fired : {topic_str}\n"
        f"query tokens : {token_str}\n"
        f"selected     : {len(selection.files)} file(s)",
        title="selected files", border_style="magenta",
    ))

    if not selection.files:
        return

    table = Table(show_lines=False)
    table.add_column("#", style="dim")
    table.add_column("path", style="bold cyan", overflow="fold")
    table.add_column("score", justify="right")
    table.add_column("lines", justify="right")
    table.add_column("chars", justify="right")
    table.add_column("status")
    table.add_column("reason", overflow="fold")

    # Index by path for fast lookup
    fc_by_path = {fc.path: fc for fc in file_contexts}
    plan_kept = {fc.path for fc in (plan.kept_files if plan else [])}
    plan_trunc = set(plan.truncated_files if plan else [])
    plan_dropped = set(plan.dropped_files if plan else [])

    for i, sf in enumerate(selection.files, start=1):
        fc = fc_by_path.get(sf.path)
        if sf.path in plan_dropped:
            status = "[red]dropped (budget)[/red]"
        elif sf.path in plan_trunc:
            status = "[yellow]truncated (budget)[/yellow]"
        elif fc and fc.skipped_reason:
            status = f"[yellow]skipped: {fc.skipped_reason}[/yellow]"
        elif fc and fc.truncated:
            status = "[yellow]truncated (lines/bytes)[/yellow]"
        elif sf.path in plan_kept:
            status = "[green]kept[/green]"
        else:
            status = "[dim]·[/dim]"
        lines = str(fc.lines_read) if fc else "?"
        chars = str(fc.chars) if fc else "?"
        table.add_row(str(i), sf.path, str(sf.score), lines, chars, status, sf.reason)
    console.print(table)

    if plan is not None:
        summary = (
            f"max_chars={plan.max_chars}  overhead={plan.overhead_chars}  "
            f"files_kept={plan.fits_files_chars}"
        )
        if plan.dropped_files or plan.truncated_files:
            summary += (
                f"\n[yellow]dropped={len(plan.dropped_files)}  "
                f"truncated={len(plan.truncated_files)}  "
                "→ context truncated to fit model limits[/yellow]"
            )
        console.print(Panel.fit(summary, title="context budget", border_style="blue"))


def _system_md_path() -> Path:
    cfg = load_app_config()
    p = cfg.prompt.system_file
    return Path(p).expanduser() if p else get_app_dir() / "system.md"


def _rules_md_path() -> Path:
    cfg = load_app_config()
    p = cfg.prompt.user_rules_file
    return Path(p).expanduser() if p else get_app_dir() / "rules.md"


# ---------------------------------------------------------------------------
# mbridge prompt ...
# ---------------------------------------------------------------------------

@prompt_app.command("list")
def cmd_prompt_list(
    project: Optional[Path] = typer.Option(
        None, "--project", "-p", help="项目路径 (默认不检测项目规则)。",
    ),
) -> None:
    """列出当前生效的 prompt / 规则文件。"""
    cfg = load_app_config()
    sys_path = _system_md_path()
    rules_path = _rules_md_path()

    table = Table(title="prompt files", show_lines=False)
    table.add_column("scope", style="dim")
    table.add_column("file")
    table.add_column("status")

    table.add_row(
        "global system", str(sys_path),
        "[green]ok[/green]" if sys_path.is_file() else "[yellow]missing (built-in default)[/yellow]",
    )
    table.add_row(
        "global rules", str(rules_path),
        "[green]ok[/green]" if rules_path.is_file() else "[yellow]missing[/yellow]",
    )

    if project is not None:
        files = discover_rule_files(project)
        proj_files = [f for f in files if f.scope != "user_global"]
        if proj_files:
            for f in proj_files:
                table.add_row(
                    f"project ({f.scope})", str(f.path), "[green]ok[/green]",
                )
        else:
            table.add_row("project", str(project.resolve()), "[yellow]no rule files found[/yellow]")
    else:
        table.add_row("project", "(--project not provided)", "[dim]skipped[/dim]")
    console.print(table)

    console.print(
        f"\n[dim]config.prompt[/dim]  "
        f"use_project_rules={cfg.prompt.use_project_rules}  "
        f"use_claude_md={cfg.prompt.use_claude_md}  "
        f"use_agent_md={cfg.prompt.use_agent_md}  "
        f"max_rules_chars={cfg.prompt.max_rules_chars}"
    )


@prompt_app.command("show")
def cmd_prompt_show(
    project: Optional[Path] = typer.Option(
        None, "--project", "-p", help="项目路径 (默认不带项目规则)。",
    ),
    full: bool = typer.Option(False, "--full", help="显示每个 section 完整内容 (默认只显示摘要)。"),
) -> None:
    """显示 PromptBuilder 组装出的最终 prompt 结构。"""
    builder = PromptBuilder().with_project(project)
    if project is not None:
        summary, _ = scan_project_cached(project)
        builder = builder.with_project_summary(
            summary.to_markdown(),
            file_tree_hash=summary.file_tree_hash,
        )
    builder = builder.with_user_request("<USER_REQUEST_PLACEHOLDER>")
    result = builder.build()

    console.print(Panel.fit(
        f"prefix_hash       = [bold]{result.prompt_prefix_hash}[/bold]\n"
        f"rules_hash        = {result.rules_hash}\n"
        f"project_summary_hash = {result.project_summary_hash}\n"
        f"file_tree_hash    = {result.file_tree_hash}\n"
        f"selected_files_hash  = {result.selected_files_hash}\n"
        f"dynamic_suffix_hash  = {result.dynamic_suffix_hash}\n"
        f"total_chars       = {result.total_chars}\n"
        f"truncated         = {result.truncated}",
        title="prompt assembly", border_style="cyan",
    ))

    table = Table(title="sections (in order)", show_lines=False)
    table.add_column("#", style="dim", no_wrap=True)
    table.add_column("section", style="bold")
    table.add_column("hash", style="dim", no_wrap=True)
    table.add_column("chars")
    table.add_column("sources", overflow="fold")
    table.add_column("preview" if not full else "content", overflow="fold")
    for i, (name, chars, head) in enumerate(result.section_summary(), start=1):
        srcs = ", ".join(result.sources.get(name, [])) or "[dim]·[/dim]"
        preview = result.sections[name] if full else head
        if not full and len(preview) > 80:
            preview = preview[:80] + "…"
        h = result.section_hashes.get(name, "")
        table.add_row(str(i), name, h, str(chars), srcs, preview)
    console.print(table)

    if result.warnings:
        console.print("[yellow]warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  · {w}")


# ---------------------------------------------------------------------------
# prompt hash / diff — cache-stability diagnostics
# ---------------------------------------------------------------------------

def _build_for_hash(
    project: Optional[Path],
    query: str,
    *,
    include_files: bool = False,
) -> tuple["PromptBuildResult", Optional["ProjectSummary"], str]:
    """Shared builder used by ``prompt hash`` and ``prompt diff``.

    Returns ``(result, summary, cache_reason)``. ``summary`` is ``None``
    when no ``--project`` was supplied. ``cache_reason`` reports whether
    the summary cache was hit ("hit"), refreshed ("refreshed"), or
    bypassed ("(no project)").
    """
    builder = PromptBuilder().with_user_request(query).with_project(project)
    summary = None
    cache_reason = "(no project)"
    if project is not None:
        summary, check = scan_project_cached(project)
        builder = builder.with_project_summary(
            summary.to_markdown(),
            file_tree_hash=summary.file_tree_hash,
        )
        cache_reason = check.reason
        if include_files:
            selection = select_files(query, summary)
            file_contexts = read_files(selection.files, project_root=project)
            builder = builder.with_project_files(file_contexts)
    return builder.build(), summary, cache_reason


@project_rules_app.callback(invoke_without_command=True)
def cmd_project_rules(
    ctx: typer.Context,
    path: Path = typer.Option(Path("."), "--path", "-p", help="项目路径。"),
) -> None:
    """列出当前项目找到的规则文件 (AGENT.md / CLAUDE.md / ...)。

    不带子命令时列出规则文件；``rules init`` 生成 AGENT.md。
    """
    if ctx.invoked_subcommand is not None:
        return
    files = discover_rule_files(path)
    if not files:
        console.print(
            "[yellow]未找到任何规则文件。[/yellow]\n"
            "可以在项目根目录放 AGENT.md / CLAUDE.md / .cursorrules，"
            "或在用户全局目录 (~/.modelbridge/rules.md) 配置。"
        )
        raise typer.Exit(code=0)

    table = Table(title=f"rule files for {Path(path).resolve()}", show_lines=False)
    table.add_column("scope", style="dim")
    table.add_column("label", style="bold cyan")
    table.add_column("size")
    table.add_column("path", overflow="fold")
    for f in files:
        table.add_row(f.scope, f.label, f"{f.size} B", str(f.path))
    console.print(table)


def _do_project_init(
    path: Path,
    model: Optional[str],
    force: bool,
    yes: bool,
    timeout: float,
) -> None:
    """Shared impl for ``project rules init`` and the deprecated ``project init``."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        err_console.print(f"[red]项目路径不是目录: {root}[/red]")
        raise typer.Exit(code=2)
    target = root / "AGENT.md"
    if target.exists() and not force:
        err_console.print(
            f"[red]{target} 已存在。[/red]"
            "若要覆盖，请加 [bold]--force[/bold]。"
        )
        raise typer.Exit(code=2)

    console.print(f"[dim]扫描 {root} …[/dim]")
    try:
        result = generate_agent_md(root, model_name=model, timeout=timeout)
    except ChatError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    except ProviderError as e:
        _print_provider_error(e)
        raise typer.Exit(code=3) from e

    # Preview
    preview = result.agent_md
    if len(preview) > 1500:
        preview = preview[:1500] + "\n[... 预览截断；完整内容稍后写入文件 ...]"
    console.print(Panel(preview, title=f"AGENT.md preview ({result.model_used} · {result.elapsed_ms}ms)",
                        border_style="cyan"))

    if not yes:
        if not Confirm.ask(f"写入 {target}?", default=True):
            console.print("[yellow]已取消。[/yellow]")
            raise typer.Exit(code=0)

    wrote = write_agent_md(result, force=force)
    if wrote:
        verb = "覆盖" if result.overwrote else "创建"
        console.print(f"[green]✓[/green] 已{verb} {target} ({len(result.agent_md)} 字符)")
    else:
        err_console.print(f"[yellow]{target} 已存在且未传 --force，跳过写入。[/yellow]")
        raise typer.Exit(code=2)


@project_rules_app.command("init")
def cmd_project_rules_init(
    path: Path = typer.Option(Path("."), "--path", "-p", help="项目路径。"),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="生成 AGENT.md 用的模型名 (默认 config.default_model)。",
    ),
    force: bool = typer.Option(False, "--force", help="如果 AGENT.md 已存在则覆盖。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过预览确认。"),
    timeout: float = typer.Option(120.0, "--timeout", help="模型调用超时秒数。"),
) -> None:
    """为项目生成 ``AGENT.md`` (会调用模型)。"""
    _do_project_init(path, model, force, yes, timeout)


# R3b: `project init` is a deprecated alias for `project rules init`
def cmd_project_init(
    path: Path = typer.Option(Path("."), "--path", "-p", help="项目路径。"),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="生成 AGENT.md 用的模型名 (默认 config.default_model)。",
    ),
    force: bool = typer.Option(False, "--force", help="如果 AGENT.md 已存在则覆盖。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过预览确认。"),
    timeout: float = typer.Option(120.0, "--timeout", help="模型调用超时秒数。"),
) -> None:
    """为项目生成 ``AGENT.md`` (会调用模型)。"""
    _do_project_init(path, model, force, yes, timeout)


deprecated_alias(project_app, "init", "project rules init", cmd_project_init)


if __name__ == "__main__":  # pragma: no cover
    app()
