"""流韧性（E1①）：stream_chat 的显式逐块看门狗。

连接池默认 Timeout 的 read=None 只是没有兜底；流路径必须自带显式的
per-request read 超时（httpx 语义 = 两次收到数据之间的最大间隔），厂商流
挂起时最多 idle 秒抛 ReadTimeout，REPL 不再永久阻塞。
"""

from __future__ import annotations

import httpx
import pytest

from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers import get_provider
from modelbridge.providers.base import HTTPProvider
from modelbridge.schemas import ChatMessage, ChatRequest, ProviderError


def _provider() -> HTTPProvider:
    p = get_provider(ModelEntry(
        name="t", provider=ProviderType.OPENAI_COMPATIBLE,
        model="m", base_url="https://x/v1",
    ))
    assert isinstance(p, HTTPProvider)
    return p


class _FakeStreamResp:
    status_code = 200

    def read(self):
        return b""

    def iter_lines(self):
        yield 'data: {"choices": [{"delta": {"content": "hi"}}]}'
        yield "data: [DONE]"


class _FakeStreamClient:
    """记录 stream() 调用参数的假连接池客户端。"""

    def __init__(self):
        self.stream_kwargs: dict = {}

    def stream(self, method, url, **kwargs):
        self.stream_kwargs = {"method": method, "url": url, **kwargs}
        import contextlib

        @contextlib.contextmanager
        def cm():
            yield _FakeStreamResp()

        return cm()


def test_stream_chat_passes_explicit_idle_watchdog(monkeypatch):
    """E1①: 流请求带显式 httpx.Timeout，read（块间最大间隔）有限且 ≥30s。"""
    fake = _FakeStreamClient()
    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client", lambda url: fake
    )
    p = _provider()
    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])
    events = list(p.stream_chat(req, timeout=120))
    assert any(ev.kind == "content" and ev.text == "hi" for ev in events)

    t = fake.stream_kwargs["timeout"]
    assert isinstance(t, httpx.Timeout)
    assert t.read is not None and t.read >= 30.0  # 看门狗有限，不挂死
    assert fake.stream_kwargs["url"].endswith("/chat/completions")


def test_stream_idle_timeout_surfaces_as_retriable_provider_error(monkeypatch):
    """厂商流挂起 → httpx.ReadTimeout → 归一化为 timeout 类 ProviderError
    （is_retryable_error 判定可重试），而非无限等待。"""
    from modelbridge.error_hints import is_retryable_error

    class _HangingClient:
        def stream(self, method, url, **kwargs):
            import contextlib

            @contextlib.contextmanager
            def cm():
                yield _HangingResp(kwargs["timeout"])

            return cm()

    class _HangingResp:
        status_code = 200

        def __init__(self, timeout):
            self._timeout = timeout

        def read(self):
            return b""

        def iter_lines(self):
            raise httpx.ReadTimeout("timed out while reading")

    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client", lambda url: _HangingClient()
    )
    p = _provider()
    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ProviderError) as ei:
        list(p.stream_chat(req, timeout=5))
    assert ei.value.error_type == "timeout"
    assert is_retryable_error(ei.value)
