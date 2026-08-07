# CLI 信息展示优化设计

**日期**: 2026-08-07
**范围**: `mbridge` REPL 的气泡宽度、每轮后的状态信息面板、启动 banner 的上下文预算
**目标**: 提升信息密度与可读性，全屏时充分利用左右边距，强制开启（无命令开关）

## 背景

现状（`modelbridge/agent/ui.py`）：

- 气泡宽度 = `width × 0.62`，硬上限 **110 列**。全屏到 200 列时气泡只有 110，左右大片留白。
- 每轮后的状态栏 `status_bar_text` 是**单行**，挤了 4 类信息（模型/token/推理/耗时），密度过大。
- 没有 todo 进度展示。
- 启动 banner 有 prefix hash 但没有 token 预算直观展示。

## 设计决策

经与用户确认：

1. 气泡（user / AI / tool 框）改成**极简色条风**：去掉完整边框，用左侧细色条（▎）+ 标签行分隔。颜色约定：绿=user、青=assistant、品红=tool。
2. 每轮结束后在底部打印**多行信息面板**（贯穿全宽），展示三类信息：token 明细 / todo 进度 / 启动预算。
3. 面板样式选 **方案 A：进度条 + 分行**。
4. **强制开启**，无需命令开关。`/tokens` 斜杠命令增强 role 明细作为按需查看入口。

## 改动详设

### 1. 气泡极简色条风改造 — `agent/ui.py`

去掉所有 `Panel`（完整边框），改为左侧细色条 + 标签行的极简风格。解决了旧 Panel 在 CJK 全角字符宽度误算时边框错位、stacked header 的老大难问题（没有完整框架就没有溢出）。

布局：

```
▎ 帮我优化这个项目的 UI                          ← user，右对齐，绿色条

● deepseek-v4-pro                               ← assistant 标签行（青）
▎ // thinking                                    ← 青色条 + 暗淡
▎ 用户想要极简色条风格，我来重写气泡渲染。        ← 暗淡斜体
▎ ────                                           ← 分隔
  方案                                            ← 内容缩进 2 格对齐色条
  我会把三个气泡函数都改成色条风：

▸ write_file  path=ui.py                         ← tool 标签行（品红）
▎ 已写入 50 行                                   ← 品红色条
```

要点：
- `_BAR = "▎"`（细竖条，比完整方框更干净，CJK 宽度问题更小）
- user 消息右对齐 + 绿色条；assistant/tool 左对齐
- Markdown 内容无法在内部加条前缀，用 `Padding(md, (0,0,0,2))` 整块缩进 2 格与色条对齐
- 流式渲染（`AssistantStream`）保留 tail-view + transient 逻辑，只把 Panel 换成 Group

### 2. 每轮后的信息面板（方案 A）— `agent/ui.py` 新增

替换 `cli.py` 中 `_print_status_bar()` 调用的单行 `status_bar_text`。

**全宽外观**（width ≥ 70）：

```
─ context ────────────────────────────────────────────────────────────────────
  [██░░░░░░░░░░░░░░░░░░] 12%  12.3k / 1.0M  剩余 987.7k
  前缀 3.1k · 推理 2.1k · 工具 4.8k · 对话 2.3k
  计划 ▸ 2/5  进行中:优化气泡宽度   耗时 3420ms · 1 轮
```

**窄窗折叠**（width < 70，拆两段、去文字描述）：

```
─ ctx ─ [██░░] 12% 12k/1M ─
  前缀3k 推理2k 工具5k 对话2k
  ▸2/5 优化气泡宽度 · 3420ms
```

**行规则**：

| 行 | 内容 | 数据源 |
|---|---|---|
| 1 | 标题分隔线 `─ context ─`（窄窗 `─ ctx ─`），贯穿 `console.width` | — |
| 2 | 20 格 ASCII 进度条 `██░░…`（绿<60% / 黄<85% / 红≥85%）+ `12.3k / 1.0M  剩余 987.7k` | `TurnStats` |
| 3 | 按 role 拆分的 token 明细：前缀 / 推理 / 工具 / 对话 | 新 `estimate_tokens_by_role` |
| 4 | todo 进度（`▸ 2/5` + 当前 in_progress 项内容，截断 20 字）+ 耗时/轮数 | `TodoStore.summary()` + `to_list()` |

无 todo 时整行 4 省略。

**字符预算**：每行硬上限 `console.width`，超出按"耗时/明细"优先级截断。

### 3. 启动预算展示 — `cli.py` banner

banner Panel 内现有 `prefix` / `cache_line` 之后追加一行：

```
budget   : 前缀 ~3.1k t / 1.0M 窗口 (0.3% 预占) · 详情 /tokens
```

- 「前缀 ~3.1k t」= 启动时 `estimate_session_tokens([session.messages[0]])`（系统消息含 system+rules+项目摘要+文件）
- 「1.0M 窗口」= `context_window_for(entry)`
- 末尾 `/tokens` 指向增强后的明细命令

### 4. token role 拆分 — `context/windows.py` 新增

```python
def estimate_tokens_by_role(messages) -> dict[str, int]:
    """按用途分类累加 token。

    返回 {"prefix": ..., "reasoning": ..., "tool": ..., "conversation": ...}
    分类规则：
      prefix:        system 消息全文
      reasoning:     任意消息的 reasoning_content
      tool:          role == "tool" 的消息体
      conversation:  其余 user/assistant 的 content
    """
```

### 5. `/tokens` 增强 — `agent/commands.py`

现有 `/tokens` Panel 增加一行 role 明细：

```
role 拆分    前缀 3.1k · 推理 2.1k · 工具 4.8k · 对话 2.3k
```

避免新增命令，复用 `/tokens`（已有别名 `/t`）。

### 6. 斜杠命令 Tab 补全 — `agent/slash_completer.py`（新增）

新增 `SlashCommandCompleter`：用户输入 `/` 起的命令名时实时下拉匹配项；按 **Tab** 一键补全（唯一匹配直接展开，多匹配接受当前高亮）。

- 命令表从 `slash_command_help()`（新加，由 `_COMMANDS` + `_HELP_ROWS` 构建）拉取，单一来源，不会和注册表脱节
- 仅补全**命令名**（首个 token），输入空格后停止 —— 参数（模型名/level 等）不自动补全，避免猜错
- `cli.py` 用 `merge_completers([SlashCommandCompleter, AtFileCompleter])` 合并，`@文件` 与 `/命令` 两套补全共存
- 显式注册 `Tab` key binding：菜单可见时接受当前项；菜单不可见且唯一匹配时直接展开，实现"一键补全"

## 模块改动清单

| 文件 | 改动 | 行数 |
|---|---|---|
| `modelbridge/agent/ui.py` | `_bubble_width` 放宽；新增 `render_context_panel` + `_progress_bar` | ~100 |
| `modelbridge/context/windows.py` | 新增 `estimate_tokens_by_role` | ~30 |
| `modelbridge/context/__init__.py` | 导出新函数 | ~2 |
| `modelbridge/cli.py` | banner budget 行；`on_turn_done` 改调面板；传 todo_store | ~40 |
| `modelbridge/agent/commands.py` | `/tokens` 加 role 明细行 | ~10 |
| `tests/test_context_panel.py` | 新测试 | ~80 |

**不碰**：`AssistantStream` 流式渲染逻辑、`StickyFooter`（DECSTBM 已弃用，保持 inline）、`compute_turn_stats` 签名、provider/cache/live_state 逻辑。

## 错误处理

- `estimate_tokens_by_role` 任一消息解析失败 → 该消息计 0，不抛
- `render_context_panel` 全程 try/except，失败回退到旧 `status_bar_text`
- `todo_store` 为 None 或空 → 跳过计划行，面板仍渲染

## 测试

`pytest tests/test_context_panel.py`：

1. `_progress_bar(0.12, 20)` → `"██░░░░░░░░░░░░░░░░░░"`（2 格填充）
2. `_progress_bar` 颜色阈值 60/85 边界
3. `estimate_tokens_by_role` 给 4 类消息 → 各自累加正确
4. `render_context_panel` 在 width=120 / width=50 输出行数与折叠
5. 无 todo 时省略计划行

## 验证

`pytest` + `ruff check modelbridge tests`（mypy 非阻塞）。手动 `mbridge` 跑一轮看面板。
