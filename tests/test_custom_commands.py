"""自定义 slash 命令（E6）：~/.modelbridge/commands 与 <project>/.modelbridge/commands。

覆盖发现/覆盖顺序、frontmatter、$ARGUMENTS/$1..$N 展开、分发（forward_text）
与 loop 转发、completer 补全。内建命令表零改动。
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from modelbridge.agent.commands import (
    CommandResult,
    SlashContext,
    discover_custom_commands,
    expand_custom_command,
    handle_slash,
)
from modelbridge.agent.loop import run_interactive
from modelbridge.agent.session import Session
from modelbridge.agent.slash_completer import SlashCommandCompleter
from modelbridge.agent.tools import ToolRegistry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


def _write_global(home: Path, name: str, text: str) -> None:
    d = home / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def _write_project(proj: Path, name: str, text: str) -> None:
    d = proj / ".modelbridge" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 发现与解析
# ---------------------------------------------------------------------------

def test_discover_global_and_project_project_overrides(home, tmp_path):
    _write_global(home, "deploy", "全局部署模板 $ARGUMENTS")
    proj = tmp_path / "proj"
    _write_project(proj, "deploy", "项目部署模板 $ARGUMENTS")
    _write_project(proj, "review-only-project", "审查")

    cmds = {c.name: c for c in discover_custom_commands(proj)}
    assert set(cmds) == {"deploy", "review-only-project"}
    assert cmds["deploy"].template == "项目部署模板 $ARGUMENTS"  # 项目覆盖全局
    assert cmds["deploy"].path == proj / ".modelbridge" / "commands" / "deploy.md"


def test_discover_parses_frontmatter(home):
    _write_global(home, "ship", "---\ndescription: 发布到生产\n"
                  "argument-hint: [env]\n---\n发布 $1 到 $ARGUMENTS")
    cmds = discover_custom_commands()
    assert len(cmds) == 1
    assert cmds[0].name == "ship"
    assert cmds[0].description == "发布到生产"
    assert cmds[0].argument_hint == "[env]"
    assert cmds[0].template == "发布 $1 到 $ARGUMENTS"


def test_discover_ignores_non_md_and_empty_dirs(home, tmp_path):
    _write_global(home, "notes", "模板")
    (home / "commands" / "ignored.txt").write_text("不是命令", encoding="utf-8")
    cmds = discover_custom_commands()
    assert [c.name for c in cmds] == ["notes"]
    assert discover_custom_commands(tmp_path / "nowhere") != []  # 全局不受 project 影响


# ---------------------------------------------------------------------------
# 模板展开
# ---------------------------------------------------------------------------

def _cmd(template: str):
    from modelbridge.agent.commands import CustomCommand

    return CustomCommand(name="t", description="", argument_hint="",
                         template=template, path=Path("t.md"))


def test_expand_arguments_and_positional():
    assert expand_custom_command(_cmd("部署 $1 到 $2 并确认"), ["prod", "v2"]) == \
        "部署 prod 到 v2 并确认"
    assert expand_custom_command(_cmd("发布 $ARGUMENTS"), ["a", "b"]) == "发布 a b"
    # 缺位参数 → 空串
    assert expand_custom_command(_cmd("部署 $1 到 $2"), ["prod"]) == "部署 prod 到 "


def test_expand_appends_arguments_when_no_placeholder():
    assert expand_custom_command(_cmd("跑日常巡检"), ["prod", "extra"]) == \
        "跑日常巡检\n\nUser arguments:\nprod extra"
    assert expand_custom_command(_cmd("无参数调用"), []) == "无参数调用"


# ---------------------------------------------------------------------------
# 分发与 loop 转发
# ---------------------------------------------------------------------------

def _sctx(proj: Path | None, session: Session) -> SlashContext:
    return SlashContext(
        console=Console(file=StringIO(), width=120),
        session=session, agent_ctx=None, registry=ToolRegistry(),
        model_name="m", entry=None, thinking_state={}, project_path=proj,
    )


def test_handle_slash_custom_command_forwards_expanded(home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_project(proj, "deploy", "部署 $1 并跑冒烟测试")
    live = Session(model_name="m")
    res = handle_slash("/deploy prod", _sctx(proj, live))
    assert isinstance(res, CommandResult)
    assert res.handled is False  # 交还 loop 走普通模型流程
    assert res.forward_text == "部署 prod 并跑冒烟测试"


def test_handle_slash_unknown_without_custom_still_errors(home):
    res = handle_slash("/no-such-cmd", _sctx(None, Session(model_name="m")))
    assert res.handled is True and res.forward_text is None


def test_builtin_command_wins_over_custom_same_name(home):
    _write_global(home, "help", "自定义 help 模板")
    live = Session(model_name="m")
    live.add_user("x")
    res = handle_slash("/help", _sctx(None, live))
    assert res.forward_text is None  # 内建命令照常执行（帮助表），不走自定义模板
    assert res.handled is True


def test_loop_forwards_expanded_text_to_model(home, tmp_path, monkeypatch):
    """端到端：/deploy prod → 展开模板作为用户消息进入模型请求。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_project(proj, "deploy", "部署 $1 并跑冒烟测试")

    import modelbridge.agent.loop as loop
    from modelbridge.agent.context import AgentContext, auto_yes
    from modelbridge.agent.security import PathPolicy
    from modelbridge.models import ModelEntry, ProviderType
    from modelbridge.schemas import ChatResponse

    captured: list = []

    class _Provider:
        def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
            captured.append(list(request.messages))
            return ChatResponse(content="收到", finish_reason="stop")

    monkeypatch.setattr(loop, "get_provider", lambda e: _Provider())
    monkeypatch.setattr(
        loop, "get_model_entry",
        lambda n: ModelEntry(
            name="m", provider=ProviderType.OPENAI_COMPATIBLE,
            model="m", base_url="http://x/v1",
        ),
    )
    ctx = AgentContext(policy=PathPolicy([], []), cwd=proj, approve=auto_yes)
    session = Session(model_name="m")
    session.add_system("S")

    def handler(text: str) -> CommandResult:
        return handle_slash(text, _sctx(proj, session))

    run_interactive(
        session=session, ctx=ctx, registry=ToolRegistry(), model_name="m",
        read_input=iter(["/deploy prod", "/exit"]).__next__,
        command_handler=handler,
    )

    assert captured, "展开后的命令必须触发一次模型请求"
    user_texts = [m.content for m in captured[0] if m.role == "user"]
    assert "部署 prod 并跑冒烟测试" in user_texts
    assert not any(str(t).startswith("/deploy") for t in user_texts)  # 原始 slash 不外发


# ---------------------------------------------------------------------------
# completer
# ---------------------------------------------------------------------------

def test_completer_offers_custom_commands_with_description():
    comp = SlashCommandCompleter(
        custom_commands_provider=lambda: [("deploy", "部署到生产 [env]")],
    )
    from prompt_toolkit.document import Document

    def _meta_text(meta) -> str:
        return "".join(seg for _, seg in meta)

    got = {
        c.text: _meta_text(c.display_meta)
        for c in comp.get_completions(Document("/de"), None)
    }
    assert "deploy" in got
    assert "部署到生产" in got["deploy"]
    # 内建命令照常在列。
    assert "clear" in dict(
        (c.text, c.display_meta) for c in comp.get_completions(Document("/cle"), None)
    )


def test_completer_builtin_wins_over_custom_same_name(home):
    _write_global(home, "clear", "自定义 clear")
    from prompt_toolkit.document import Document

    comp = SlashCommandCompleter(
        custom_commands_provider=lambda: [
            (c.name, c.description) for c in discover_custom_commands()
        ],
    )
    texts = [c.text for c in comp.get_completions(Document("/cle"), None)]
    assert texts.count("clear") == 1  # 内建优先，不重复出现
