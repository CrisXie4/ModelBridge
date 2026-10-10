"""自动压缩连续失败熔断（maybe_auto_compact 的失败计数与闸门）。

契约（见 :func:`modelbridge.agent.compact.maybe_auto_compact`）：

* 摘要器连续失败 ≥ ``_MAX_AUTO_COMPACT_FAILURES``（3）次后，自动压缩
  静默跳过（返回 None，不再调 compact_session）—— 否则 loop 每个
  step 边界都会白付一次最长 120s 的失败调用并刷屏。
* 失败计数存 ``session.metadata["auto_compact_consecutive_failures"]``，
  达到阈值时只提示一次暂停。
* :func:`compact_session` 成功提交即重置 —— 手动 /compact 直接调
  compact_session，重置放在那里才能同时覆盖 auto 与手动两条路。
* ``force=True``（溢出恢复）不受熔断限制。
* 网络/配置无关：``compact_session`` 与 ``load_app_config`` 被 fake，
  计数与闸门走 maybe_auto_compact 的真实代码路径。
"""

from __future__ import annotations

import modelbridge.agent.compact as compact
import modelbridge.config as config_mod
from modelbridge.agent.compact import (
    _FAILURES_METADATA_KEY,
    _MAX_AUTO_COMPACT_FAILURES,
    CompactResult,
    maybe_auto_compact,
)
from modelbridge.agent.session import Session
from modelbridge.models import (
    AppConfig,
    ModelEntry,
    ProviderType,
    PromptConfig,
)
from modelbridge.schemas import ChatMessage

_BIG = "关于项目结构的说明。" * 40  # 同 test_compact.py —— 保证可收缩


def _wire(monkeypatch):
    """启用 auto_compact 并放低门槛，让测试会话能越过阈值/条数闸门。"""
    cfg = AppConfig(
        prompt=PromptConfig(
            auto_compact_threshold_pct=1,
            auto_compact_min_messages=2,
            # 10 token 的测试窗口必须把 E5 输出预留设 0，否则分母归零。
            auto_compact_output_reserve_tokens=0,
        )
    )
    monkeypatch.setattr(config_mod, "load_app_config", lambda: cfg)
    monkeypatch.setattr(
        compact,
        "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1", extra={"context_window": 10},
        ),
    )


def _fake_compact(*, ok: bool):
    """替换 compact_session：恒定成功/失败，并统计调用次数。"""
    calls = {"n": 0}

    def fake(session, **kwargs):
        calls["n"] += 1
        return CompactResult(ok=ok, reason="" if ok else "boom")

    return fake, calls


def _session() -> Session:
    s = Session(model_name="fake")
    s.add_system("sys")
    s.add_user("这是一条足够长的消息，让启发式 token 估算越过阈值。" * 10)
    return s


# ---------------------------------------------------------------------------
# 熔断闸门
# ---------------------------------------------------------------------------

def test_breaker_skips_after_three_consecutive_failures(monkeypatch):
    _wire(monkeypatch)
    fake, calls = _fake_compact(ok=False)
    monkeypatch.setattr(compact, "compact_session", fake)
    session = _session()
    notices: list[str] = []

    results = [
        maybe_auto_compact(session, model_name="fake", on_system=notices.append)
        for _ in range(5)
    ]

    # 前 3 次真的尝试（并失败），第 4、5 次被熔断静默跳过。
    assert calls["n"] == _MAX_AUTO_COMPACT_FAILURES
    assert session.metadata[_FAILURES_METADATA_KEY] == _MAX_AUTO_COMPACT_FAILURES
    assert all(r is not None and not r.ok for r in results[:3])
    assert results[3] is None and results[4] is None
    # 失败通知照发（3 次），"暂停"提示只在恰好达到阈值时出现一次。
    assert sum("自动压缩失败" in n for n in notices) == 3
    assert sum("暂停自动压缩" in n for n in notices) == 1


def test_breaker_blocked_calls_emit_no_notice(monkeypatch):
    _wire(monkeypatch)
    fake, _ = _fake_compact(ok=False)
    monkeypatch.setattr(compact, "compact_session", fake)
    session = _session()
    for _ in range(_MAX_AUTO_COMPACT_FAILURES):
        maybe_auto_compact(session, model_name="fake")

    before = len(session.messages)
    notices: list[str] = []
    assert maybe_auto_compact(session, model_name="fake", on_system=notices.append) is None
    assert notices == []  # 静默 —— 熔断的意义就是不再刷屏
    assert len(session.messages) == before


def test_consecutive_failures_tolerates_junk_metadata(monkeypatch):
    _wire(monkeypatch)
    fake, calls = _fake_compact(ok=False)
    monkeypatch.setattr(compact, "compact_session", fake)
    session = _session()
    session.metadata[_FAILURES_METADATA_KEY] = "not-a-number"

    maybe_auto_compact(session, model_name="fake")
    assert calls["n"] == 1
    assert session.metadata[_FAILURES_METADATA_KEY] == 1  # 从 0 重新起算


# ---------------------------------------------------------------------------
# 重置
# ---------------------------------------------------------------------------

def test_successful_compact_session_resets_counter():
    """真实 compact_session 成功提交即重置 —— 手动 /compact 走的正是这条路。"""
    session = Session(model_name="fake")
    session.messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content=_BIG + " u1"),
        ChatMessage(role="assistant", content=_BIG + " a1"),
        ChatMessage(role="user", content="u2"),
        ChatMessage(role="assistant", content="a2"),
    ]
    session.metadata[_FAILURES_METADATA_KEY] = _MAX_AUTO_COMPACT_FAILURES

    result = compact.compact_session(
        session,
        keep_turns=1,
        model_name="fake",
        summarize_fn=lambda _run: "短摘要",
    )
    assert result.ok and not result.skipped
    assert _FAILURES_METADATA_KEY not in session.metadata  # 熔断解除


def test_failed_compact_session_keeps_counter():
    """失败（回滚）不重置计数 —— 只有成功提交才解除熔断。"""
    def _explode(_run: str) -> str:
        raise RuntimeError("summarizer down")

    session = Session(model_name="fake")
    session.messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content=_BIG + " u1"),
        ChatMessage(role="assistant", content=_BIG + " a1"),
        ChatMessage(role="user", content="u2"),
        ChatMessage(role="assistant", content="a2"),
    ]
    session.metadata[_FAILURES_METADATA_KEY] = 1

    result = compact.compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_explode,
    )
    assert not result.ok
    assert session.metadata[_FAILURES_METADATA_KEY] == 1  # 原样保留


def test_success_attempt_does_not_increment_counter(monkeypatch):
    """成功不计数：失败×2 → 成功 → 失败×1 仍可尝试；再失败才数满 3 熔断。"""
    _wire(monkeypatch)
    fail_fake, fail_calls = _fake_compact(ok=False)
    ok_fake, ok_calls = _fake_compact(ok=True)
    monkeypatch.setattr(compact, "compact_session", fail_fake)
    session = _session()
    maybe_auto_compact(session, model_name="fake")
    maybe_auto_compact(session, model_name="fake")
    assert fail_calls["n"] == 2

    monkeypatch.setattr(compact, "compact_session", ok_fake)
    maybe_auto_compact(session, model_name="fake")
    assert ok_calls["n"] == 1
    assert session.metadata[_FAILURES_METADATA_KEY] == 2  # 成功不增长计数

    monkeypatch.setattr(compact, "compact_session", fail_fake)
    result = maybe_auto_compact(session, model_name="fake")
    assert result is not None and not result.ok  # 第 3 次失败才到阈值
    assert session.metadata[_FAILURES_METADATA_KEY] == 3
    assert maybe_auto_compact(session, model_name="fake") is None  # 此后熔断
    assert fail_calls["n"] == 3


# ---------------------------------------------------------------------------
# force 旁路
# ---------------------------------------------------------------------------

def test_force_bypasses_breaker(monkeypatch):
    """溢出恢复（force=True）不受熔断限制 —— 不压缩请求就发不出去。"""
    _wire(monkeypatch)
    fake, calls = _fake_compact(ok=False)
    monkeypatch.setattr(compact, "compact_session", fake)
    session = _session()
    session.metadata[_FAILURES_METADATA_KEY] = _MAX_AUTO_COMPACT_FAILURES + 2

    notices: list[str] = []
    result = maybe_auto_compact(
        session, model_name="fake", on_system=notices.append, force=True,
    )
    assert calls["n"] == 1
    assert result is not None and not result.ok
    # 计数继续增长，但"暂停"提示只在恰好 == 阈值时发过（早已错过）。
    assert session.metadata[_FAILURES_METADATA_KEY] == _MAX_AUTO_COMPACT_FAILURES + 3
    assert not any("暂停自动压缩" in n for n in notices)
