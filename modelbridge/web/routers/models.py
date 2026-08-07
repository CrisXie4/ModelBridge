"""Models (channels) CRUD — mirrors ``mbridge model`` CLI commands."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...config import find_model, load_models_file, remove_model, upsert_model
from ...context.windows import context_window_for
from ...cost.estimator import DEFAULT_PRICING
from ...models import Capabilities, ModelEntry, ModelLevel, ProviderType, TransportType
from ...provider_profiles import PROFILES, get_profile
from ...secrets import is_protected, protect
from ..schemas import Message, ModelIn, ModelOut

router = APIRouter(prefix="/models", tags=["models"])


def _to_out(entry: ModelEntry) -> ModelOut:
    return ModelOut(
        name=entry.name,
        provider=entry.provider.value,
        type=entry.type.value,
        base_url=entry.base_url,
        model=entry.model,
        api_key_env=entry.api_key_env,
        has_api_key=bool(entry.api_key) or bool(entry.api_key_env),
        level=entry.level.value,
        capabilities=entry.capabilities.model_dump(),
        extra=entry.extra,
    )


@router.get("", response_model_exclude_none=True)
def list_models() -> dict:
    mf = load_models_file()
    return {"models": [_to_out(m) for m in mf.models]}


@router.get("/catalog")
def catalog() -> dict:
    """Built-in model catalog — all models ModelBridge knows pricing/context for.

    Joins :data:`DEFAULT_PRICING` (price + currency + cache rate) with
    :data:`DEFAULT_CONTEXT_WINDOWS` (token window) and the provider profile
    (base_url / api_key_env / capabilities preset). This is what the "添加渠道"
    picker reads so the user can configure any of the 30+ builtin models in
    one click instead of typing base_url / model id / price by hand.
    """
    # Map provider by matching the profile whose model_examples contains mid.
    # Fall back to OPENAI_COMPATIBLE when no profile claims the model.
    profile_by_model: dict[str, str] = {}
    for prov, prof in PROFILES.items():
        for ex in prof.model_examples:
            profile_by_model.setdefault(ex, prov.value)

    items: list[dict] = []
    for mid, pricing in DEFAULT_PRICING.items():
        prov_name = profile_by_model.get(mid, "openai-compatible")
        prof = get_profile(ProviderType(prov_name) if prov_name in ProviderType._value2member_map_ else ProviderType.OPENAI_COMPATIBLE)
        # context_window_for needs a ModelEntry; build a throwaway one.
        tmp = ModelEntry(
            name=mid, provider=ProviderType.OPENAI_COMPATIBLE,
            type=TransportType.OPENAI_COMPATIBLE, base_url=prof.base_url,
            model=mid, api_key_env=prof.api_key_env, level=prof.default_level,
            capabilities=prof.default_capabilities, extra={},
        )
        ctx = context_window_for(tmp)
        items.append({
            "model": mid,
            "provider": prov_name,
            "base_url": prof.base_url,
            "api_key_env": prof.api_key_env,
            "currency": pricing.currency,
            "input_per_1m": pricing.input_per_1m,
            "output_per_1m": pricing.output_per_1m,
            "cache_hit_input_per_1m": pricing.cache_hit_input_per_1m,
            "context_window": ctx,
            "pricing_source": pricing.source,
            "default_level": prof.default_level.value,
            "is_local": prof.is_local,
        })
    # Cheapest first within each provider, then by model id for stable order.
    items.sort(key=lambda x: (x["provider"], x["input_per_1m"], x["model"]))
    return {"catalog": items, "count": len(items)}


@router.post("", response_model=ModelOut)
def create_model(payload: ModelIn) -> ModelOut:
    existing = find_model(payload.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"模型 '{payload.name}' 已存在")
    entry = _build_entry(payload)
    upsert_model(entry)
    return _to_out(find_model(payload.name) or entry)


@router.put("/{name}", response_model=ModelOut)
def update_model(name: str, payload: ModelIn) -> ModelOut:
    if find_model(name) is None:
        raise HTTPException(status_code=404, detail=f"模型 '{name}' 不存在")
    if payload.name != name:
        # rename: remove old, insert new
        remove_model(name)
    entry = _build_entry(payload)
    upsert_model(entry)
    return _to_out(find_model(payload.name) or entry)


@router.delete("/{name}", response_model=Message)
def delete_model(name: str) -> Message:
    if not remove_model(name):
        raise HTTPException(status_code=404, detail=f"模型 '{name}' 不存在")
    return Message(message=f"已删除模型 '{name}'")


def _build_entry(payload: ModelIn) -> ModelEntry:
    api_key = payload.api_key or ""
    # If the frontend sent a protected token back unchanged, keep it.
    # Only protect brand-new plaintext keys.
    if api_key and not is_protected(api_key):
        api_key = protect(payload.name, api_key)
    caps = Capabilities(**(payload.capabilities.model_dump() if hasattr(payload.capabilities, "model_dump") else dict(payload.capabilities)))
    return ModelEntry(
        name=payload.name,
        provider=_coerce_enum(ProviderType, payload.provider, ProviderType.OPENAI_COMPATIBLE),
        type=_coerce_enum(TransportType, payload.type, TransportType.OPENAI_COMPATIBLE),
        base_url=payload.base_url,
        model=payload.model,
        api_key=api_key,
        api_key_env=payload.api_key_env,
        level=_coerce_enum(ModelLevel, payload.level, ModelLevel.CHEAP),
        capabilities=caps,
        extra=payload.extra or {},
    )


def _coerce_enum(enum_cls, value, default):
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default
