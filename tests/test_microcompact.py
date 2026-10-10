"""两级压缩（E4）+ 压缩分母预留输出（E5）。

* microcompact：达全量触发点的子比例即触发、只清中段最老工具结果、保留
  最近 N 条与错误结果、净省不足不动 —— 免模型前置减压。
* 真实用量锚点：resp.usage → session.metadata，压缩决策优先于字符估算。
* E5：压缩分母 = window − 输出预留（80% 触发后一次大输出不再直接顶过 100%）。

网络无关：compact_session 被 fake，计数与闸门走 maybe_auto_compact 的真实
代码路径；microcompact 直接驱动。
"""

from __future__ import annotations

import modelbridge.agent.compact as compact
import modelbridge.config as config_mod
from modelbridge.agent.compact import (
    _MICROCOMPACT_CLEARED_MARK,
    CompactResult,
    maybe_auto_compact,
    microcompact_session,
)
from modelbridge.agent.session import Session
from modelbridge.models import (
    AppConfig,
    ModelEntry,
    ProviderType,
    PromptConfig,
)
from modelbridge.schemas import ChatResponse


def _wire(monkeypatch, *, window: int = 100_000, threshold_pct: int = 80,
          reserve: int = 8_192, min_messages: int = 2):
    cfg = AppConfig(prompt=PromptConfig(
        auto_compact_threshold_pct=threshold_pct,
        auto_compact_min_messages=min_messages,
        auto_compact_output_reserve_tokens=reserve,
    ))
    monkeypatch.setattr(config_mod, "load_app_config", lambda: cfg)
    monkeypatch.setattr(
        compact,
        "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1",
            extra={"context_window": window},
        ),
    )


def _fake_compact(monkeypatch, *, ok: bool = True):
    calls = {"n": 0}

    def fake(session, **kwargs):
        calls["n"] += 1
        return CompactResult(ok=ok)

    monkeypatch.setattr(compact, "compact_session", fake)
    return calls


def _tool_session(n_tools: int, *, body_chars: int = 3000, system: str = "S") -> Session:
    """n_tools 个"助手调工具 + 用户追问"轮次；每轮一个 user turn，保证
    keep_turns=6 的尾部保护之外仍有足够的中段候选。"""
    s = Session(model_name="fake")
    s.add_system(system)
    s.add_user("开始任务")
    for i in range(n_tools):
        s.add_assistant(ChatResponse(content="调用工具", provider="fake"))
        s.add_tool_result(
            tool_call_id=f"c{i}", content=f"工具输出 {i}：" + "x" * body_chars
        )
        s.add_user(f"继续 {i}")
    return s


# ---------------------------------------------------------------------------
# microcompact_session 单元
# ---------------------------------------------------------------------------

def test_microcompact_clears_oldest_keeps_recent_five():
    s = _tool_session(14)  # 中段候选 14−6(尾部保护)=8（实际按 user 轮回退）
    cleared, saved = microcompact_session(s, keep_turns=6)
    assert cleared >= 1 and saved > 0
    tool_contents = [m.content for m in s.messages if m.role == "tool"]
    cleared_n = sum(c.startswith(_MICROCOMPACT_CLEARED_MARK[:6]) for c in tool_contents)
    assert cleared_n == cleared
    assert tool_contents[-1].startswith("工具输出 13")  # 最近的工具结果保留
    # 清的是最老的一段：保留区条数恰为 keep_recent（+错误保护位）。
    assert len(tool_contents) - cleared_n >= 5


def test_microcompact_protects_error_results():
    s = _tool_session(14)
    for m in s.messages:
        if m.role == "tool":
            m.content = "错误：权限不足"  # 最老的候选改为错误结果
            break
    cleared, _ = microcompact_session(s, keep_turns=6)
    tool_contents = [m.content for m in s.messages if m.role == "tool"]
    assert any(c.startswith("错误") for c in tool_contents)  # 错误结果保留
    assert sum(c.startswith(_MICROCOMPACT_CLEARED_MARK[:6]) for c in tool_contents) == cleared


def test_microcompact_min_savings_noop():
    """净省不足 256 tokens（≈768 字符）→ 不动会话。"""
    s = _tool_session(12, body_chars=10)  # 中段候选 >5 但每条只有几十字符
    before = [m.content for m in s.messages if m.role == "tool"]
    cleared, saved = microcompact_session(s, keep_turns=6)
    assert cleared == 0 and saved == 0
    after = [m.content for m in s.messages if m.role == "tool"]
    assert before == after


def test_microcompact_leaves_prefix_and_tail_untouched():
    s = _tool_session(14)
    microcompact_session(s, keep_turns=6)
    assert s.messages[0].role == "system" and s.messages[0].content == "S"
    assert s.messages[-1].role == "user"  # 尾部（keep_turns 内）原样保留
    assert s.messages[-1].content == "继续 13"


# ---------------------------------------------------------------------------
# maybe_auto_compact：microcompact 前置 + 真实用量锚点
# ---------------------------------------------------------------------------

def test_micro_only_skips_summarizer_when_tool_output_dominates(monkeypatch):
    """子比例触发 → micro 清完降到全量触发点以下 → 不发起摘要调用。"""
    _wire(monkeypatch, window=100_000, threshold_pct=80, reserve=0)
    calls = _fake_compact(monkeypatch)
    # 锚点 75K：低于全量触发点 80K、高于 micro 触发点 72K；14 个工具轮的
    # 中段候选 9 条 → 清 4 条（保 5）≈ 省 4K tokens → 降到全量触发点以下。
    s = _tool_session(14)
    s.metadata["usage_anchor"] = {"prompt_tokens": 74_000, "completion_tokens": 1_000}

    notices: list[str] = []
    result = maybe_auto_compact(s, model_name="fake", on_system=notices.append)
    assert calls["n"] == 0, "micro 减压足够时必须跳过摘要模型调用"
    assert result is not None and result.ok
    assert result.pruned_count == 4
    assert result.saved_tokens >= 256
    tool_contents = [m.content for m in s.messages if m.role == "tool"]
    assert sum(c.startswith(_MICROCOMPACT_CLEARED_MARK[:6]) for c in tool_contents) == 4


def test_micro_insufficient_falls_through_to_summarizer(monkeypatch):
    """micro 清理量不足（净省低于最小值）→ 回落全量摘要路径。"""
    _wire(monkeypatch, window=100_000, threshold_pct=80, reserve=0)
    calls = _fake_compact(monkeypatch)
    s = Session(model_name="fake")
    s.add_system("S")
    s.add_user("u")
    for i in range(14):  # 短工具结果：micro 净省不足 → no-op
        s.add_assistant(ChatResponse(content="call", provider="fake"))
        s.add_tool_result(tool_call_id=f"c{i}", content=f"短 {i}")
        s.add_user(f"继续 {i}")
    s.metadata["usage_anchor"] = {"prompt_tokens": 90_000, "completion_tokens": 1_000}

    result = maybe_auto_compact(s, model_name="fake")
    assert calls["n"] == 1  # 仍高于全量触发点 → 走摘要
    assert result is not None


def test_real_usage_anchor_drives_trigger(monkeypatch):
    """锚点优先于估算：估算低于阈值但锚点超过 → 触发压缩。"""
    _wire(monkeypatch, window=100_000, threshold_pct=80, reserve=0)
    calls = _fake_compact(monkeypatch)
    s = Session(model_name="fake")
    s.add_system("S")
    s.add_user("短短输入")  # 估算只有几十 tokens
    s.metadata["usage_anchor"] = {"prompt_tokens": 90_000, "completion_tokens": 2_000}

    maybe_auto_compact(s, model_name="fake")
    assert calls["n"] == 1  # 锚点 92K ≥ 80%×100K → 触发

    # 反向：锚点很低而估算高 —— 也以锚点为准（不触发）。
    s2 = Session(model_name="fake")
    s2.add_system("S")
    s2.add_user("长" * 90_000)  # 估算 ~90K
    s2.metadata["usage_anchor"] = {"prompt_tokens": 100, "completion_tokens": 10}
    maybe_auto_compact(s2, model_name="fake")
    assert calls["n"] == 1  # 仍只发生过那一次


def test_output_reserve_denominator_exact(monkeypatch):
    """精确锚点：78K 用量在完整窗口下 78% < 80% 不触发；预留 20K 后
    分母 80K → 97.5% ≥ 80% 触发。"""
    _wire(monkeypatch, window=100_000, threshold_pct=80, reserve=20_000)
    calls = _fake_compact(monkeypatch)
    s = Session(model_name="fake")
    s.add_system("S")
    s.add_user("x" * 200)
    s.metadata["usage_anchor"] = {"prompt_tokens": 77_000, "completion_tokens": 1_000}

    maybe_auto_compact(s, model_name="fake")
    assert calls["n"] == 1


# --- 评审修复：错误标志（ChatMessage.error）驱动保护 -------------------------

def test_failed_tool_result_flag_protects_from_microcompact():
    """loop 写入的失败结果（error=True，文案不带任何错误字样）受保护。"""
    s = Session(model_name="fake")
    s.add_system("S")
    s.add_user("开始任务")
    for i in range(14):
        s.add_assistant(ChatResponse(content="调用工具", provider="fake"))
        # 最老一条是失败结果（文案无“错误”/Traceback/[exit] 等可启发识别的特征）。
        s.add_tool_result(
            tool_call_id=f"c{i}",
            content=f"old_str 在文件中出现 2 次，需要唯一匹配（第 {i} 处说明）" + "x" * 3000,
            error=(i == 0),
        )
        s.add_user(f"继续 {i}")
    cleared, _ = microcompact_session(s, keep_turns=6)
    tool_contents = [m.content for m in s.messages if m.role == "tool"]
    # 失败结果（第 0 条，error=True）未被清成占位符 —— 显式标志保护生效。
    assert any("（第 0 处说明）" in c for c in tool_contents)
    assert cleared == 3      # 其余非保护结果照常清理（中段候选 9−1 错误 = 8，清 8−5=3）
    assert sum(c.startswith(_MICROCOMPACT_CLEARED_MARK[:6]) for c in tool_contents) == 3


def test_add_tool_result_error_flag_roundtrip():
    """error 标志在 ChatMessage 上记录，且随 to_dict/from_dict 往返保留；
    to_wire 不含该字段（协议 wire 不变）。"""
    s = Session(model_name="m")
    s.add_tool_result(tool_call_id="c1", content="失败详情", error=True)
    msg = s.messages[-1]
    assert msg.error is True and msg.role == "tool"
    assert "error" not in msg.to_wire()
    restored = Session.from_dict(s.to_dict())
    assert restored.messages[-1].error is True


def test_microcompact_cleared_message_keeps_error_flag():
    """被清理的消息若原本带错误标志（理论上不该被清，防御性），标志不丢。"""
    s = Session(model_name="fake")
    s.add_system("S")
    s.add_user("开始任务")
    for i in range(14):
        s.add_assistant(ChatResponse(content="调用工具", provider="fake"))
        s.add_tool_result(tool_call_id=f"c{i}", content="x" * 3000, error=True)
        s.add_user(f"继续 {i}")
    # 全部 error=True → 无候选 → 不清理。
    cleared, _ = microcompact_session(s, keep_turns=6)
    assert cleared == 0
