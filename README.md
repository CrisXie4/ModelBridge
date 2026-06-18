<p align="center">
  <img src="assets/icon.png" alt="ModelBridge" width="128" />
</p>

# ModelBridge

> **国产模型优先的 AI Coding Agent + 兼容 CLI。**
>
> 直接运行 `mbridge` 进入**持续会话** (像 Claude Code) — AI 可以读 / 写 / 编辑项目文件，需要时也能跑 shell。管理类操作 (添加模型、自检、路由、成本、预算、缓存) 走子命令。
>
> 支持 DeepSeek / Qwen / Kimi / MiMo / GLM / MiniMax / Ollama / vLLM / LM Studio。Provider Adapter 层吸收国产模型字段差异 (`reasoning_content`、`thinking`、`tool_calls`)。

---

## 命令一览

```
mbridge                            进入持续会话 REPL (默认 default_model)
mbridge -m NAME --cwd PATH         切换模型 / 工作目录
mbridge --allow-bash               额外开启 run_bash 工具 (每条命令仍要确认)
mbridge --yes                      跳过所有 write/edit/bash 确认弹窗

mbridge init                       初始化 ~/.modelbridge/
mbridge model init / add / list / remove   模型管理 (init 推荐入口)
mbridge config show / upgrade              查看 / 升级 config.yaml
mbridge config profile add/list/use/show/remove   命名配置切换

mbridge ask "..."                  单轮请求 (非交互；--route/--auto 自动路由；--fallback 失败升级)
mbridge edit "..."                 让 AI 生成 diff 改代码 (review→apply→backup→rollback；--undo 回滚上次)
mbridge run "pytest -x"            在项目内安全执行白名单 shell 命令
mbridge route "..."                路由分析 (输出等级与模型，不实调；加 --mode)

mbridge doctor                     全局自检
mbridge doctor model NAME / doctor all / doctor route   模型 / 全部 / 路由自检

mbridge usage cost "..."           成本估算
mbridge usage budget [set ...]     月度预算 查看 / 设置
mbridge usage cache                缓存命中统计

mbridge prompt list / show / edit / set-system / reset   提示词与规则文件
mbridge project scan / rules / rules init                项目扫描 / 规则 / 生成 AGENT.md
mbridge mcp list / tools / resources / prompts           MCP 客户端
mbridge mcp serve                  把 ModelBridge 自己作为 MCP server
mbridge bridge install / status / on / off               浏览器侧边栏宿主 (装扩展见下方章节)

mbridge version [--check]          显示版本号
mbridge --version / -V             同上 (任意位置)
mbridge update [--yes]             检查并下载新版本
```

> 旧命令名（chat / cost estimate / budget / cache / profile / bridge control … ）仍可用但会提示已迁移，将在 v2.0 移除。

> **版本与自动更新**：REPL 启动时会显示当前版本，并在每天检查一次 GitHub
> Release。发现新版本时会提示 `🔔 发现新版本 vX.Y.Z`，此时直接输入 **同意**
> （或 `/update`）即可自动下载对应平台的安装包并打开所在目录，按提示完成安装。
> 检查结果缓存在 `~/.modelbridge/update_check.json`，离线 / 失败时静默跳过，不影响使用。

## 安装

```bash
pip install -e .
```

`pyproject.toml` 注册了 `mbridge = "modelbridge.cli:app"` 入口，安装后可直接使用 `mbridge` (别名 `modelbridge`)。

### Shell 自动补全

```bash
mbridge --install-completion        # 为当前 shell (bash/zsh/fish/powershell) 安装补全
mbridge --show-completion           # 只打印补全脚本，自行决定如何接入
```

安装后重开终端即可对子命令 (`model` / `doctor` / `route` / `usage` / `config` …) 和选项做 Tab 补全。

## 起步

```bash
# 1. 初始化
mbridge init

# 2. 添加一个模型
export DEEPSEEK_API_KEY=sk-xxx
mbridge model init             # 选 DeepSeek → 输入 deepseek-chat

# 3. 自检
mbridge doctor

# 4. 进入持续会话
cd ~/my-project
mbridge                        # AI 在 ~/my-project 内读/写/编辑

# 或换个模型
mbridge -m qwen-coder --cwd ~/my-project --allow-bash
```

---

## 持续会话 (REPL)

直接运行 `mbridge`，进入交互式会话 — 你可以连续输入指令，AI 一直保留上下文，可以主动调用工具读写文件。

```
$ mbridge -m deepseek-chat
┌─ mbridge ──────────────────────────────────────────────┐
│ ModelBridge agent REPL                                 │
│ model     : deepseek-chat                              │
│ cwd       : /home/me/my-project                        │
│ tools     : list_dir, read_file, str_replace, write_file
│ approval  : 每次询问                                   │
│ allow_bash: False                                      │
└────────────────────────────────────────────────────────┘

you: 看一下 src/auth.py，告诉我 login 函数有什么问题
[tool · read_file (path=src/auth.py)] ...
[assistant] 这个函数有几个问题：1) ... 2) ...

you: 帮我用 str_replace 修掉第二个
[tool · str_replace (path=src/auth.py, old_str=…)]
   批准 · str_replace
   - if token == None:
   + if token is None:
   执行?  [y]es / [N]o / [a]lways  y
[assistant] 已修复。运行测试确认一下？

you: /exit
[bye]
session saved → ~/.modelbridge/sessions/2026-05-23_153012_repl_deepseek-chat.json
```

**特殊输入**：

- `/exit` / `/quit` — 退出
- `/clear` — 清空历史 (system prompt 保留)
- Ctrl-D — 退出

**会话历史**保存到 `~/.modelbridge/sessions/<ts>_<label>.json`，完整保留 `reasoning_content` 和 `tool_calls` (MiMo / Kimi-thinking 多轮兼容)。

### 工具集

| 工具 | 用途 | 需要确认 |
|---|---|---|
| `read_file(path)` | 读取文件 (200 KB 上限，超出截断) | 否 |
| `list_dir(path)` | 列目录 (默认隐藏点文件) | 否 |
| `write_file(path, content)` | 覆盖 / 创建文件 (500 KB 上限) | **是** |
| `str_replace(path, old_str, new_str)` | 精确替换；要求 old_str 在文件中唯一出现 | **是** |
| `run_bash(command)` | 执行 shell；默认 30s 超时；输出截断到 8 KB | **是** (且需 `--allow-bash` 启用) |

确认弹窗有三个选项：

- **y** — 同意这一次
- **N** — 拒绝 (默认)
- **a** — 本会话内对该工具始终同意

### 安全模型

文件路径有两层防护：

1. **白名单** — 必须落在 `config.yaml: security.allowed_project_dirs` 之内 (或当前 `--cwd`)。Symlink 解析后再校验，不能逃逸。
2. **黑名单** — 命中 `block_sensitive_files` 模式 (`.env`、`id_rsa`、`.ssh`、`config.json`、`secrets.yaml` 等) 一律拒绝。

`run_bash` **默认关闭**。即使开启 (`--allow-bash`)，AI 每条命令仍会请求确认 (除非 `--yes`)。需要更强隔离请把 ModelBridge 跑在容器里。

### 国产模型兼容

REPL 会话里：

- **MiMo** thinking + tool_calls：assistant 消息原样回传 (`reasoning_content` + `raw`)，不会被清洗，避免 400。
- **Kimi / DeepSeek-reasoner** thinking 模型：`reasoning_content` 完整保留，多轮不丢。
- **Qwen 百炼**：`thinking` / `thinking_budget` 走 adapter 自动转换。
- **Ollama / vLLM / LM Studio**：本地模型若不支持 tools，会在第一次调用时报清晰错误而不是静默失败。

---

## 浏览器侧边栏 — 让 AI 读写当前网页 (可选)

> 只有当你想让 AI **读取 / 操作浏览器里正在看的网页**（总结页面、按 CSS 选择器点按钮、填表单、跳转）时才需要它。纯命令行问答 / 改代码 **不需要**装扩展。

一个 Chrome/Edge 的 MV3 侧边栏插件 + 本机 Native Messaging 宿主（LocalBridge）：在侧边栏里聊天，AI 复用 CLI 的同一套引擎，通过宿主读写你**当前标签页**。

> ⚠️ 本扩展**未上架 Chrome 应用商店**，自托管只能用「加载已解压」（load-unpacked）。**不提供 `.crx`** —— Chrome 会拦截并停用自签名 crx（报 `CRX_REQUIRED_PROOF_MISSING` 或「此扩展不是来自任何已知来源」）。如需批量部署可走企业策略 `ExtensionInstallForcelist`。

**安装步骤：**

**1. 下载并解压**：<https://github.com/CrisXie4/ModelBridge/releases/latest/download/modelbridge-extension.zip>
解压后得到 `modelbridge-extension/` 文件夹（扩展本体）+ `INSTALL.txt`。

**2. 注册 Native Messaging 宿主**（让浏览器能拉起 LocalBridge）：

```bash
mbridge bridge install
mbridge bridge status          # 查看注册位置 / 扩展 ID
```

**3. 在浏览器里加载**：打开 `chrome://extensions` → 右上角开启 **开发者模式** → 点 **加载已解压的扩展程序** → 选解压出的 `modelbridge-extension` 文件夹。
4. 因为浏览器从 GUI 启动宿主、读不到 shell 的环境变量，**API key 必须放在 keyring / `config.yaml`** 里（不能只靠 `export`）。
5. 回到 `chrome://extensions` **重新加载** 扩展，即可在侧边栏使用。

> 扩展 ID 已由 manifest key 固定（所有安装一致），`mbridge bridge install` 默认用官方固定 ID；只有你 fork 改了 key 才需要 `--extension-id`。

侧边栏里 AI 可自动调用的网页工具（操作类会先请求确认）：`read_page` / `get_selection` / `query_dom` / `extract` / `click` / `fill` / `navigate`。

---

## 项目结构

```
modelbridge/
├── cli.py                # Typer 入口；mbridge (no args) → REPL
├── config.py             # ~/.modelbridge/{config,models}.yaml
├── models.py             # ModelEntry / Capabilities / ProviderType / ModelLevel
├── schemas.py            # ChatMessage / ChatRequest / ChatResponse / ProviderError
├── client.py             # chat_once (mbridge ask 用)
├── doctor.py             # 自检
├── error_hints.py        # 中文错误诊断
├── raw_logger.py         # --verbose 时 raw 响应落盘
├── provider_profiles.py  # `model init` 简化流程
├── utils.py              # 路径 / 脱敏 / 日志
│
├── providers/            # Provider Adapter
│   ├── base.py registry.py openai_compatible.py
│   └── deepseek/qwen/kimi/mimo/glm/minimax/ollama/local_openai.py
│
├── router/               # 模型路由
│   └── classifier.py · fallback.py · router.py
│
├── cost/                 # 成本与预算
│   └── estimator.py · budget.py
│
├── cache/                # 缓存统计
│   └── manager.py
│
└── agent/                # ★ 持续会话 + 工具
    ├── security.py       # PathPolicy
    ├── context.py        # AgentContext + Approval 回调
    ├── session.py        # 持久化到 sessions/
    ├── loop.py           # run_interactive / run_agent_turn
    └── tools/
        ├── base.py registry.py
        ├── file_tools.py # read/list/write/str_replace
        └── bash_tool.py  # run_bash (opt-in)
```

---

## 第三阶段：路由 · 成本 · 缓存

ModelBridge 的核心能力之一是：**简单任务自动用便宜 / 本地模型，复杂任务自动升级到强模型**，帮 AI Coding Agent 长期降本。

### 1. 模型路由

`mbridge route "..."` 把一句 prompt 分类到 5 个等级，按 `config.yaml routing.levels` 映射到具体模型：

| 等级 | 用途 |
|---|---|
| `tiny` | 意图分类 / 是非判断 / 便宜预处理 |
| `cheap` | 普通问答 / 解释报错 / 简单代码解释 |
| `coder` | 单文件代码生成 / 简单 bug 修复 / 生成 diff |
| `agent` | 多文件任务 / 工具调用 / MCP |
| `expert` | 架构重构 / 安全审查 / 多次失败的兜底 |

输出包含 `task_type` (chat / explain / code_generate / code_edit / debug / architecture / security_review / agent_task / refactor / unknown)、`complexity` (simple / medium / hard)、`risk_level`、`cache:supported?`、`cost_band`。

### 2. 路由模式 economy / balanced / powerful

`config.yaml routing.mode` 决定全局口味；也可单次用 `--mode` 覆盖：

| 模式 | 取舍 |
|---|---|
| `economy` | 尽量 tiny / cheap，失败再升级，偏好本地 / cache 友好模型 |
| `balanced` | **默认** — 普通问 cheap、代码 coder、复杂 agent，失败 fallback expert |
| `powerful` | 代码直接 coder/agent，架构直接 expert，少考虑成本 |

例：

```bash
mbridge route "帮我修复这个项目里的登录 bug" --mode economy   # → cheap
mbridge route "帮我修复这个项目里的登录 bug" --mode balanced  # → coder
mbridge route "帮我修复这个项目里的登录 bug" --mode powerful  # → agent
mbridge route test                                            # 8 题套件
```

### 3. 自动调用：`ask --route` / `ask --auto`

`mbridge ask "..." --route` 先路由再调用；`--fallback` 让调用失败时按 `tiny→cheap→coder→agent→expert` 向上重试，受 `routing.fallback.max_upgrade_steps` 限制（默认 2）。

```bash
mbridge ask "解释这个报错" --route                # 自动 cheap
mbridge ask "写一个 FastAPI hello" --route --fallback
mbridge ask "..." --route --mode powerful --fallback
```

升级触发条件：超时、429、provider 400、空内容、模型明确说做不了。

### 4. 成本估算 + `pricing.yaml`

价格按四级优先级解析：

1. 显式 `rate_override`（程序内）
2. `models.yaml` 中该模型的 `extra.pricing` 块
3. `~/.modelbridge/pricing.yaml`（参考 `examples/pricing.example.yaml`）
4. 内置 `DEFAULT_PRICING` 表
5. 若为本地模型 → 0
6. 否则报 `PricingNotFound`

示例 `pricing.yaml`：

```yaml
pricing:
  deepseek-chat:
    input_per_1m: 0.27
    output_per_1m: 1.10
    currency: USD
    cache_hit_input_per_1m: 0.027
```

### 5. 预算守卫

`budget.json` 同时跟踪**月度**与**每日**开销，每次 `chat` 调用后自动累加：

```bash
mbridge usage budget set --monthly 30 --daily 2 --warn-at 80 --hard-stop
mbridge usage budget
```

- `--warn-at N`：到达 N% 时打 warning。
- `--hard-stop`：超额时**阻止**非本地模型调用（本地模型永远允许）。
- 跨货币的开销只入 history、不并入 spent。

### 6. 缓存命中率与固定前缀

`cache/prompt_builder.py` 提供 `PromptBuilder` —— **固定段顺序**是 prefix-cache 命中的关键：

```
1. system_prompt
2. policy / agent rules
3. tool_schema
4. project_summary
5. file_tree
6. user_query   ← 唯一变化的段，放最后
```

为什么重要：provider 的 prefix cache 按消息前缀匹配，一旦你今天把 system 放第 1、明天放第 3，所有累积的缓存都失效。`PromptBuilder` 强制顺序，并对稳定段输出一个确定性 hash（v0.4 接到请求层时直接当 cache key 用）。

不要做：把时间戳放最前；把 user 问题塞进 system；每次随机重排 tool 列表。

### 7. 本地模型 = 0 成本

`Capabilities.local=true` 的模型在估算 / 预算里走 `local-free`（0 元）。`hard_stop` 永远不会阻止它们。所以 `economy` 模式 + 本地 tiny 模型，是最省的组合。

### 8. 命名配置切换 (profile)

`mbridge config profile add daily` 交互式记下一组 `default_model + routing.levels`，`mbridge config profile use doubao` 一键换整组模型。不动 router / REPL 代码，只是写回顶层字段。

---



## 第五阶段：项目文件读取

`mbridge ask "..." --project .` 在 prompt 里只塞**最相关的 5–10 个文件**，不灌整库。流水线是：

```
scan_project  →  select_files  →  read_files  →  context.budget.plan  →  PromptBuilder
```

### 1. 关键词驱动的文件选择器

`project/file_selector.py` 用 Topic 关键词 + 路径/文件名匹配给每个文件打分：

| query 关键词 | 命中目录 / 文件名 |
|---|---|
| 登录 / auth / session / 鉴权 | `login`, `auth`, `session`, `oauth`, `jwt`, `token` |
| 接口 / api / endpoint / 路由 | `api`, `route`, `router`, `controller`, `handler` |
| 数据库 / db / schema / orm | `db`, `model`, `schema`, `migration`, `repository` |
| 配置 / config / env | `config`, `settings`, `setup`, `env` |
| 测试 / test | `test`, `spec`, `__tests__` |
| 页面 / 组件 / ui | `pages`, `components`, `view`, `screen` |
| 介绍 / 项目是什么 | `readme`, `docs` |
| 部署 / docker | `Dockerfile`, `docker-compose`, `.github/workflows` |

打分规则（每文件累加）：
- basename 含 query token：+6；路径含：+3
- 命中 topic 关键词 basename：+8；路径：+4
- README / `package.json` / `pyproject.toml` 等 manifest：+5
- 入口文件（`main.py` / `index.ts` / `main.go` …）：+4
- 用户问题里出现目录名（如 `src` / `tests`）：同目录 +3
- 非测试问题碰到 `tests/`：-2

默认返回 Top 8，硬上限 10。**README 与第一个 entrypoint 总是入围**（哪怕没人提到它们）—— 用一两百字符给模型一个"项目是什么"的锚点。

### 2. 文件读取器（捷径式）

`project/file_reader.py` 不会全文塞进 prompt：

- 单文件上限：**300 行 / 10 KB**
- 超出时：保留头 80 行 + 用正则提取剩余部分的 `def` / `class` / `function` / `interface` / `type` 等签名
- 二进制 / 图片 / 压缩包后缀直接跳过
- `.env` / `id_rsa` / `*.pem` 等敏感文件再次拒读（即使被选中），返回 `skipped_reason`
- 路径解析后必须仍在 `project_root` 内（防 symlink 逃逸）

### 3. 上下文预算

`context/budget.py` 在 PromptBuilder 之前再过一道：

```
max_chars  = 20000 (默认；--max-context 可改)
overhead   = rules + system + summary + user_query + 256 字符 headroom
files_room = max_chars - overhead
```

文件按"是否 anchor（README/manifest/entrypoint）"+"是否短小"评分，从最高开始塞；放不下时**先压缩**成"头 30 行 + 签名"，再不行才整体丢弃。被压缩 / 丢弃的文件都会出现在 verbose 输出里，并在 chat 结果末尾打 `context truncated to fit model limits`。

### 4. PromptBuilder 集成

`project_files` 作为新的固定 section 插在 `project_summary` 之后、`tools_schema` 之前。它也是 prefix-hash 的一部分——**同一个项目同一组文件，跨多轮问答 prefix_hash 不变**，这是 DeepSeek / Qwen 等 provider prefix-cache 命中的前提。

### 5. 用法

```bash
# 用项目规则 + 项目摘要 + 自动选 5-10 个文件来回答
mbridge ask "这个项目是做什么的？" --project .

# 不调模型，只看选了哪些文件
mbridge ask "登录功能在哪个文件？" --project . --show-files --show-prompt

# 路由 + 项目都开
mbridge ask "分析这个项目结构" --project . --route

# 自己设上下文上限（小模型用）
mbridge ask "..." --project . --max-context 8000

# verbose: 文件 / 行数 / 截断 / 总 context 全列出来
mbridge ask "..." --project . -v
```

### 6. 不读什么

`scan_project` 与 `file_reader` 共同维护一份硬黑名单：

- 文件：`.env` / `.env.*` / `id_rsa` / `id_ed25519` / `*.pem` / `*.key` / `secrets.{yaml,yml}` / `*_secret*` / `*credentials*`
- 目录：`.git` / `.ssh` / `node_modules` / `dist` / `build` / `__pycache__` / `.venv` / `venv` / `target` / `.idea` / `.vscode` / `vendor` / `.next` / `.nuxt`
- 单文件 > 200 KB 时只读头部

检测到敏感文件会进入 `ProjectSummary.ignored_files` 并在 notes 里写一行 "检测到敏感文件 (已跳过未读取): .env"，模型能知道它的存在但永远看不到内容。

---



## 第六阶段：diff 驱动的文件编辑

**核心契约**：AI 永远不能直接动你的文件。它只能输出一段 **unified diff**；ModelBridge 负责解析、安全检查、展示、确认、备份、应用、回滚。

### 1. 命令

```bash
# 让模型生成 diff (不应用，仅展示)
mbridge edit "给 README 加一段介绍" --project . --dry-run

# 完整流程：生成 → 安全检查 → 展示 → 确认 → 备份 → 应用
mbridge edit "修复登录 bug" --project .

# 跳过最终确认 (但安全检查 + 备份照常)
mbridge edit "修复 typo" --project . --yes

# 应用磁盘上的现成 patch
mbridge patch preview path/to/x.patch
mbridge patch apply path/to/x.patch --yes
mbridge patch apply path/to/x.patch --dry-run

# 回滚最近一次 apply
mbridge patch rollback
```

`mbridge edit` 走完后会把模型生成的 diff 持久化到 `.modelbridge/patches/<ts>.patch` —— 即使你 `--dry-run` 也保留，方便事后人工应用或归档。

### 2. 模型必须输出标准 unified diff

`editor/edit_prompt.py` 给模型的系统指令是硬规则：

```
你的唯一职责是输出一段标准 unified diff。
1. 只能是 unified diff，可以放在 ```diff ... ``` 代码块里。
2. 禁止输出整文件覆盖、伪代码、解释、Markdown 列表。
3. 必须有 `--- a/...` + `+++ b/...` 文件头。
4. 每个 hunk 必须有合法 `@@ -X,Y +A,B @@` 头。
5. hunk 内每行以 ` `（context）、`-`（删除）或 `+`（新增）开头。
6. 不准动 .env / .ssh / .git / node_modules / dist / build。
7. 新建文件用 `--- /dev/null`；超出"修改文件"能力时输出 `# need-human-decision`。
```

`extract_diff` 容忍这些常见偏差：模型在 diff 前后加段解释、把 diff 包在 ```diff``` 围栏里。但**任何拿不到合法 diff header 的响应直接报错退出**——绝不会"猜"。

### 3. Diff 解析与校验

`editor/diff_parser.py` 自己解析 unified diff（不依赖系统 `patch`，Windows 也能跑）。校验内容：

- `---` 必须紧跟 `+++`，不能孤立
- `@@` 行格式：`@@ -old[,count] +new[,count] @@`
- hunk 内每行开头只能是 ` `、`-`、`+`
- hunk header 声称的 old/new 行数与 body 偏差超过 2 行 → 报错
- 接受 `\ No newline at end of file` 行
- 自动剥掉 `a/` / `b/` / `./` 前缀
- 支持 `--- /dev/null`（新建）与 `+++ /dev/null`（删除）

### 4. 路径安全（硬黑名单）

每条 diff 路径在应用前都过 `editor/safety.guard_path`：

| 拒绝原因 | 触发条件 |
|---|---|
| 绝对路径 / Windows 驱动器 | `/abs/x.py`, `C:/Users/x.py` |
| `..` 跳目录 | `../escape.py` |
| 受保护目录 | `.git/`, `.ssh/`, `node_modules/`, `dist/`, `build/`, `.venv/`, `.next/`, `vendor/`, `.idea/`, `.vscode/` 等 |
| 敏感文件名 | `.env` / `.env.*` / `id_rsa` / `id_ed25519` / `*.pem` / `*.key` / `secrets.{yaml,yml}` / `*_secret*` / `*credentials*` |
| 路径逃逸 | symlink 解析后跳出 `project_root` |

**任何一条不过，整个 patch 拒绝应用**——不会"先应用安全的、跳过危险的"。

### 5. Hunk 应用：context-anchored，不盲信行号

`editor/patch_applier.py` 不照搬 hunk 的行号；它把 hunk 的 *context + removed* 块当作锚点，在文件中以 `±5 行` 模糊匹配。找到了才替换；找不到则整文件失败（不写半个 patch）。

实际效果：模型把行号写偏一两行不要紧；只要 context 行还在文件里，patch 就能落上去。

### 6. 备份 + 回滚

每次 `apply` 前自动落一个备份到 `<project>/.modelbridge/backups/<ts>_<label>/`：

```
.modelbridge/backups/
└── 2026-05-23_143012_edit/
    ├── meta.json              ← {timestamp, user_request, modified[], created[], deleted[]}
    ├── patch.diff             ← 应用的那份 patch
    └── files/
        └── src/auth.py        ← 应用前的原始内容
```

`mbridge patch rollback` 选最新的非 `.rolledback` 目录，逐文件恢复（被创建的文件 → 再删除；被删除的文件 → 重建），然后把目录改名加 `.rolledback` 后缀（不删除历史，只移出栈顶）。

### 7. dry-run vs --yes 语义

- `--dry-run`：完整跑 prompt → 模型 → 解析 → 安全检查 → 展示 diff → **停**。不写文件、不建备份、不动 budget。
- `--yes`：跳过"确认应用?"那一步弹窗，但前面的**安全检查、备份、应用结果展示**全部照常。`--yes` 不是 "yolo"，它是 "我确认看过这种 prompt 的输出"。

### 8. 完整流程一条线

```
用户输入需求
   ↓
scan_project (项目摘要)
   ↓
select_files (选 5-10 个相关文件)
   ↓
read_files (300 行 / 10 KB 上限)
   ↓
context.budget.plan (压到 max_context 之内)
   ↓
PromptBuilder + EDIT_SYSTEM_RULES (固定段顺序，cache-friendly)
   ↓
provider.chat → 模型生成 diff
   ↓
extract_diff (剥 ```diff``` fence)
   ↓
parse_unified_diff (语法校验)
   ↓
guard_paths (路径黑名单)
   ↓
render_diff_panel (彩色展示 +/-)
   ↓
Confirm.ask (除非 --yes)
   ↓
create_backup (写 .modelbridge/backups/<ts>/)
   ↓
apply_diff (context-anchored)
   ↓
[如果失败] → 备份留着，可 rollback
```

---



## 路线图

| 版本 | 状态 | 内容 |
|---|---|---|
| v0.1 | ✅ | CLI 骨架、模型配置、OpenAI-compatible 通用调用 |
| v0.2 | ✅ | Provider Adapter、统一 schema、doctor、错误诊断 |
| v0.3 | ✅ | 路由 (TaskProfile + mode) / 成本 (`pricing.yaml`) / 预算 (daily + hard_stop) / 缓存 PromptBuilder / profile / config show/upgrade |
| v0.4 | ✅ | 持续会话 REPL + 文件 / shell 工具 (Claude Code 风格) + AGENT.md / `/init` |
| v0.5 | ✅ | `chat --project`：scan → select → read → budget → PromptBuilder，敏感文件硬黑名单 |
| v0.6 | ✅ | **当前** — diff 驱动编辑：`mbridge edit` / `patch preview` / `apply` / `rollback`，自动备份 |
| v0.7 | — | 真正请求路由 + prefix-cache 接入 + 预算拦截 |
| v0.8 | — | Web Server / Agent Proxy / MCP Client |
| v1.1 | ✅ | **浏览器侧边栏 Agent**：Chrome/Edge MV3 插件 + Native Messaging 宿主 (LocalBridge)，聊天读写当前网页。CLI 保留为共享后端。插件在独立的 [`extension` 分支](https://github.com/CrisXie4/ModelBridge/tree/extension) 维护（orphan 分支，只含扩展本身） |
| v1.2 | ✅ | **MCP 完整接入** (M0–M7)：stdio + Streamable HTTP 双传输、多 server 治理与故障隔离、重连退避/心跳/`list_changed` 热刷新、REPL `/mcp` 运行时启停 + 工具级权限 (`tool_overrides`)、sampling 回调（server 借用国产模型）、`mbridge mcp serve` 反向把 ModelBridge 暴露为 MCP server。详见 [docs/mcp-architecture.md](docs/mcp-architecture.md) |

---

## 不做的事

ModelBridge **不做**：

- ❌ Web UI (Gradio / Streamlit / 前端)
- ❌ Agent 之间并发调度 (留到 v0.6+)
- ❌ 用户系统 / 多租户 / 数据库
- ❌ 内置 sandbox / 容器 — 需要更强隔离请自己用 Docker 跑 mbridge

---

## 项目规则文件 (v0.4)

ModelBridge **优先遵守项目规则文件**。项目规则文件越清楚，AI 后续改代码越稳定。

### 支持的文件 (按优先级，顶部覆盖底部)

| 位置 | 文件 |
|---|---|
| **项目根目录** | `AGENT.md` · `AGENTS.md` · `CLAUDE.md` · `.cursorrules` · `.windsurfrules` |
| **项目 .modelbridge/** | `rules.md` · `prompt.md` |
| **用户全局** | `~/.modelbridge/system.md` (系统提示) · `~/.modelbridge/rules.md` (跨项目规则) |

多个文件都存在时**全部合并**，每个文件前会插一个 `# Rules from <name> (scope)` 标题，方便 debug。

### 为什么支持 CLAUDE.md / .cursorrules

如果你的项目已经为 Claude Code / Cursor / Windsurf 写过规则文件，ModelBridge 直接复用，**不要求你再写一份**。优先级顺序保证项目级规则在最前面，AI 读到时最先看到。

### 用 `/init` / `mbridge project init` 自动生成 AGENT.md

```bash
# CLI 模式
mbridge project init --path . --model deepseek-chat

# REPL 模式 (mbridge 进 REPL 后)
> /init           # AI 扫描项目并生成 AGENT.md，写前给预览
> /init --force   # 已有 AGENT.md 时覆盖
```

生成的 AGENT.md 包含 8 个段：Project Overview / Tech Stack / Common Commands / Directory Structure / Coding Rules / Agent Instructions / Safety Rules / Known Notes。

### 看到当前生效的规则

```bash
mbridge prompt list --project .       # 全局 + 项目，状态表格
mbridge project rules --path .        # 仅项目侧
mbridge prompt show --project . --full   # PromptBuilder 组装后的完整 sections
```

REPL 里直接输 `/rules` / `/prompt` 等效。

---

## 自定义全局提示词

`mbridge init` 会在 `~/.modelbridge/` 写两个默认文件：

- `system.md` — 给所有项目共用的 system prompt
- `rules.md` — 跨项目通用规则 (回答风格、安全约束等)

修改方式：

```bash
mbridge prompt edit                          # 默认编辑 rules.md
mbridge prompt edit system                   # 编辑 system.md
mbridge prompt set-system "你是一个严谨的 …" # 一行覆盖 system.md
mbridge prompt reset --force                 # 恢复默认 (system + rules)
mbridge prompt reset --which system          # 只恢复 system.md
```

`mbridge prompt edit` 用 `$EDITOR` / `$VISUAL` 环境变量；没设的话会打印路径让你手动开。

### `config.yaml` 里的 `prompt` 段

```yaml
prompt:
  system_file: ~/.modelbridge/system.md     # 可指向任意路径
  user_rules_file: ~/.modelbridge/rules.md
  use_project_rules: true
  use_claude_md: true
  use_agent_md: true
  max_rules_chars: 20000                    # 规则文件硬上限，超出截断
  inject_position: before_user_request
```

---

## PromptBuilder：固定 section 顺序 (缓存命中)

`mbridge` 调模型前用 `PromptBuilder` 组装 messages，**section 顺序固定**：

```
1. core_system        ← ~/.modelbridge/system.md
2. global_rules       ← ~/.modelbridge/rules.md
3. project_rules      ← AGENT.md + CLAUDE.md + .cursorrules
4. project_summary    ← scanner 输出的 ProjectSummary
5. tools_schema       ← (预留位，v0.5+ 工具定义)
─────────── 以上是稳定前缀 ───────────
6. history            ← 此前对话
7. user_request       ← 当前问题
```

**为什么 1-5 必须不变顺序**：DeepSeek / Qwen / Kimi 这些都有 prefix-cache，相同前缀重复命中能省 ~80% 输入 token 费用。ModelBridge 出 prefix 时不打时间戳、不随机洗牌，并通过三个 hash 让你看见缓存是否有效：

```
prefix_hash       = 7f705996       ← 1+2+3+4+5 合并后的 sha256[:8]
rules_hash        = 64e90903       ← 仅规则部分
project_summary_hash = 2ee6e678    ← 仅 scanner 输出
```

**同一项目里第二次问问题，应该看到相同的 `prefix_hash`** — 如果不同就说明规则或扫描结果变了，缓存不会命中。`mbridge ask --verbose` 和 REPL 里的 `/prompt` 都会输出这三个 hash。

---

## ask 集成项目规则

```bash
mbridge ask "这个项目应该怎么启动？" --project .
mbridge ask "..." --project . --show-prompt    # 不调用模型，只看组装结果
mbridge ask "..." --project . --verbose        # 显示 prefix_hash + raw 落盘
```

启用 `--project` 时 chat 会：

1. 扫描项目 (`scan_project`)
2. 加载 system.md + rules.md + 项目规则文件
3. PromptBuilder 组装 messages
4. 发到 provider
5. 输出底下显示 `rules sources: ...` 和 `prefix=xxxxxxxx`

---

## 项目扫描器的安全边界

`scan_project` **绝不**读取这些文件的内容 (基于文件名匹配)：

```
.env / .env.* / *.env
id_rsa / id_rsa.* / id_ed25519 / id_ed25519.*
*.pem / *.key
*_secret* / *credentials*
secrets.yaml / secrets.yml
```

**绝不**进入这些目录：

```
.git .hg .svn        node_modules bower_components
.venv venv env       __pycache__ .mypy_cache .pytest_cache .ruff_cache
dist build target    .next .nuxt .turbo .cache
vendor Pods          .idea .vscode .ssh
```

检测到敏感文件**只记录文件名**到 `notes`，比如 `"检测到敏感文件 (已跳过未读取): .env, id_rsa"`，让 AI 知道它们存在但拿不到内容。

任何单个普通文件超过 200 KB 也跳过。README 截断到 2500 字符。文件树最多 250 条。

---

## REPL slash 命令 (v0.4 新增)

`mbridge` 进 REPL 后：

| 命令 | 作用 |
|---|---|
| `/init [--force]` | 调用模型为当前项目生成 AGENT.md，写前给预览 |
| `/rules` | 列出当前生效的规则文件 (scope / size / path) |
| `/prompt` | 显示 PromptBuilder 组装结果 (sections + prefix_hash) |
| `/debug on\|off` | 开启 / 关闭调试日志 (`~/.modelbridge/logs/mbridge.log`) |
| `/version`、`/update` | 显示版本号 / 检查并下载新版本 |
| `/help`、`/think`、`/tokens`、`/save`、`/policy`、`/tools` | 见 v0.3 |

`/init` 不会被当成普通 prompt 发给模型——它走专门的 `generate_agent_md → write_agent_md` 流程。

---

## 开发

```bash
pip install -e ".[dev]"        # 装上 pytest / ruff / mypy
pytest                         # 跑测试 (配置见 pyproject [tool.pytest.ini_options])
ruff check .                   # lint (CI 硬门禁，必须干净)
mypy modelbridge/              # 类型检查 (CI 里非阻塞，存量噪声待清)
```

CI 见 `.github/workflows/ci.yml`：`test` 是硬门禁 (3.10/3.11/3.12)，`lint` 已收紧为硬门禁，`typecheck` 暂为非阻塞。

> **Windows 注意**：`mbridge` 安装为 console script (`mbridge.exe`)。重新 `pip install -e .` 前先关掉所有正在运行的 `mbridge`，否则可能因 `mbridge.exe` 被占用报 `WinError 32`；若装到一半失败导致 `No module named 'modelbridge'`，重跑一次 `pip install -e . --no-deps` 即可修复。

## License

Apache-2.0

