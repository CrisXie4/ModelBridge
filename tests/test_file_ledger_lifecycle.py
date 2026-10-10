"""file_reads 账本的生命周期（U8 + 评审修复）。

账本必须随历史"同生共死"，否则两类错位都会伤害模型：

* ``/resume`` / ``clear_history``（/clear、/new）替换或清空历史后，旧账本
  指纹可能恰好命中 —— read_file 返回"未变化"stub，而新历史里并没有该文件
  内容，模型拿不到文件。
* 压缩成功提交后，被摘要掉的历史里可能包含 read_file 结果 —— 经
  :func:`compact_session` 的 ``on_commit`` 回调清账。

网络无关：不触发任何模型调用。
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from modelbridge.agent.commands import CommandResult, SlashContext, handle_slash
from modelbridge.agent.compact import compact_session
from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.loop import run_interactive
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.tools import ToolRegistry
from modelbridge.agent.tools.file_tools import ReadFileTool
from modelbridge.schemas import ChatMessage


def _ctx(tmp_path: Path) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=auto_yes)


def _session_with_system() -> Session:
    s = Session(model_name="m")
    s.add_system("PREFIX")
    return s


def test_resume_clears_ledger_and_read_file_resends_full(tmp_path):
    """恢复另一会话后，旧账本必须清空 —— 重复 read_file 重发全文而非 stub。"""
    p = tmp_path / "f.txt"
    p.write_text("file body\n", encoding="utf-8")
    ctx = _ctx(tmp_path)

    # 旧会话读过该文件（账本已记账）。
    r1 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert not r1.is_error and "file body" in r1.content

    # 构造一个内容不同、但历史里没有该文件的新会话存档并 /resume。
    saved = Session(model_name="m")
    saved.add_system("OLD PREFIX")
    saved.add_user("别的会话的提问")
    path = tmp_path / "s.json"
    path.write_text(json.dumps(saved.to_dict(), ensure_ascii=False), encoding="utf-8")

    live = _session_with_system()
    sctx = SlashContext(
        console=Console(file=StringIO(), width=120),
        session=live, agent_ctx=ctx, registry=ToolRegistry(),
        model_name="m", entry=None, thinking_state={},
    )
    res = handle_slash(f"/resume {path}", sctx)
    assert res.handled
    assert ctx.file_reads == {}  # 账本已随恢复清空

    r2 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert not r2.is_error
    assert "file body" in r2.content  # 重发全文，而非"未变化"stub
    assert "未变化" not in r2.content


def test_clear_history_clears_ledger(tmp_path):
    """loop 的 clear_history（/clear、/new）分支同样清账。"""
    ctx = _ctx(tmp_path)
    p = tmp_path / "f.txt"
    p.write_text("body\n", encoding="utf-8")
    assert not ReadFileTool().execute({"path": "f.txt"}, ctx).is_error
    assert ctx.file_reads  # 已记账

    session = _session_with_system()
    session.add_user("将被清掉的一轮")

    def handler(text: str) -> CommandResult:
        return CommandResult(clear_history=True) if text == "/clear" else CommandResult()

    def feed(lines):
        it = iter(lines)
        def read():
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None
        return read

    run_interactive(
        session=session,
        ctx=ctx,
        registry=ToolRegistry(),
        model_name="m",
        read_input=feed(["/clear", "/exit"]),
        command_handler=handler,
    )

    assert session.messages and all(
        m.role == "system" for m in session.messages
    )  # 历史只剩 system 前缀
    assert ctx.file_reads == {}  # 账本同步清空


def test_compact_commit_fires_on_commit_callback(tmp_path):
    """压缩成功提交触发 on_commit；跳过（无可压缩段）不触发。"""
    big = "关于项目结构的说明。" * 40
    commits: list[int] = []

    # 成功路径：摘要替换中段 → on_commit 恰好一次。
    s = Session(model_name="m")
    s.messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content=big + " u1"),
        ChatMessage(role="assistant", content=big + " a1"),
        ChatMessage(role="user", content="u2"),
        ChatMessage(role="assistant", content="a2"),
    ]
    result = compact_session(
        s, keep_turns=1, model_name="m",
        summarize_fn=lambda _run: "短摘要",
        on_commit=lambda: commits.append(1),
    )
    assert result.ok and not result.skipped
    assert commits == [1]

    # 跳过路径（历史太短）→ 不触发。
    s2 = Session(model_name="m")
    s2.add_user("只有一条")
    assert compact_session(
        s2, keep_turns=1, model_name="m",
        summarize_fn=lambda _run: "x",
        on_commit=lambda: commits.append(2),
    ).skipped
    assert commits == [1]


def test_compact_commit_callback_exception_is_swallowed():
    """回调抛异常不拖垮压缩结果（提交照常）。"""
    big = "关于项目结构的说明。" * 40
    s = Session(model_name="m")
    s.messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content=big + " u1"),
        ChatMessage(role="assistant", content=big + " a1"),
        ChatMessage(role="user", content="u2"),
        ChatMessage(role="assistant", content="a2"),
    ]

    def _boom() -> None:
        raise RuntimeError("callback exploded")

    result = compact_session(
        s, keep_turns=1, model_name="m",
        summarize_fn=lambda _run: "短摘要",
        on_commit=_boom,
    )
    assert result.ok and not result.skipped
