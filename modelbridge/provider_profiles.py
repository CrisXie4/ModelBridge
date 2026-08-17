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
        model_examples=["deepseek-v4-pro", "deepseek-v4-flash"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_THINKING_CAPS,
        notes="OpenAI 端点 https://api.deepseek.com；Anthropic 兼容端点 /anthropic。V4 系列默认思考，返回 reasoning_content；带工具调用的历史轮必须回传 reasoning_content。ChatRequest.thinking 自动翻译为 thinking 参数，预算映射 reasoning_effort (high/max)。峰谷计价，闲时半价；deepseek-chat/reasoner 已于 2026-07-24 下线。前缀缓存按「厂商×模型」隔离（切模型即换缓存域，flash↔pro 不共享）；请求体无缓存键字段（dsh 线规），ModelBridge 不注入。",
    ),
    ProviderType.QWEN: ProviderProfile(
        provider=ProviderType.QWEN,
        label="Qwen / 阿里云百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        model_examples=[
            "qwen3.8-max",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3-coder-plus",
            "qwen3-coder-flash",
            "qwen-plus-latest",
            "qwen-long",
        ],
        default_level=ModelLevel.CODER,
        default_capabilities=_CLOUD_CAPS,
        notes="thinking 系列需要 enable_thinking=true，由 ChatRequest.thinking 自动设置。千问Max/Plus 走阶梯计价，价格表记录的是 ≤第一档。",
    ),
    ProviderType.KIMI: ProviderProfile(
        provider=ProviderType.KIMI,
        label="Kimi (Moonshot AI)",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        model_examples=[
            "kimi-k3",
            "kimi-k2.7-code",
            "kimi-k2.6",
        ],
        default_level=ModelLevel.EXPERT,
        default_capabilities=_THINKING_CAPS,
        notes="OpenAI 端点 https://api.moonshot.ai/v1；Anthropic 兼容端点 /anthropic。K3 为旗舰 (2.8T 开源权重、多模态推理、1M ctx、默认 reasoning，$3/$15)；K2.7-code 编码专用；K2.6 thinking 模型返回 reasoning_content；temperature 通常应=0。",
    ),
    ProviderType.MIMO: ProviderProfile(
        provider=ProviderType.MIMO,
        label="MiMo (小米)",
        base_url="https://api.xiaomimimo.com",
        api_key_env="MIMO_API_KEY",
        model_examples=["mimo-v2.5-pro", "mimo-v2.5-pro-ultraspeed", "mimo-v2.5"],
        default_level=ModelLevel.AGENT,
        default_capabilities=_THINKING_CAPS,
        notes="★ thinking + tool_calls 必须回传 reasoning_content，否则 400。V2 系列已于 2026-06-30 下线，请用 V2.5。Token Plan 用 https://token-plan-cn.xiaomimimo.com。",
    ),
    ProviderType.GLM: ProviderProfile(
        provider=ProviderType.GLM,
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPU_API_KEY",
        model_examples=["glm-5.2", "glm-5.1"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
    ),
    ProviderType.MINIMAX: ProviderProfile(
        provider=ProviderType.MINIMAX,
        label="MiniMax",
        base_url="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_API_KEY",
        model_examples=["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5"],
        default_level=ModelLevel.AGENT,
        default_capabilities=_CLOUD_CAPS,
        notes="OpenAI 兼容 https://api.minimaxi.com/v1；Anthropic 兼容 /anthropic。国内 minimaxi.com / 国际 minimax.io，账号互不通用。model id 官方写法首字母大写含点(MiniMax-M3)；M3 当前 50% 促销。⚠ MiniMax-H3 (2026-07-31 发布) 是全模态视频生成模型 (文/图生视频、2K、15s、¥0.8/秒)，走视频生成 V2 异步 API，不能配置为对话模型。",
    ),
    ProviderType.HUNYUAN: ProviderProfile(
        provider=ProviderType.HUNYUAN,
        label="腾讯混元",
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        api_key_env="HUNYUAN_API_KEY",
        model_examples=["hy3"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="OpenAI 兼容端点 https://api.hunyuan.cloud.tencent.com/v1 (Bearer sk-xxx，控制台「API Key 管理」创建；原生 TC3 签名 API 已不需要)。hy3 正式版 GA (快慢思考融合 MoE 295B/A21B，think/no_think 双模式，¥1/¥4、缓存命中 ¥0.25，已开源)；preview 已被替代。国内直连，TokenHub 闲时半价。",
    ),
    ProviderType.DOUBAO: ProviderProfile(
        provider=ProviderType.DOUBAO,
        label="豆包 (字节火山方舟)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_API_KEY",
        model_examples=[
            "doubao-seed-evolving",
            "doubao-seed-1.8",
            "doubao-seed-1.6",
            "doubao-seed-2.0-lite",
        ],
        default_level=ModelLevel.AGENT,
        default_capabilities=_CLOUD_CAPS,
        notes="OpenAI 兼容 https://ark.cn-beijing.volces.com/api/v3 (注意 api/v3)；model 填模型别名或推理接入点 ep-xxxx。seed 系列默认深度思考，ChatRequest.thinking 自动翻译为 thinking 参数。Seed-Evolving 为最新 Coding & Agent 旗舰；定价以方舟价格文档为准 (docs.volcengine.com/docs/82379/1544106)。",
    ),
    ProviderType.ERNIE: ProviderProfile(
        provider=ProviderType.ERNIE,
        label="百度文心 (千帆)",
        base_url="https://qianfan.baidubce.com/v2",
        api_key_env="QIANFAN_API_KEY",
        model_examples=[
            "ernie-4.5-turbo-128k",
            "ernie-4.0-turbo-8k",
            "ernie-speed-128k",
            "ernie-x1-turbo-32k",
        ],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="千帆 OpenAI 兼容 v2 接口 https://qianfan.baidubce.com/v2 (Bearer；v1 的 AK/SK access_token 已过时)。ernie-x1 为推理系列，返回 reasoning_content。Speed/Lite 档便宜，适合 tiny/cheap 路由。",
    ),
    ProviderType.SPARK: ProviderProfile(
        provider=ProviderType.SPARK,
        label="讯飞星火",
        base_url="https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1",
        api_key_env="XINGHUO_API_KEY",
        model_examples=["x2", "x2-flash", "x2-agent"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="X2 系列走星辰 MaaS (base_url 即左侧)；旧款 lite/generalv3.5/4.0Ultra 走 https://spark-api-open.xf-yun.com/v1 + APIPassword。新用户有 1500 万 tokens 体验额度，Lite 永久免费。X2 192K / X2-Flash·X2-Agent 256K。",
    ),
    ProviderType.STEPFUN: ProviderProfile(
        provider=ProviderType.STEPFUN,
        label="阶跃星辰 StepFun",
        base_url="https://api.stepfun.com/v1",
        api_key_env="STEPFUN_API_KEY",
        model_examples=["step-3", "step-2-16k", "step-2-mini"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="OpenAI 兼容 https://api.stepfun.com/v1。step-3 为全模态旗舰 (文本+图像输入)；step-2 系列纯文本。模型/价格以 platform.stepfun.com 为准。",
    ),
    ProviderType.SENSENOVA: ProviderProfile(
        provider=ProviderType.SENSENOVA,
        label="商汤日日新",
        base_url="https://api.sensenova.cn/compatible-mode/v1",
        api_key_env="SENSENOVA_API_KEY",
        model_examples=["SenseNova-V5-Turbo", "SenseNova-V5-Pro"],
        default_level=ModelLevel.CHEAP,
        default_capabilities=_CLOUD_CAPS,
        notes="OpenAI 兼容端点 /compatible-mode/v1 (原生 /v1 是 JWT 签名不能用)。model id 官方写法 SenseNova-V5-*。模型/价格以日日新控制台为准。",
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
