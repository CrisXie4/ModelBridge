"""grep tool + built-in sub-agent kinds.

* GrepTool: real filesystem sandbox — matches with path:line evidence,
  include glob, ignore_case, binary/no-match handling, path-policy denial.
* spawn_subagent kinds: built-in prompts resolve ``{goal}``, explore is
  read-only + confirmation-free, general carries write tools and forwards
  approvals to the parent, unknown kinds are rejected.
"""

from __future__ import annotations

from pathlib import Path

import modelbridge.agent.tools.subagent_tool as sub_mod
from modelbridge.agent.context import AgentContext, ApprovalDecision, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.file_tools import GrepTool
from modelbridge.agent.tools.subagent_tool import (
    AGENT_KINDS,
    DEFAULT_KIND,
    SpawnSubagentTool,
)


def _ctx(tmp_path, *, approve=auto_yes):
    policy = PathPolicy(
        allowed_dirs=[tmp_path.resolve()],
        blocked_patterns=[".env", "*.key"],
    )
    return AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=approve)


# --- grep -------------------------------------------------------------------

def _sandbox(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def main():\n    return MODEL_NAME\n", encoding="utf-8"
    )
    (tmp_path / "lib.py").write_text("MODEL_NAME = 'x'\nother = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("model_name is lowercase here\n", encoding="utf-8")
    sub = tmp_path / "node_modules" / "dep"
    sub.mkdir(parents=True)
    (sub / "dep.js").write_text("MODEL_NAME should not appear\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"MODEL_NAME\x00\x01binary")


def test_grep_finds_matches_with_path_line(tmp_path):
    _sandbox(tmp_path)
    res = GrepTool().execute({"pattern": "MODEL_NAME"}, _ctx(tmp_path))
    assert not res.is_error
    assert "app.py:2" in res.content
    assert "lib.py:1" in res.content
    # node_modules pruned, binary skipped, lowercase note not matched
    assert "dep.js" not in res.content
    assert "blob.bin" not in res.content
    assert "notes.md" not in res.content


def test_grep_include_glob_filters_files(tmp_path):
    _sandbox(tmp_path)
    res = GrepTool().execute({"pattern": "model_name", "include": "*.md"}, _ctx(tmp_path))
    assert not res.is_error
    assert "notes.md:1" in res.content
    assert ".py" not in res.content


def test_grep_ignore_case(tmp_path):
    _sandbox(tmp_path)
    res = GrepTool().execute({"pattern": "model_name", "ignore_case": True}, _ctx(tmp_path))
    assert "notes.md:1" in res.content
    assert "app.py:2" in res.content


def test_grep_no_match_is_ok_result(tmp_path):
    _sandbox(tmp_path)
    res = GrepTool().execute({"pattern": "zzz_not_there"}, _ctx(tmp_path))
    assert not res.is_error
    assert "没有匹配" in res.content


def test_grep_invalid_regex_errors(tmp_path):
    res = GrepTool().execute({"pattern": "([unclosed"}, _ctx(tmp_path))
    assert res.is_error
    assert "正则" in res.content


def test_grep_outside_allowed_dir_denied(tmp_path):
    outside = tmp_path.parent
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    ctx = AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=auto_yes)
    res = GrepTool().execute({"pattern": "x", "path": str(outside)}, ctx)
    assert res.is_error


# --- sub-agent kinds ----------------------------------------------------------

def test_kinds_have_goal_placeholder_and_toolsets():
    for kind, cfg in AGENT_KINDS.items():
        assert "{goal}" in cfg["system"], f"{kind} prompt must embed the goal"
        assert "read_file" in cfg["tools"]
        assert isinstance(cfg["default_max_iters"], int)
    assert DEFAULT_KIND in AGENT_KINDS
    # explore stays read-only; general can write
    assert "write_file" not in AGENT_KINDS["explore"]["tools"]
    assert "write_file" in AGENT_KINDS["general"]["tools"]
    # neither kind may nest sub-agents
    for cfg in AGENT_KINDS.values():
        assert "spawn_subagent" not in cfg["tools"]


def test_schema_offers_kind_enum():
    schema = SpawnSubagentTool().json_schema()
    assert schema["properties"]["kind"]["enum"] == sorted(AGENT_KINDS)
    assert schema["properties"]["kind"]["default"] == DEFAULT_KIND


def test_unknown_kind_rejected_without_confirmation(tmp_path):
    calls = []

    def recording_approve(**kw):
        calls.append(kw)
        return ApprovalDecision.YES

    ctx = _ctx(tmp_path, approve=recording_approve)
    res = SpawnSubagentTool().execute({"goal": "x", "kind": "nemesis"}, ctx)
    assert res.is_error
    assert "nemesis" in res.content
    assert calls == []  # rejected before any approval


def test_explore_kind_needs_no_confirmation(tmp_path, monkeypatch):
    """explore is read-only + policy-confined → runs without a prompt."""
    prompted = []

    def never_approve(**kw):
        prompted.append(kw)
        return ApprovalDecision.NO  # would block if asked

    ctx = _ctx(tmp_path, approve=never_approve)

    captured = {}

    def fake_run_agent_turn(*, session, ctx, registry, model_name, **kw):
        captured["registry_names"] = set(registry.names())
        captured["system"] = session.messages[0].content
        captured["ctx"] = ctx

        class _R:
            iterations = 2
            stopped_reason = "stop"
            error = None
            tool_calls_executed = []

            class _Final:
                content = "结论：找到了"
            final_response = _Final()

        return _R()

    monkeypatch.setattr(sub_mod, "run_agent_turn", fake_run_agent_turn)
    # 模型解析也必须隔离：CI 上没有 models.yaml，会落在硬编码的
    # deepseek-v4-flash 兜底名上直接报"模型不可用"，走不到 mock 的回合。
    monkeypatch.setattr(sub_mod, "get_model_entry", lambda _name: None)
    monkeypatch.setattr(sub_mod, "_default_model_name", lambda: "test-model")
    res = SpawnSubagentTool().execute({"goal": "找一下入口函数", "kind": "explore"}, ctx)
    assert not res.is_error, res.content
    assert prompted == []                      # no confirmation asked
    assert "结论：找到了" in res.content
    # read-only toolset + goal embedded in the built-in prompt
    assert captured["registry_names"] == {"read_file", "list_dir", "grep"}
    assert "找一下入口函数" in captured["system"]
    assert "探索子智能体" in captured["system"]


def test_general_kind_confirms_and_forwards_parent_approval(tmp_path, monkeypatch):
    """general asks once at spawn; its writes flow through the parent channel."""
    prompted = []

    def parent_approve(**kw):
        prompted.append(kw.get("tool"))
        return ApprovalDecision.YES

    ctx = _ctx(tmp_path, approve=parent_approve)

    captured = {}

    def fake_run_agent_turn(*, session, ctx, registry, model_name, **kw):
        captured["registry_names"] = set(registry.names())
        # Simulate the sub-agent attempting a write: its ctx.confirm must
        # route back to the parent's approve (recorded above).
        allowed = ctx.confirm(tool="write_file", summary="子任务写入", detail="d")
        captured["write_allowed"] = allowed

        class _R:
            iterations = 1
            stopped_reason = "stop"
            error = None
            tool_calls_executed = []

            class _Final:
                content = "完成"
            final_response = _Final()

        return _R()

    monkeypatch.setattr(sub_mod, "run_agent_turn", fake_run_agent_turn)
    monkeypatch.setattr(sub_mod, "get_model_entry", lambda _name: None)
    monkeypatch.setattr(sub_mod, "_default_model_name", lambda: "test-model")
    res = SpawnSubagentTool().execute(
        {"goal": "把 README 改成英文", "kind": "general"}, ctx
    )
    assert not res.is_error
    # One spawn prompt + one forwarded write prompt, both through the parent.
    assert prompted == ["spawn_subagent", "write_file"]
    assert captured["write_allowed"] is True
    assert "write_file" in captured["registry_names"]
