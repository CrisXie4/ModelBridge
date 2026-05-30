"""Provider profiles — sane defaults per provider.

These power the simplified ``mbridge model init`` flow: choose a provider
preset and we fill in base_url / api_key_env / capabilities so the user
only has to enter the model id and API key.

Profiles are *suggestions* — every field is overridable interactively.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Capabilities, ModelLevel, ProviderType


@dataclass
class ProviderProfile:
    """Suggested defaults for a provider."""

    provider: ProviderType
    label: str
    base_url: str
    api_key_env: str | None = None
    model_examples: list[str] = field(default_factory=list)
    default_level: ModelLevel = ModelLevel.CHEAP
    default_capabilities: Capabilities = field(default_factory=Capabilities)
    is_local: bool = False
    notes: str = ""


# Capability presets to keep profile definitions terse.
_CLOUD_CAPS = Capabilities(
    tools=True, json=True, vision=False,
    reasoning=False, reasoning_content_back=False,
    cache=True, local=False, streaming=True,
)
_THINKING_CAPS = Capabilities(
    tools=True, json=True, vision=False,
    reasoning=True, reasoning_content_back=True,
    cache=True, local=False, streaming=True,
)
_LOCAL_CAPS = Capabilities(
    tools=False, json=False, vision=False,
    reasoning=False, reasoning_content_back=False,
    cache=False, local=True, streaming=True,
)


PROFILES: dict[ProviderType, ProviderProfile] = {
    ProviderType.DEEPSEEK: ProviderProfile(
        provider=ProviderType.DEEPSEEK,
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model_examples=["deepseek-chat", "deepseek-reasoner"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="deepseek-reasoner 返回 reasoning_content，多轮要保留。",
    ),
    ProviderType.QWEN: ProviderProfile(
        provider=ProviderType.QWEN,
        label="Qwen / 阿里云百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        model_examples=[
            "qwen3-coder-plus",
            "qwen3-coder-flash",
            "qwen-plus-latest",
            "qwen-max-latest",
        ],
        default_level=ModelLevel.CODER,
        default_capabilities=_CLOUD_CAPS,
        notes="thinking 系列需要 enable_thinking=true，由 ChatRequest.thinking 自动设置。",
    ),
    ProviderType.KIMI: ProviderProfile(
        provider=ProviderType.KIMI,
        label="Kimi (Moonshot AI)",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        model_examples=[
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
            "kimi-k2",
        ],
        default_level=ModelLevel.EXPERT,
        default_capabilities=_CLOUD_CAPS,
        notes="thinking 模型返回 reasoning_content；temperature 通常应=0。",
    ),
    ProviderType.MIMO: ProviderProfile(
        provider=ProviderType.MIMO,
        label="MiMo (小米)",
        base_url="https://api.platform.xiaomimimo.com/v1",
        api_key_env="MIMO_API_KEY",
        model_examples=["mimo-v2"],
        default_level=ModelLevel.AGENT,
        default_capabilities=_THINKING_CAPS,
        notes="★ thinking + tool_calls 必须回传 reasoning_content，否则 400。",
    ),
    ProviderType.GLM: ProviderProfile(
        provider=ProviderType.GLM,
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPU_API_KEY",
        model_examples=["glm-4.5", "glm-4-plus", "glm-4-flash"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
    ),
    ProviderType.MINIMAX: ProviderProfile(
        provider=ProviderType.MINIMAX,
        label="MiniMax",
        base_url="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_API_KEY",
        model_examples=["minimax-m2", "abab6.5-chat"],
        default_level=ModelLevel.AGENT,
        default_capabilities=_CLOUD_CAPS,
        notes="国内 minimaxi.com / 国际 minimax.io，账号互不通用。",
    ),
    ProviderType.OPENAI: ProviderProfile(
        provider=ProviderType.OPENAI,
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        model_examples=["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
        default_level=ModelLevel.EXPERT,
        default_capabilities=_CLOUD_CAPS,
    ),
    ProviderType.OLLAMA: ProviderProfile(
        provider=ProviderType.OLLAMA,
        label="Ollama (本地)",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env=None,
        model_examples=["qwen2.5-coder:7b", "qwen2.5-coder:14b", "deepseek-coder-v2:16b"],
        default_level=ModelLevel.TINY,
        default_capabilities=_LOCAL_CAPS,
        is_local=True,
        notes="`ollama serve` 启动后，先 `ollama pull <model>`。",
    ),
    ProviderType.VLLM: ProviderProfile(
        provider=ProviderType.VLLM,
        label="vLLM (本地)",
        base_url="http://127.0.0.1:8000/v1",
        api_key_env=None,
        model_examples=["Qwen/Qwen2.5-Coder-7B-Instruct"],
        default_level=ModelLevel.CODER,
        default_capabilities=_LOCAL_CAPS,
        is_local=True,
    ),
    ProviderType.LMSTUDIO: ProviderProfile(
        provider=ProviderType.LMSTUDIO,
        label="LM Studio (本地)",
        base_url="http://127.0.0.1:1234/v1",
        api_key_env=None,
        model_examples=["lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_LOCAL_CAPS,
        is_local=True,
    ),
    ProviderType.OPENAI_COMPATIBLE: ProviderProfile(
        provider=ProviderType.OPENAI_COMPATIBLE,
        label="其它 OpenAI-compatible",
        base_url="https://api.example.com/v1",
        api_key_env=None,
        model_examples=[],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
    ),
    ProviderType.CUSTOM: ProviderProfile(
        provider=ProviderType.CUSTOM,
        label="Custom (自定义 OpenAI-compatible)",
        base_url="https://your-endpoint/v1",
        api_key_env=None,
        model_examples=[],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
    ),
}


def get_profile(provider: ProviderType) -> ProviderProfile:
    """Return the profile for a provider, falling back to OPENAI_COMPATIBLE."""
    return PROFILES.get(provider, PROFILES[ProviderType.OPENAI_COMPATIBLE])
