# tests/test_new_providers.py
"""Adapters for the 2026-08 domestic-vendor expansion.

Covers Hunyuan / Doubao / ERNIE / Spark / StepFun / SenseNova: endpoint
normalisation, Doubao's thinking translation, vendor error hints, and
registry wiring (the right adapter class must come back from
``get_provider``).
"""

from __future__ import annotations

import pytest

from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers import (
    DoubaoProvider,
    ERNIEProvider,
    HunyuanProvider,
    SenseNovaProvider,
    SparkProvider,
    StepFunProvider,
    get_provider,
)
from modelbridge.schemas import ChatMessage, ChatRequest


def _entry(provider: ProviderType, model: str, base_url: str) -> ModelEntry:
    return ModelEntry(
        name="t", provider=provider, model=model, base_url=base_url,
        capabilities={"local": False},
    )


def _provider(provider: ProviderType, model: str, base_url: str):
    p = get_provider(_entry(provider, model, base_url))
    assert p.provider_type == provider
    return p


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider,cls,model,base", [
    (ProviderType.HUNYUAN, HunyuanProvider, "hy3", "https://api.hunyuan.cloud.tencent.com/v1"),
    (ProviderType.DOUBAO, DoubaoProvider, "doubao-seed-1.8", "https://ark.cn-beijing.volces.com/api/v3"),
    (ProviderType.ERNIE, ERNIEProvider, "ernie-4.5-turbo-128k", "https://qianfan.baidubce.com/v2"),
    (ProviderType.SPARK, SparkProvider, "x2", "https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1"),
    (ProviderType.STEPFUN, StepFunProvider, "step-3", "https://api.stepfun.com/v1"),
    (ProviderType.SENSENOVA, SenseNovaProvider, "SenseNova-V5-Turbo",
     "https://api.sensenova.cn/compatible-mode/v1"),
])
def test_registry_returns_dedicated_adapter(provider, cls, model, base):
    assert isinstance(_provider(provider, model, base), cls)


# ---------------------------------------------------------------------------
# Endpoint normalisation
# ---------------------------------------------------------------------------

def test_hunyuan_legacy_host_is_redirected_to_compat_endpoint():
    p = _provider(
        ProviderType.HUNYUAN, "hy3", "https://hunyuan.tencentcloudapi.com"
    )
    assert p.chat_endpoint() == "https://api.hunyuan.cloud.tencent.com/v1/chat/completions"


def test_hunyuan_compat_base_normalises():
    p = _provider(
        ProviderType.HUNYUAN, "hy3", "https://api.hunyuan.cloud.tencent.com/v1"
    )
    assert p.chat_endpoint() == "https://api.hunyuan.cloud.tencent.com/v1/chat/completions"


def test_doubao_ark_base_appends_chat_completions():
    p = _provider(
        ProviderType.DOUBAO, "doubao-seed-1.8", "https://ark.cn-beijing.volces.com/api/v3"
    )
    assert p.chat_endpoint() == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def test_sensenova_native_endpoint_is_redirected_to_compatible_mode():
    # The native /v1 endpoint uses JWT signing the OpenAI transport can't
    # do — pasting it must still land on compatible-mode.
    p = _provider(ProviderType.SENSENOVA, "SenseNova-V5-Turbo", "https://api.sensenova.cn/v1")
    assert p.chat_endpoint() == "https://api.sensenova.cn/compatible-mode/v1/chat/completions"
    p2 = _provider(ProviderType.SENSENOVA, "SenseNova-V5-Turbo", "https://api.sensenova.cn")
    assert p2.chat_endpoint() == "https://api.sensenova.cn/compatible-mode/v1/chat/completions"


def test_spark_maas_and_stepfun_endpoints():
    assert _provider(
        ProviderType.SPARK, "x2", "https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1"
    ).chat_endpoint() == "https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1/chat/completions"
    assert _provider(
        ProviderType.STEPFUN, "step-3", "https://api.stepfun.com/v1"
    ).chat_endpoint() == "https://api.stepfun.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# Doubao thinking translation (same shape as DeepSeek, no effort grades)
# ---------------------------------------------------------------------------

def _doubao() -> DoubaoProvider:
    return _provider(
        ProviderType.DOUBAO, "doubao-seed-1.8", "https://ark.cn-beijing.volces.com/api/v3"
    )


def _req(**kw) -> ChatRequest:
    defaults = dict(
        model="doubao-seed-1.8",
        messages=[ChatMessage(role="user", content="hi")],
    )
    defaults.update(kw)
    return ChatRequest(**defaults)


def test_doubao_thinking_off_sends_disabled():
    body = _doubao().build_chat_payload(_req(thinking=False))
    assert body["thinking"] == {"type": "disabled"}


def test_doubao_thinking_on_sends_enabled_without_budget_field():
    body = _doubao().build_chat_payload(_req(thinking=True, thinking_budget=4096))
    assert body["thinking"] == {"type": "enabled"}
    # Ark has no effort/budget parameter — the budget must not leak onto the wire.
    assert "reasoning_effort" not in body
    assert "thinking_budget" not in body


def test_doubao_no_thinking_signal_leaves_body_untouched():
    body = _doubao().build_chat_payload(_req(thinking=None))
    assert "thinking" not in body


def test_doubao_explicit_extra_body_wins():
    body = _doubao().build_chat_payload(
        _req(thinking=False, extra_body={"thinking": {"type": "enabled"}})
    )
    assert body["thinking"] == {"type": "enabled"}


# ---------------------------------------------------------------------------
# Error hints
# ---------------------------------------------------------------------------

def test_hunyuan_hint_explains_compat_endpoint():
    p = _provider(ProviderType.HUNYUAN, "hy3", "https://api.hunyuan.cloud.tencent.com/v1")
    err = p.normalize_error(status_code=401, body="{}")
    assert "api.hunyuan.cloud.tencent.com" in (err.hint or "")


def test_doubao_hint_mentions_ep_and_v3():
    p = _doubao()
    err = p.normalize_error(status_code=404, body="{}")
    assert "api/v3" in (err.hint or "")
    assert "ep-" in (err.hint or "")


def test_ernie_hint_mentions_v2_endpoint():
    p = _provider(ProviderType.ERNIE, "ernie-x1-turbo-32k", "https://qianfan.baidubce.com/v2")
    err = p.normalize_error(status_code=400, body="{}")
    assert "qianfan.baidubce.com/v2" in (err.hint or "")


def test_spark_hint_mentions_maas_endpoint():
    p = _provider(ProviderType.SPARK, "x2", "https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1")
    err = p.normalize_error(status_code=404, body="{}")
    assert "xinghuo-maas" in (err.hint or "")


def test_sensenova_hint_warns_against_native_jwt_endpoint():
    p = _provider(
        ProviderType.SENSENOVA, "SenseNova-V5-Turbo",
        "https://api.sensenova.cn/compatible-mode/v1",
    )
    err = p.normalize_error(status_code=401, body="{}")
    assert "compatible-mode" in (err.hint or "")


# ---------------------------------------------------------------------------
# HTTPProvider.chat 有限重试（429/5xx/超时/连接与传输类；指数退避 + jitter）
# ---------------------------------------------------------------------------

import httpx  # noqa: E402
import pytest  # noqa: E402

import modelbridge.config as config_mod  # noqa: E402
import modelbridge.providers.base as provider_base  # noqa: E402
from modelbridge.error_hints import is_retryable_error  # noqa: E402
from modelbridge.models import AppConfig, PromptConfig  # noqa: E402
from modelbridge.schemas import ProviderError  # noqa: E402

_OK_PAYLOAD = {
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ]
}


class _FakeResp:
    def __init__(
        self, status_code: int = 200, payload: dict | None = None,
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = f'{{"error": {{"message": "HTTP {status_code}"}}}}'

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """按脚本逐个返回响应/异常，记录 post 次数。"""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.posts = 0

    def post(self, url, **kwargs):  # noqa: ARG002
        self.posts += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _retry_setup(monkeypatch, outcomes: list, *, retries: int = 2):
    """接好 fake 传输层 + 确定性 jitter + sleep 捕获，返回 (provider, fake, sleeps)。"""
    monkeypatch.setattr(
        config_mod,
        "load_app_config",
        lambda: AppConfig(
            prompt=PromptConfig(
                model_retry_max_retries=retries,
                model_retry_base_delay_ms=500,
                model_retry_max_delay_ms=8000,
            )
        ),
    )
    fake = _FakeClient(outcomes)
    monkeypatch.setattr(provider_base, "_shared_http_client", lambda url: fake)
    # jitter 取中点 (a+b)/2 == 1.0 → 退避序列确定：0.5s, 1.0s, 2.0s…
    monkeypatch.setattr(provider_base.random, "uniform", lambda a, b: (a + b) / 2)
    sleeps: list[float] = []
    monkeypatch.setattr(provider_base.time, "sleep", sleeps.append)
    p = _provider(
        ProviderType.OPENAI_COMPATIBLE, "gpt-test", "https://api.example.com/v1"
    )
    return p, fake, sleeps


def test_429_retries_then_succeeds(monkeypatch):
    p, fake, sleeps = _retry_setup(
        monkeypatch, [_FakeResp(429), _FakeResp(200, _OK_PAYLOAD)]
    )
    resp = p.chat(_req(), timeout=5.0)
    assert resp.content == "ok"
    assert fake.posts == 2
    assert sleeps == [0.5]  # base 500ms，jitter 中点


def test_persistent_timeout_exhausts_bounded_retries(monkeypatch):
    p, fake, sleeps = _retry_setup(
        monkeypatch, [httpx.ReadTimeout("read timed out")], retries=2
    )
    with pytest.raises(ProviderError) as ei:
        p.chat(_req(), timeout=5.0)
    assert ei.value.error_type == "timeout"
    # 首次 + 2 次重试 = 3 次，退避递增（0.5 → 1.0），然后原样抛出。
    assert fake.posts == 3
    assert sleeps == [0.5, 1.0]
    assert sleeps[1] > sleeps[0]


def test_http_400_is_not_retried(monkeypatch):
    p, fake, sleeps = _retry_setup(monkeypatch, [_FakeResp(400)], retries=2)
    with pytest.raises(ProviderError):
        p.chat(_req(), timeout=5.0)
    assert fake.posts == 1  # 语义错误重试无意义
    assert sleeps == []


def test_5xx_retried_then_succeeds(monkeypatch):
    p, fake, sleeps = _retry_setup(
        monkeypatch, [_FakeResp(503), _FakeResp(200, _OK_PAYLOAD)]
    )
    resp = p.chat(_req(), timeout=5.0)
    assert resp.content == "ok"
    assert fake.posts == 2
    assert sleeps == [0.5]


def test_retry_disabled_when_max_retries_zero(monkeypatch):
    p, fake, sleeps = _retry_setup(
        monkeypatch, [_FakeResp(429), _FakeResp(200, _OK_PAYLOAD)], retries=0
    )
    with pytest.raises(ProviderError):
        p.chat(_req(), timeout=5.0)
    assert fake.posts == 1
    assert sleeps == []


def test_is_retryable_error_classification():
    def _err(**kw) -> ProviderError:
        return ProviderError("boom", provider="t", **kw)

    # 可重试：429 / 5xx / 超时 / 连接 / 传输
    assert is_retryable_error(_err(status_code=429, error_type="http_4xx"))
    assert is_retryable_error(_err(status_code=500, error_type="http_5xx"))
    assert is_retryable_error(_err(error_type="timeout"))
    assert is_retryable_error(_err(error_type="connect"))
    assert is_retryable_error(_err(error_type="transport"))
    # 不可重试：其余 4xx / 鉴权 / decode
    assert not is_retryable_error(_err(status_code=401, error_type="http_4xx"))
    assert not is_retryable_error(_err(status_code=400, error_type="http_4xx"))
    assert not is_retryable_error(_err(error_type="decode"))


# --- U10: HTTP JSON 业务错误体识别 + msg/error_code/code/request_id 兜底链 ---

def test_chat_json_body_business_error_raises(monkeypatch):
    """HTTP 200 + success:false 业务体 → 抛带分类 hint 与 request_id 的错误。"""
    monkeypatch.setattr(
        config_mod,
        "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(model_retry_max_retries=0)),
    )
    payload = {
        "success": False, "msg": "余额不足",
        "error_code": 1316, "request_id": "req-9",
    }
    fake = _FakeClient([_FakeResp(200, payload)])
    monkeypatch.setattr(provider_base, "_shared_http_client", lambda url: fake)
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    with pytest.raises(ProviderError) as ei:
        p.chat(_req(), timeout=5)
    err = ei.value
    assert err.error_type == "business_quota_exhausted"
    assert err.request_id == "req-9"
    assert "req-9" in (err.hint or "") and "报障必填" in (err.hint or "")
    assert not is_retryable_error(err)
    assert err.to_dict()["request_id"] == "req-9"  # to_dict 带上 request_id


def test_normalize_error_msg_fallback_chain(monkeypatch):
    """错误体只有 msg 字段时也能取到文案；1302 业务码给出限流可重试 hint。"""
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    err = p.normalize_error(status_code=200, body='{"msg": "[1302][x][rid-3]"}')
    assert err.error_type == "business_rate_limited"
    assert is_retryable_error(err)
    assert err.request_id is None  # 方括号只解析 code；字段链才有 request_id


def test_normalize_error_plain_400_message_still_works():
    """旧路径不回退：error.message 依旧优先，普通 4xx 分类不变。"""
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    err = p.normalize_error(status_code=401, body='{"error": {"message": "bad key"}}')
    assert err.error_type == "http_4xx"
    assert "bad key" in err.message
    assert err.request_id is None


# --- E1③: Retry-After 优先于本地退避 ---------------------------------------

def test_retry_after_header_takes_priority(monkeypatch):
    """429 + Retry-After: 7 → 重试等待 7s（厂商节奏优先于本地 0.5s 退避）。"""
    fake = _FakeClient([
        _FakeResp(429, headers={"Retry-After": "7"}),
        _FakeResp(200, _OK_PAYLOAD),
    ])
    monkeypatch.setattr(provider_base, "_shared_http_client", lambda url: fake)
    monkeypatch.setattr(provider_base.random, "uniform", lambda a, b: (a + b) / 2)
    sleeps: list[float] = []
    monkeypatch.setattr(provider_base.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        config_mod, "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(model_retry_max_retries=2)),
    )
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    resp = p.chat(_req(), timeout=5)
    assert resp.content == "ok"
    assert sleeps == [7.0]  # Retry-After 原样优先，而非本地退避 0.5


def test_retry_after_over_cap_falls_back_to_backoff(monkeypatch):
    """Retry-After 超过 5min 视为不合理 → 回落本地指数退避（0.5s）。"""
    fake = _FakeClient([
        _FakeResp(429, headers={"Retry-After": "9999"}),
        _FakeResp(200, _OK_PAYLOAD),
    ])
    monkeypatch.setattr(provider_base, "_shared_http_client", lambda url: fake)
    monkeypatch.setattr(provider_base.random, "uniform", lambda a, b: (a + b) / 2)
    sleeps: list[float] = []
    monkeypatch.setattr(provider_base.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        config_mod, "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(model_retry_max_retries=2)),
    )
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    resp = p.chat(_req(), timeout=5)
    assert resp.content == "ok"
    assert sleeps == [0.5]


def test_retry_after_none_or_garbage_falls_back(monkeypatch):
    """无 Retry-After / HTTP-date 形式 → None → 本地退避；错误对象仍携带字段。"""
    fake = _FakeClient([
        _FakeResp(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _FakeResp(200, _OK_PAYLOAD),
    ])
    monkeypatch.setattr(provider_base, "_shared_http_client", lambda url: fake)
    monkeypatch.setattr(provider_base.random, "uniform", lambda a, b: (a + b) / 2)
    sleeps: list[float] = []
    monkeypatch.setattr(provider_base.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        config_mod, "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(model_retry_max_retries=2)),
    )
    p = _provider(ProviderType.OPENAI_COMPATIBLE, "m", "https://x/v1")
    assert p.chat(_req(), timeout=5).content == "ok"
    assert sleeps == [0.5]


def test_parse_retry_after_unit():
    from modelbridge.providers.base import _parse_retry_after

    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after(" 2.5 ") == 2.5
    assert _parse_retry_after("0") is None
    assert _parse_retry_after("-1") is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None  # HTTP-date
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("soon") is None


def test_provider_error_retry_after_in_to_dict():
    from modelbridge.schemas import ProviderError

    err = ProviderError("x", provider="t", retry_after=12.5)
    assert err.to_dict()["retry_after"] == 12.5
