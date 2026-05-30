# ModelBridge 问题与改进清单

> 生成日期：2025-07-11 | 版本：v0.4.0 | 共 11 项

---

## P0 — 阻塞级（缺少会直接导致回归 / 安全风险）

---

### BUG-001 零测试覆盖

**严重程度**：🔴 P0 · 高风险
**涉及文件**：整个项目
**现象**：`pyproject.toml` 配置了 `pytest>=8.0`，但仓库中不存在 `tests/` 目录，0 条测试。以下模块一旦修改极易引入回归：

- `modelbridge/editor/diff_parser.py` — 手工 unified diff 解析器，正则 + 状态机无测试
- `modelbridge/router/classifier.py` — 关键词驱动路由器，命中规则顺序敏感
- `modelbridge/agent/tools/file_tools.py` — 文件读写 + 路径安全检查无覆盖
- `modelbridge/editor/patch_applier.py` — diff 应用到磁盘的匹配/写入逻辑

**修复方向**：
1. 在项目根创建 `tests/` 目录
2. 为以下模块各写 ≥3 条 pytest：
   - `tests/test_diff_parser.py` — 覆盖：正常 diff、多文件 diff、空 diff、非法 diff、`/dev/null` 创建/删除
   - `tests/test_classifier.py` — 覆盖：8 条内置测试 prompt，验证路由结果与预期 level 一致
   - `tests/test_file_tools.py` — 覆盖：读文件、读不存在文件、写文件、str_replace 多匹配、路径越界拒绝
   - `tests/test_patch_applier.py` — 覆盖：应用 hunk、偏移匹配、创建/删除文件、冲突拒绝
3. 跑通 `python -m pytest tests/ -v`

---

### BUG-002 cli.py 巨型文件 (~134 KB / 3000+ 行)

**严重程度**：🔴 P0 · 可维护性
**涉及文件**：`modelbridge/cli.py`
**现象**：所有 CLI 命令（定义 + 业务逻辑 + UI 渲染 + 错误处理）全在一个文件：

- `cmd_chat` 函数包含路由、prompt 构建、文件选择、模型调用、费用记录、输出渲染等 5-6 种职责
- `_run_repl` 函数约 200 行，混合了配置解析、session 初始化、prompt 构建、UI 回调、缓存统计
- 多个 `_print_*` 渲染函数与业务逻辑紧耦合

**修复方向**：
1. 把 `_run_repl` 的核心流程抽到 `modelbridge/agent/repl.py` (新建)
2. 把 `cmd_chat` / `_chat_with_routing` 抽到 `modelbridge/agent/chat_cmd.py` (新建)
3. 把 `_print_*` 渲染函数集中到 `modelbridge/agent/ui.py` 或新建 `modelbridge/cli_render.py`
4. `cli.py` 最终只保留 typer 命令定义（参数解析 + 调用下层函数），目标 ≤800 行

---

### BUG-003 没有 CI 质量门禁

**严重程度**：🔴 P0 · 工程质量
**涉及文件**：`.github/workflows/`
**现象**：`.github/workflows/` 下只有 `release.yml`（打包发布）。没有：
- lint (ruff check)
- type check (mypy 或 pyright)
- test (pytest)

**修复方向**：
1. 新建 `.github/workflows/ci.yml`，包含以下 job：
   ```yaml
   jobs:
     lint:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/ruff-action@v3
           with: { args: "check --fix" }
     typecheck:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - run: pip install -e ".[dev]" mypy
         - run: mypy modelbridge/ --ignore-missing-imports
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - run: pip install -e ".[dev]"
         - run: pytest tests/ -v
   ```
2. 在 `pyproject.toml` 补充 `[tool.mypy]` 配置：
   ```toml
   [tool.mypy]
   python_version = "3.10"
   ignore_missing_imports = true
   warn_return_any = false
   ```

---

### BUG-004 API Key 明文存储

**严重程度**：🔴 P0 · 安全
**涉及文件**：`modelbridge/cli.py` (~L745 附近)、`modelbridge/config.py`
**现象**：代码中注释已自认：
```python
"v0.2 仍以明文保存到 ~/.modelbridge/models.yaml，后续版本将加密 (TODO)。"
```
当前 v0.4.0 仍未加密。`models.yaml` 中 `api_key` 字段以明文持久化。

**修复方向**：
1. 推荐方案：优先使用环境变量 `api_key_env`，不再支持 `api_key` 明文字段
2. 过渡方案：使用 Python 标准库的 `hashlib` + 用户提供的机器级密钥做对称加密（或依赖 `keyring` 库）
3. 在 `mbridge init` / `mbridge model init` 流程中引导用户设环境变量而非明文 key
4. 旧 `models.yaml` 中的明文 key 在加载时自动迁移到加密存储

---

## P1 — 重要（影响代码质量与用户体验）

---

### BUG-005 模块职责重叠

**严重程度**：🟡 P1 · 架构
**涉及文件**：
- `modelbridge/cost/budget.py` 和 `modelbridge/context/budget.py` — 两个 budget 模块语义不清
- `modelbridge/context/` 和 `modelbridge/context_window.py` — context 概念分散
- `modelbridge/project/` 下 6 个子模块，部分可合并

**修复方向**：
1. 合并 `cost/budget.py` 和 `context/budget.py`：一个管持久化+守卫，另一个管 context 截断，职责分明即可，或在模块 `__init__.py` 中加注释说明边界
2. `context_window.py` 移到 `context/` 包内
3. `project/` 下的 `file_reader.py` + `file_selector.py` 考虑合并为 `project/file_ops.py`

---

### BUG-006 全同步 HTTP，流式响应会阻塞

**严重程度**：🟡 P1 · 性能 / 体验
**涉及文件**：`modelbridge/providers/base.py` (`HTTPProvider`)
**现象**：所有 HTTP 调用使用 `httpx.Client` (同步)。流式 `stream_chat` 的 `iter_lines()` 在慢速模型上会阻塞主线程，无法在等待期间处理用户中断 (`Ctrl-C`) 或其他 UI 更新。

**修复方向**：
1. 新增 `AsyncHTTPProvider` 子类，使用 `httpx.AsyncClient`
2. `stream_chat` 改为 `async for` 模式
3. 在 CLI 层用 `asyncio.run()` 包裹，逐步迁移
4. 同步版本保留作为 fallback（本地模型不需要 async）

---

### BUG-007 路由器关键词硬编码，无法用户自定义

**严重程度**：🟡 P1 · 可扩展性
**涉及文件**：`modelbridge/router/classifier.py`
**现象**：`_TASK_RULES` 是硬编码的中英文关键词列表。加新模型等级或调整路由策略必须改代码发版。

**修复方向**：
1. 把 `_TASK_RULES` 定义为可被 `config.yaml` 的 `routing.custom_rules` 覆盖/追加：
   ```yaml
   routing:
     custom_rules:
       - keywords: ["k8s", "docker", "部署"]
         task_type: "agent_task"
         complexity: "hard"
         level: "agent"
   ```
2. `classify_task()` 合并内置规则 + 用户规则，用户规则优先匹配

---

### BUG-008 错误信息对终端用户不够友好

**严重程度**：🟡 P1 · 用户体验
**涉及文件**：`modelbridge/cli.py` (`_print_provider_error`)、`modelbridge/error_hints.py`
**现象**：401/403 错误只展示 `status_code=401` 和 raw snippet，缺少对新手友好的下一步提示。

**修复方向**：
1. 在 `error_hints.py` 中补充每种 HTTP 状态码的**下一步操作**提示：
   - 401 → "API Key 无效。运行 `mbridge model init` 重新输入，或检查环境变量 {env}。"
   - 403 → "权限不足。确认 API Key 有调用模型 {model_id} 的权限。"
   - 404 → "模型 ID 或 endpoint 不存在。当前 endpoint: {url}，model: {id}。"
   - 429 → "被限流。等待 {retry_after} 秒后重试，或切换模型。"
   - 5xx → "上游服务异常。可稍后重试，或运行 `mbridge doctor model {name}` 排查。"
2. `_print_provider_error` 增加 `[bold]下一步:[/bold] ...` 行

---

## P2 — 优化（改进体验与健壮性）

---

### BUG-009 缺少统一的 `--dry-run` 模式

**严重程度**：🟢 P2 · 一致性
**涉及文件**：`modelbridge/cli.py`
**现象**：`mbridge edit --dry-run` 存在，但 `mbridge chat` 没有 dry-run（虽然有 `--show-prompt`），`mbridge run` 也没有。

**修复方向**：
1. 为 `mbridge run` 加 `--dry-run`：只校验命令白名单 + 路径安全，不实际执行
2. 为 `mbridge chat` 的 `--dry-run` 统一语义：打印将要发送的 prompt + 目标模型 + 预估 token/费用，不实际调用

---

### BUG-010 缺少 Shell 自动补全

**严重程度**：🟢 P2 · 用户体验
**涉及文件**：`modelbridge/cli.py`、`pyproject.toml`
**现象**：`pyproject.toml` 中 Typer app 明确设置了 `add_completion=False`。用户无法用 `mbridge --install-completion` 生成 bash/zsh/fish 补全脚本。

**修复方向**：
1. 把 `app = typer.Typer(add_completion=False)` 改为 `add_completion=True`
2. 加回 `mbridge --install-completion` 的文档说明
3. 如果担心补全脚本和子命令冲突，至少为 `model` / `doctor` / `profile` / `route` 等高频子命令提供补全

---

### BUG-011 config.yaml schema 升级无迁移逻辑

**严重程度**：🟢 P2 · 健壮性
**涉及文件**：`modelbridge/cli.py` (`cmd_config_upgrade`)、`modelbridge/config.py`
**现象**：`cmd_config_upgrade` 只做"补齐缺失字段"，但如果未来发生 breaking change（字段改名、结构重组），没有版本号标记和迁移函数链。

**修复方向**：
1. 在 `config.yaml` 中增加 `schema_version: 1` 字段
2. `load_app_config()` 读取后检查版本号，调用 `_migrate_v1_to_v2()` 等迁移函数
3. 确保旧配置始终可自动升级，不需要用户手动编辑

---

## 修复顺序建议

```
第 1 轮：BUG-003 (CI) → BUG-001 (测试)
第 2 轮：BUG-004 (加密存储) → BUG-002 (拆分 cli.py)
第 3 轮：BUG-005 ~ BUG-008
第 4 轮：BUG-009 ~ BUG-011
```

每轮之间可以先合并到 main 并打一次 tag，降低回滚成本。
