# 多模态图像识别 — 设计文档

- 日期: 2026-06-19
- 范围: 让 ModelBridge 把**图像**喂给支持 vision 的模型识别。交互理念对齐 Claude Code —— **图片直接内联进你发给 AI 的那条消息**（一条消息＝图＋问题一起发），AI 当场读图当场回。来图方式：① `@图片文件` 内联提及 ② `@paste` 内联抓剪贴板截图 ③ 消息里的图片 URL 自动识别 ④ 单次 `ask --image` ⑤ AI 在 agent 循环里主动用 `view_image` 工具读图。
- 非范围（本期不做）: 子 Agent 派生（用户已同意拆成下一份 spec）；vision 自动改路由；下载远程图再转 base64。
- 兼容策略: 输入消息内容由 `str` 扩成 `str | list[block]`，输出（`ChatResponse.content`）**保持 `str` 不变**；下游读消息内容处统一走 `text_of()` 适配，避免破坏现有逻辑。复用现有 `@` 提及基建（[mentions.py](../../../modelbridge/agent/mentions.py) / [at_completer.py](../../../modelbridge/agent/at_completer.py) / [file_index.py](../../../modelbridge/project/file_index.py)）。
- 状态: 关键决策已获用户逐条口头批准（2026-06-19，含侦察 + 对"内容联合类型"破坏面的对抗性核查 + 用户明确要求"Claude Code 式内联贴图、非暂存式"后的设计修订）；待用户复核本文档。

---

## 1. 背景与现状

侦察（2 轮并行 agent 核查）确认的现状：

1. **`capabilities.vision` 旗标已存在但完全没人消费** —— 定义在 [models.py:77](../../../modelbridge/models.py) 与 [schemas.py:133](../../../modelbridge/schemas.py)，纯摆设，正好拿来当门禁。
2. **消息内容是纯字符串** —— `ChatMessage.content: str | None`（[schemas.py:34](../../../modelbridge/schemas.py)）。OpenAI 多模态要求 content 能装内容块数组 `[{"type":"text","text":...}, {"type":"image_url","image_url":{"url":...}}]`。
3. **传输层已经能透传数组** —— `to_wire()`（[schemas.py:41](../../../modelbridge/schemas.py)）对 content 用 `is not None` 守卫后原样塞进 dict；`build_chat_payload` / `_serialize_message`（[providers/base.py:122-153](../../../modelbridge/providers/base.py)）与全部 9 个 adapter 都不改写 content；httpx `json=body` 走 `json.dumps`，嵌套数组无损。**结论：传输/provider 层近乎零改动。**
4. **没有任何图像入口** —— `ask`（[cli.py:1033](../../../modelbridge/cli.py)）和 REPL（[loop.py:163](../../../modelbridge/agent/loop.py)）都只接受文本。
5. **确认门已就绪可复用** —— `ctx.confirm(...)`（[context.py:66](../../../modelbridge/agent/context.py)），审批面板在 [cli.py:770](../../../modelbridge/cli.py)。
6. **没有任何剪贴板/图像编码代码** —— 全新模块。
7. **已有 `@` 内联提及基建（关键复用点）** —— [mentions.py](../../../modelbridge/agent/mentions.py)：`find_mentions` 扫 `@token`、`resolve_mentions` 经 `FileIndex` 解析成 `Attachment`、`inject_file_mentions(text, index, session)` 把附件作为 user 消息注入（当前**只按文本读**文件）。补全器 `AtFileCompleter`（[at_completer.py](../../../modelbridge/agent/at_completer.py)）基于 **prompt_toolkit**（缺依赖/非 TTY 时回退纯读取器）。**图片输入直接扩展这套，而非另造入口。**

### 1.1 核查发现的关键风险（已据此调整设计）

把 `ChatMessage.content` 从 `str` 扩成联合类型，会波及对消息内容做字符串操作的代码。对抗性核查澄清了一个关键区分：

- **`ChatResponse.content`（助手*输出*）保持 `str` 不变** —— 因此所有 `resp.content` / `final.content` / `last_response.content` 的字符串操作（`Panel(resp.content)`、`estimate_tokens(resp.content)`、`extract_diff`、`_extract_json`、doctor 切片等）**不受影响**。
- **只有 `ChatMessage.content`（*输入*）会变成联合类型** —— 真正需要处理的"读消息内容"点是有限几处（见 §6）。

应对：新增一个 `text_of(content) -> str` 适配器（list → 取 text 块拼接、忽略 image 块；str → 原样），在这些读取点统一过一道。

---

## 2. 目标 / 非目标

**目标**
- 用户能把截图/图片文件/图片 URL 交给 vision 模型识别，单次（`ask`）与交互（REPL）都支持。
- AI 能在 agent 任务里主动读项目内的图片来"看"。
- 模型不支持 vision 时给清晰报错，绝不静默白烧 token。
- 不破坏现有文本路径、前缀缓存、思考模型（MiMo/Kimi/DeepSeek 的 `reasoning_content` 不变式）。

**非目标（本期显式排除）**
- 子 Agent 派生（下一份 spec）。
- 检测到带图时**自动**切换到 vision 模型（本期只报错+提示，手动切）。
- 远程 URL 下载后转 base64（本期 URL 直接透传给 provider 自取）。
- 视频/音频/PDF 等其它模态。

---

## 3. 功能范围一览

核心理念：**图片内联进消息内容**（非暂存式）。一条用户消息可同时含问题文本与一张/多张图，AI 当场读图当场回。

| 能力 | 入口 | 来图方式 |
|---|---|---|
| 内联图片文件 | REPL 消息里 `@<图片文件> 问题`（复用 `@` 提及 + 补全） | 本地路径 → base64 data URL，块并入该消息 content |
| 内联剪贴板截图 | REPL 消息里 `@paste 问题`（伪提及，复用同管道） | 系统剪贴板位图 → base64（Pillow，懒加载） |
| 内联图片 URL | REPL 消息里出现 `https://….png/.jpg/...` 自动识别 | 远程 URL → 透传（不下载） |
| 单次带图 | `mbridge ask --image <path\|url>`（可多次） | 本地→base64；URL→透传 |
| AI 主动读图 | 主工具 `read_file(path)`（vision 模型直接看到图像） | 本地路径 → base64，经 PathPolicy 安全校验 |

---

## 4. 架构

### 4.1 消息 schema：内容联合类型 + `text_of()`

```python
# schemas.py
ContentBlock = dict[str, Any]            # {"type": "text"|"image_url", ...}

class ChatMessage(BaseModel):
    content: str | list[ContentBlock] | None = None   # 由 str|None 扩展
    ...

def text_of(content: str | list[ContentBlock] | None) -> str:
    """把消息内容收敛成纯文本：str 原样；list 取 text 块拼接、忽略图像块；None→''。"""
```

- `to_wire()` 已对 content 用 `is not None` 守卫，**无需改**（list 会原样透传）。
- `ChatResponse.content` **不动**，仍是 `str`。
- 约束：**system / tool 消息永远是纯文本**；只有 user 消息（用户贴图）和 `view_image` 注入的 user 消息携带图像块。

### 4.2 图像解析模块 `modelbridge/images.py`（新增）

单一职责：把"来源"统一成一个 `image_url` 内容块。

```python
def block_from_path(path: str) -> ContentBlock      # 读字节→猜 mime→base64 data URL
def block_from_url(url: str) -> ContentBlock         # 远程 URL 透传；data: URL 原样
def block_from_clipboard() -> ContentBlock           # Pillow ImageGrab，懒导入
def resolve_image_arg(arg: str) -> ContentBlock      # 自动判别 http(s)://、data:、本地路径
def text_block(text: str) -> ContentBlock            # {"type":"text","text":text}
```

- **mime 探测**：扩展名优先 + 魔数兜底（`png/jpeg/gif/webp`）。无法识别 → 报错。
- **体积守卫**：编码后超过 `MAX_IMAGE_BYTES`（常量，默认 10 MB）→ 报错并提示压缩；base64 膨胀 ~33% 计入。
- **Pillow 仅剪贴板用到**：`block_from_clipboard` 内 `try: from PIL import ImageGrab except ImportError` → 友好提示 `pip install "modelbridge[vision]"`。路径/URL 路径零依赖。
- 剪贴板取不到图（剪贴板是文本/空）→ 报错提示"先截图再 `@paste`"。

### 4.3 vision 门禁

发送任何"含图像块的请求"前校验解析到的模型 `entry.capabilities.vision`：

```python
def ensure_vision(entry, has_images: bool) -> None:
    if has_images and not entry.capabilities.vision:
        raise ... # 列出 models.yaml 里 capabilities.vision=true 的模型，提示 --model / /model 切换
```

调用点：`ask`（解析完图与模型后、发送前）；REPL 消费贴图组装 user 消息时；`view_image` 工具仅在 vision 模型下注册（见 4.6），故工具路径天然不触发。

### 4.4 `ask --image`（单次）

- `cmd_ask`（[cli.py:1033](../../../modelbridge/cli.py)）新增 `--image PATH_OR_URL`（`List[str]`，可多次）。
- 经 `resolve_image_arg` 解析为图像块；`PromptBuilder.with_user_request(text, images=[...])`（[prompt/builder.py](../../../modelbridge/prompt/builder.py)）把 user 段 content 组成 `[text_block(text), *image_blocks]`。
- **前缀缓存不受影响**：图像块落在动态 user 段（非稳定前缀 1-5 段），`stable_prefix_hash` 不变。
- 发送前 `ensure_vision`。

### 4.5 REPL：内联图片（扩展 `@` 提及，**非暂存**）

理念：图片是**用户那条消息内容的一部分**，与问题同轮发送。复用现有 `@` 提及管道，**不引入暂存缓冲、不加 `/image` `/paste` 斜杠命令**。

**(a) `@<图片文件>` —— 图像型附件**
- `mentions.py` 现在把每个 `@token` 解析成 `Attachment`（文本）。扩展：`Attachment` 增 `kind`（`"file"|"dir"|"image"`）与可选 `block: ContentBlock | None`。
- `_resolve_token` 命中的文件若扩展名属图像集（`.png/.jpg/.jpeg/.gif/.webp/.bmp`）→ 走图像分支：经 `images.block_from_path` 编码为 `image_url` 块，存进 `Attachment.block`，**不读文本**。
- 补全器无需改（`AtFileCompleter` 已对所有索引文件给补全，图片文件自然在列）。

**(b) `@paste` —— 剪贴板伪提及**
- `resolve_mentions` 特判保留字 `@paste`（及 `@clipboard` 别名）：跳过 `FileIndex`，调 `images.block_from_clipboard` → 图像 `Attachment`。剪贴板无图/Pillow 缺失 → 记为 `unresolved` 并给友好提示（不抛）。
- 可选：`AtFileCompleter` 把 `paste` 作为一个补全项提示存在（nice-to-have，非必须）。

**(c) 图片 URL 自动识别**
- 在组装用户消息前，用正则扫描问题文本里的图片 URL（`https?://\S+\.(png|jpe?g|gif|webp)(\?\S*)?`）→ `images.block_from_url` 透传为图像块。URL 文本仍原样留在消息里作标签。

**注入与分轮（关键差异）**
- **文本/目录附件**：保持现状 —— `inject_file_mentions` 作为独立 user 消息注入在问题*之前*（参考材料在前、问题在后）。
- **图像附件（含 @paste、URL）**：**并入用户那条问题消息的 content**，即 `Session.add_user(text, images=[block, ...])` → content = `[text_block(text), *image_blocks]`。真正"图片加进消息内容"。
- 为此：`inject_file_mentions`（或新并行函数 `resolve_inline_attachments`）返回收集到的图像块列表给 REPL 调用点；文本附件照旧 append，图像块交给 `add_user`。
- `Session.add_user` 扩签名 `add_user(content: str, images: list[ContentBlock] | None = None)`：无图时行为不变；有图时 content 设为 `[text_block(content), *images]`。

**vision 门禁**：组装含图像块的 user 消息前对当前模型 `ensure_vision`；不通过 → 拒绝本轮、提示 `/model` 切换并列出可用 vision 模型，**不发送**。

> 回退路径（无 prompt_toolkit / 非 TTY）：`@文件`、`@paste`、URL 识别全部基于纯文本解析，与补全器无关，**回退读取器下同样可用**（只是没有 `@` 自动补全）。

### 4.6 AI 读图：主工具 `read_file` 直接读图 + `ToolResult.extra_messages`

> **修订（2026-06-19，实现后）**：取消独立的 `view_image` 工具，改为让**主命令 `mbridge` 里既有的 `read_file` 工具**直接处理图片——AI 不需要学一个新命令，读图片就用 `read_file`。`read_file` 命中图片扩展名时：vision 模型 → 走下面的图像块注入；非 vision 模型 → 返回"这是图片、当前模型无法识别"的文字说明（不再吐乱码）。是否 vision 由 `AgentContext.model_is_vision`（REPL 构造时按当前模型 `capabilities.vision` 设置）决定。下文机制不变，仅触发入口从 `view_image` 改为 `read_file`。

OpenAI 兼容 API 的 `role=tool` 消息**只能纯文本**，没法直接回图。机制：

1. `ViewImageTool`（新增 [agent/tools/image_tools.py](../../../modelbridge/agent/tools/image_tools.py)）`execute(path)`：经 `ctx.policy`/`ctx.resolve` 做安全校验（须在 allowed dir、非敏感文件），`block_from_path` 编码。
2. 返回 `ToolResult(content="已加载 <name> (<w>x<h>)，见下条消息", extra_messages=[ChatMessage(role="user", content=[text_block("[view_image 加载的图片: <name>]"), image_block])])`。
3. `ToolResult` 新增可选字段 `extra_messages: list[ChatMessage] | None = None`。
4. agent 循环（[loop.py:133](../../../modelbridge/agent/loop.py)）在 `add_tool_result` 之后，若 `result.extra_messages` 非空则逐条 `session.messages.append`。下一轮模型即可"看到"图。
5. **注册策略**：`view_image` 仅在当前模型 `capabilities.vision=true` 时注册进 registry（无 vision 不给模型这个工具，避免无效调用）。它是用户明确要的能力，故在 vision 模型下**默认开启**。

> 不变式保护：注入的是 **user** 消息，不触碰 assistant 轮的 `reasoning_content`，MiMo/Kimi/DeepSeek 思考链不受影响。

### 4.7 token / 成本 / 上下文窗口计量

- 凡对 **`ChatMessage.content`** 估 token 处改为 `estimate_tokens(text_of(m.content))`，最关键是 [context/windows.py:183](../../../modelbridge/context/windows.py)（遍历历史计 token）。
- 图像 token 成本不在本期精确建模（各家算法不一）；估算仅计文本块，图像按 0 计并在文档注明"图像 token 未计入估算，实际以 provider 账单为准"。
- 成本记账走 `resp.usage`（provider 实报）优先，本就不依赖输入 content 类型，**不受影响**。

---

### 4.8 新增 / 扩展文件一览

| 文件 | 动作 | 内容 |
|---|---|---|
| `modelbridge/images.py` | 新增 | `block_from_path/url/clipboard`、`resolve_image_arg`、`text_block`、mime/体积守卫、`MAX_IMAGE_BYTES` |
| `modelbridge/agent/tools/image_tools.py` | 新增 | `ViewImageTool` |
| [schemas.py](../../../modelbridge/schemas.py) | 改 | `ChatMessage.content` 联合类型 + `text_of()` |
| [agent/mentions.py](../../../modelbridge/agent/mentions.py) | 改 | `Attachment.kind/block`；图像扩展名分支；`@paste` 特判；返回图像块给调用点 |
| [agent/session.py](../../../modelbridge/agent/session.py) | 改 | `add_user(content, images=None)` |
| [agent/tools/base.py](../../../modelbridge/agent/tools/base.py) | 改 | `ToolResult.extra_messages` |
| [agent/loop.py](../../../modelbridge/agent/loop.py) | 改 | 追加 `extra_messages` 进 session |
| [agent/tools/registry.py](../../../modelbridge/agent/tools/registry.py) | 改 | vision 模型下注册 `view_image` |
| [cli.py](../../../modelbridge/cli.py) | 改 | `ask --image`；REPL 内联组装调用点；vision 门禁 |
| [prompt/builder.py](../../../modelbridge/prompt/builder.py) | 改 | `with_user_request(text, images=None)` |
| §6 各处 | 改 | `text_of()` 落点 |
| `pyproject.toml` | 改 | 可选 extra `vision = ["Pillow>=10"]` |

## 5. 依赖

- **Pillow**：列为可选 extra —— `pyproject.toml` 增 `[project.optional-dependencies] vision = ["Pillow>=10"]`。仅 `@paste` 剪贴板路径懒加载用到；未安装时该路径友好报错，其余功能不受影响。

---

## 6. 破坏面与整改清单（`text_of()` 落点）

核查区分后，**真正需要改的"读 `ChatMessage.content`"点**（`resp.content` 系列是 `ChatResponse`，保持 str，不在此列）：

| 位置 | 现状 | 整改 |
|---|---|---|
| [context/windows.py:183](../../../modelbridge/context/windows.py) | `estimate_tokens(m.content)` | `estimate_tokens(text_of(m.content))` ⭐关键 |
| [agent/commands.py:161](../../../modelbridge/agent/commands.py) | `(m.content or "").replace(...).strip()` | `text_of(m.content).replace(...)` |
| [agent/ui.py:223,280](../../../modelbridge/agent/ui.py) | 渲染消息 content（用户贴图回显） | 渲染前过 `text_of()`，图像块显示为 `🖼 <name>` 占位 |
| [cli.py:1010](../../../modelbridge/cli.py) | `"\n".join(m.content or "" ...)` 拼 builder 文本 | `text_of(m.content)` |
| [cli.py:508](../../../modelbridge/cli.py) | `initial.messages[0].content or ...`（system 段） | system 恒为 str，加防御 `text_of()` |
| [agent/commands.py:554](../../../modelbridge/agent/commands.py)、[mcp/adapters/prompt_adapter.py:34](../../../modelbridge/mcp/adapters/prompt_adapter.py) | `ChatMessage(content=m.content)` 复制 | 透传 list 安全；保留原样 |
| [agent/session.py:64](../../../modelbridge/agent/session.py) | `content=resp.content or ""` | `resp` 是 ChatResponse(str)，**不受影响** |

> 验证手段：实现期跑全量测试 + mypy；并对每个改点加针对性单测（见 §7）。

---

## 7. 测试计划

- **schema/text_of**：`to_wire()` 原样透传 list；`text_of` 对 str/list/None/混合块的收敛。
- **images.py**：路径→data URL（含 mime 探测、魔数兜底、缺文件报错、超限报错）；URL 透传；`data:` 原样；剪贴板成功（mock `PIL.ImageGrab.grabclipboard` 返回 `Image`）+ 无图报错 + Pillow 缺失报错。
- **mention 图像扩展**（纯函数，免终端）：`resolve_mentions` 对 `@图片文件` 产出 `kind="image"` 且带 `image_url` 块、不读文本；`@code.py` 仍走文本分支；`@paste` 特判走剪贴板；`@paste` 剪贴板空 → `unresolved`。
- **URL 自动识别**：问题文本含 `https://….png` → 抽出图像块；非图 URL / 普通文本不误伤。
- **vision 门禁**：非 vision 模型带图 → 明确异常且列出可用 vision 模型。
- **ask --image**：注入假 provider 捕获 payload，断言 user content 为 `[text, image_url]`，且稳定前缀 hash 不变。
- **REPL 内联组装**：`@图片 问题` → 一条多模态 user 消息 `[text, image_url]`（图并入问题同轮，非独立暂存）；`@code.py @图片 问题` → 文本附件作前置消息、图像并入问题消息；非 vision 模型 → 拒绝本轮、不发送。
- **view_image + loop**：工具返回 `extra_messages`；loop 把 user 图像消息追加进 session；PathPolicy 拦截敏感文件。
- **回归**：`context/windows` token 计数对含图历史不抛错；MiMo `reasoning_content` 往返不变；纯文本 `@mention` 行为不变。

---

## 8. 文档更新

- README `## 命令一览` 增 `ask --image`；新增"多模态/图像"小节（内联 `@图片` / `@paste` / 图片 URL + `ask --image` + `view_image` + vision 模型如何在 `models.yaml` 标 `capabilities.vision: true` + Pillow 可选装）。
- README 现有 `@` 提及说明处补充"图片文件会作为图像内联识别"。
- `provider_profiles.py`（可选）：为已知 vision 型号预置 `vision=true` 提示（数据便利，非必须）。

---

## 9. 风险与待办

1. **国产 vision 模型格式 verbatim 兼容未证实**（GLM-4V / Qwen-VL / Kimi 是否原样吃 OpenAI `image_url`）。缓解：保留 per-provider `build_chat_payload`/`_serialize_message` 覆写口子；实现期对至少一个真实 vision 模型做手测；文档注明已验证型号。
2. **剪贴板跨平台差异**：Pillow `ImageGrab` 在 Windows/macOS 可靠，Linux 需 `xclip`/`wl-paste`。本期主保 Windows（用户环境），其余平台失败即友好报错。
3. **图像 token 成本未精确计量**：文档明确告知"以账单为准"。

---

## 10. 未来（显式排除，留待后续 spec）

- 子 Agent 派生（用户确认门 + 成本警告）—— 下一份 spec。
- 带图时自动改路由到 vision 模型。
- 远程 URL 下载落地再编码 / 图像压缩。
- 工具结果原生带图（Anthropic 风格 tool_result 图像，待 provider 支持）。
- 真·Ctrl+V/快捷键拦截贴图（prompt_toolkit 键绑定，仅 TTY 可用）—— 本期用 `@paste` 伪提及替代，键绑定留作体验增强。
