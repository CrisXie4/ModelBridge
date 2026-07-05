# AGENT.md

## Project Overview
ModelBridge 是一个 **国产模型优先的 AI Coding Agent + CLI 工具**。  
直接运行 `mbridge` 进入持续会话（类似 Claude Code），AI 可读/写/编辑项目文件，并在确认后执行 Shell 命令。  
管理性操作（添加模型、自检、路由、成本、缓存）通过子命令完成。  
通过 Provider Adapter 层适配国产模型（DeepSeek、Qwen、Kimi、GLM 等）的字段差异，提供统一调用界面。

## Tech Stack
- **语言**：Python 3.11, Shell
- **框架**：Typer（CLI）, rich（终端 UI）, pytest（测试）
- **包管理器**：pip（pyproject.toml / PEP 621）
- **部署方式**：开发安装 `pip install -e .`；也可通过 PyInstaller 打包为独立可执行文件（见 `packaging/`）

## Common Commands
```bash
# 安装（开发模式）
pip install -e .

# 初始化用户配置（创建 ~/.modelbridge/）
mbridge init

# 添加模型（需先设置对应 API Key 的环境变量）
export DEEPSEEK_API_KEY=sk-xxx
mbridge model init

# 进入持续会话 REPL（默认模型、当前目录）
mbridge

# 指定模型和工作目录，并开启 bash 工具
mbridge -m qwen-coder --cwd /path/to/project --allow-bash

# 运行全局自检
mbridge doctor

# 运行测试套件
pytest

# 打包可执行文件（Linux 示例）
bash packaging/build_linux.sh
```

## Directory Structure
- `modelbridge/`：主包
  - `cli.py`：命令行入口，定义所有子命令
  - `client.py`：模型 API 统一客户端
  - `config.py`：用户配置读写
  - `schemas.py` / `models.py`：数据结构定义
  - `agent/`：Agent 持续会话核心
    - `loop.py`：输入-处理-输出主循环
    - `tools/`：Agent 可调用的工具（文件读写、bash、浏览器、子 agent）
    - `security.py`：操作确认与安全审批
    - `ui.py`：基于 rich 的终端界面
    - `commands.py`：REPL slash 命令（/model, /think, /mcp …）
    - `thinking.py`：thinking/reasoning 预算与 level 解析
    - `at_completer.py`：@文件提及补全
    - `mentions.py`：@提及 → 文件内容注入
  - `providers/`：模型提供商适配层
    - `base.py`：抽象基类
    - `registry.py`：适配器注册表
    - 各文件（`deepseek.py`, `qwen.py`, `glm.py` …）实现具体适配
  - `router/`：路由分析与自动选模、回退逻辑
  - `cost/`：成本估算（基于本地 token 计数 + provider 定价，预算/上限已在 2026-07 移除）
  - `editor/`：代码编辑、补丁应用、备份与安全
  - `executor/`：Shell 命令执行、输出解析与验证
  - `project/`：项目扫描、文件选择、初始化配置
  - `cache/`：本地缓存管理
  - `prompt/`：系统提示词构建与加载
- `packaging/`：PyInstaller 打包相关脚本与配置
- `pyproject.toml`：项目元数据和依赖
- `README.md`

## Coding Rules
- 代码风格：未知（未提供 linter/格式化配置，推测遵循 PEP 8）
- 命名规范：模块名用小写+下划线（如 `error_hints.py`）；类名 PascalCase；函数和变量用 snake_case
- 提交规范：未知

## Agent Instructions
- 修改 Agent 核心逻辑（`modelbridge/agent/`）前，必须理解 `loop.py`、`security.py` 以及工具系统的交互，确保安全确认流程不被意外绕过。
- 新增模型适配器需继承 `modelbridge/providers/base.py` 中的基类，并在 `modelbridge/providers/registry.py` 中注册。
- 任何操作 `~/.modelbridge/` 目录下文件（包含 API key 等敏感信息）的代码，均需保留用户的确认步骤，严禁静默修改。
- CLI 扩展必须通过 Typer，在 `modelbridge/cli.py` 中注册新命令，保持命令行参数风格一致。
- 编辑文件功能（`modelbridge/editor/`）修改前，应保证备份机制（`backup.py`）完好，并遵循 `safety.py` 的校验策略。
- 涉及 Shell 执行时，严格遵守 `modelbridge/agent/tools/bash_tool.py` 与 `security.py` 的白名单与审批逻辑，不可添加后门或绕过确认。
- 提示词（`modelbridge/prompt/` 下内容）的调整需谨慎，变更后需验证模型输出质量和工具调用格式不变。

## Safety Rules
- 绝不读取 `.env` 文件或任何包含明文 API Key 的非项目配置文件；仅在明确获权时访问 `~/.modelbridge/config.yaml`。
- 不经用户明确同意，不删除或覆盖任何文件。编辑操作必须先以 diff 形式展示变更并等待确认（除非使用了 `--yes` 参数）。
- 修改安全模块（`modelbridge/agent/security.py`）后，必须运行全量测试并由人工审核。
- 外部命令执行遵循已批准的命令列表，禁止执行列表外的任意指令。
- 禁止将 API Key、机密信息写入日志；遵循 `modelbridge/raw_logger.py` 中的脱敏规则。

## Known Notes
- 国产模型字段差异大（如 `reasoning_content` vs `thinking`），适配新模型时需逐个映射，避免字段丢失或解析错误。
- 成本估算依赖本地 `pricing.yaml` 或内置价格表，供应商价格变动时需手动同步。
- `modelbridge doctor` 的诊断可能因网络或 API 变更而失败，增加容错处理是合理的优化方向。
- 打包为独立可执行文件时，确保 `prompt/` 等非代码资源被包含在 PyInstaller 分析的依赖中。

## Known Tech Debt
- **腾讯混元无专用适配器**：`ProviderType.HUNYUAN` + `PROFILES` 条目已建（`mbridge model init` 可选），但 `providers/registry.py` 未注册，运行时回退到 `OpenAICompatibleProvider`。混元是国内 SSE 协议 + TC3-HMAC-SHA256 鉴权，非纯 OpenAI 兼容，回退后大概率不通。真正接入需新建 `providers/hunyuan.py`（SSE 解析 + 签名）并在 registry 注册，参考 `mcp/transport/http.py` 的 SSE 处理。
- **`models.py` vs `schemas.py` 双 Schema 模块**：`models.py` 是配置层（`models.yaml` / `config.yaml`）的 Pydantic 模型，`schemas.py` 是 provider 传输层（`ChatRequest` / `ChatResponse`）的模型。两者概念重叠（`Capabilities` vs `ModelCapability`，且都有 `json` 字段触发 `type: ignore[assignment]`）。各有 24/26 处 import，合并需重排所有 provider 与 cli 的导入路径，暂未做。改动任一侧前先确认另一侧是否受影响。
- **`cli.py` 仍 ~3900 行**：单文件承载所有子命令实现（model / doctor / route / usage / profile / config / edit / patch / project / prompt）。可按命令组拆分到 `cli/` 子包（如 `cli/model.py`、`cli/patch.py`），参照已拆出的 `mcp/cli.py`、`bridge/cli.py`、`skills/cli.py` 模式。
