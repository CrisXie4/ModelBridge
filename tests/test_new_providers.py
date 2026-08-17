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
