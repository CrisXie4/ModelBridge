# 联网搜索移除 + REPL 体验优化设计

**日期**: 2026-08-11
**范围**: 删除收费联网搜索功能；用户规则覆盖内置系统提示词；子命令实时补全；AI 内联补全（ghost text + Tab 接受）
**目标**: 去掉尚未开放、徒增维护成本的收费搜索代码；让 REPL 的规则注入与补全体验更接近 Claude Code

## 背景

1. **收费联网搜索**走 OAuth + PKCE 对接 `https://web.crisxie.top` 的 `/v1/search`，由 `SEARCH_ENABLED=False` 总开关暂停。代码散布在 `modelbridge/search/`、`agent/tools/web_search_tool.py` 以及 4 处 wiring（`cli.py`、`bridge/session_runner.py`、`weixin/runner.py`）。无测试、无 docs 引用，删除安全。
2. **系统提示词**：`mbridge init` 把 `DEFAULT_SYSTEM_MD` 写到 `~/.modelbridge/system.md`，PromptBuilder 永远把它作为 `core_system` 拼在最前。即使用户写了 AGENT.md / rules.md，那份冗长默认提示词仍在，无法真正「接管」。
3. **子命令补全**：`SlashCommandCompleter` 只补命令名（第一个 token），遇到空格即停，`/think o` 之类没有提示。
4. **AI 补全**：不存在。`@file` 与 `/cmd` 是实时下拉补全，但没有 ghost-text 风格的 AI 续写。

## 设计决策（与用户确认）

- A. 联网搜索：全量删除。
- B. 规则覆盖：用户/项目有任何非空规则文件时，**不再注入内置默认系统提示词**，由用户规则完全接管。全新安装回退默认。
- C. 子命令补全：扩展 `SlashCommandCompleter` 在命令名之后继续补子命令/选项/模型名。
- D. AI 补全：走 **D1**——prompt_toolkit `AutoSuggest` 接口 + 后台异步 `chat_once`，停顿 ~450ms 触发，灰色 ghost text，Tab 接受。**默认开**，配置可关。走当前 REPL 活动模型。

## 改动详设

### A. 删除收费联网搜索

**整删文件：**
- `modelbridge/search/__init__.py`
- `modelbridge/search/cli.py`
- `modelbridge/search/client.py`
- `modelbridge/search/creds.py`
- `modelbridge/search/crypto.py`
- `modelbridge/search/wiring.py`
- `modelbridge/agent/tools/web_search_tool.py`
- `modelbridge/search/` 目录（含 `__pycache__`）

**改动文件：**
- `cli.py`：删 `from .search.cli import search_app` + `app.add_typer`；删 REPL 启动时的 `wire_search` try/except。
- `bridge/session_runner.py`：`build_registry` 删 `maybe_register_web_search` try/except；`_SYSTEM_PROMPT` 末句「若已登录联网搜索…」删除。
- `weixin/runner.py`：删同名 try/except。

### B. 用户规则覆盖内置系统提示词

改 `prompt/builder.py` 的 `core_system` 段：

```
core_system =
  ① ~/.modelbridge/system.md 存在且内容 ≠ DEFAULT_SYSTEM_MD → 用用户 system.md
  ② 否则若存在任意非空规则(rules.md / AGENT.md / AGENTS.md / CLAUDE.md
        / .cursorrules / .windsurfrules / .modelbridge/rules.md
        / .modelbridge/prompt.md)
        → core_system 置空
  ③ 否则 → DEFAULT_SYSTEM_MD
```

`rules_hash` / `prompt_prefix_hash` 计算不变，prefix-cache 语义保持稳定。

### C. 子命令实时补全

扩展 `agent/slash_completer.py`：

- 模块级常量 `_SUBCOMMANDS: dict[str, list[str]]`，覆盖 `/think`、`/mcp`、`/debug`、`/auto`、`/init` 等。
- 命令名后按空格时，根据命令进入子命令补全模式；继续输入则前缀过滤。
- `/model` 通过可选 `model_names_provider` 回调动态拉取配置里的模型名。
- `complete_while_typing=True` 保持，子命令也是实时下拉。

### D. AI 内联补全（ghost text + Tab 接受）

**新增 `modelbridge/agent/ai_completer.py`：**

- `AIAutoSuggest(AutoSuggest)`：实现 `get_suggestion(buffer, document) -> Suggestion | None`。
- 入参：`model_name_provider: Callable[[], str | None]`、`history_provider: Callable[[], list[ChatMessage]]`、`enabled_provider: Callable[[], bool]`。
- 后台线程调 `chat_once(prompt=构造补全请求, model_name=当前模型, system=<极简补全指令>, thinking=False, timeout=8.0)`。
- 防抖 ~450ms + 最少输入 3 字符 + 多行/`/` 开头跳过（slash 有自己的补全）+ `@` 触发后跳过（@file 有自己的补全）。
- 命中前缀（当前输入是上次建议的前缀）直接复用；输入变化取消在途请求；网络错误静默吞掉。
- 用 `ThreadedAutoSuggest` 包一层做异步。

**补全 system 指令：**

```
你是一个 CLI 输入补全器。根据用户当前输入和最近对话，只输出要追加到末尾的续写文本。
不要解释、不要反引号、不要 markdown、不要换行解释。最多 60 字。
```

**Tab key binding：**

- `_pt_bindings` 加 `c-i`（Tab）→ 若 `buffer.suggestion` 存在且补全菜单未开，调 `buffer.apply_suggestion()`；否则 fall-through 让默认 Tab 行为（菜单接受）继续生效。

**cli.py 接入：**

- 构造 `PromptSession` 时加 `auto_suggest=ThreadedAutoSuggest(AIAutoSuggest(...))`。
- `model_name_provider` 用闭包读当前 `model_holder`（支持 `/model` 切换后立刻跟上）。
- `history_provider` 读 `session.messages` 最近 N 条。
- `enabled_provider` 读 `config.prompt.ai_autocomplete`（默认 `True`）。

**成本护栏：**

- 防抖 + 最少字符 + 仅停顿时触发；连续失败 N 次后本次会话自动关闭。
- 配置项 `prompt.ai_autocomplete: bool`（默认 `True`）+ 防抖间隔 `prompt.ai_autocomplete_debounce_ms`（默认 450）。

**降级：**

- prompt_toolkit 不可用 / 非 TTY / 请求连续失败 → 静默关闭，不影响现有输入。

## 验证

1. `python -c "import modelbridge"` 无残留导入错误。
2. `ruff check modelbridge` 通过。
3. `pytest` 通过；新增 `test_slash_completer.py`（子命令前缀过滤）、`test_prompt_builder_rules_override.py`（三种分支）。
4. 手测：REPL `/think o` → 下拉 `on/off`；正常输入停顿后出现灰字，Tab 接受；`/model` 切换后补全跟随。

## 不做

- 不动 MCP 通道（MCP 走自己的注册，不依赖 web_search）。
- 不做专用快速模型路由（按用户要求走当前活动模型）。
- 不做补全结果的缓存/持久化。
