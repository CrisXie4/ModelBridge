# 浏览器操控扩展 — 设计文档

**日期**: 2026-08-11
**范围**: 给浏览器写工具加 `reason` 必填参数；新增模拟真人点击（默认软模拟 DOM 事件序列，可选 CDP `trusted` 硬模拟）；上下文感知自动同意（LLM 语义判断，把 reason + 目标喂给安全判定小模型）

## 背景

当前浏览器工具链（`agent/tools/browser_tools.py` 读、`browser_write_tools.py` 写、`computer_control_tools.py.InjectJsTool` 注入）已支持 navigate / click / fill / inject_js，但：

1. **click 只调原生 `el.click()`** —— 无鼠标移动、无 hover、无时序，反 bot 检测一眼识破。
2. **审批卡片只有 `summary`+`detail`** —— 用户看到「点击元素 / selector: .btn」完全不知道 AI 为什么点这个。
3. **自动同意靠 `_is_risky` 工具名子串** —— 不懂语义，「点清缓存按钮」和「点删除账户按钮」一视同仁需要确认。
4. **JS 注入已存在**（`InjectJsTool` + 扩展 `inject_js` 分支）—— 本设计复用、加 reason。

## 设计决策（与用户确认 2026-08-11）

- **模拟真人点击**：默认走软模拟（完整鼠标事件序列 + 随机时序）；`click` 加 `trusted: bool` 参数，`true` 时走 `chrome.debugger` + CDP `Input.dispatchMouseEvent`（`isTrusted=true`，能过严格反检测，代价是浏览器顶部出现调试警告条）。
- **自动同意规则**：走 LLM 语义判断，复用现有 `_auto_judge` / `_llm_safety_judge`，提示词增强（把 reason + 目标 + 低风险操作白名单示例喂进去）。
- **reason 字段**：写工具（click / fill / navigate / inject_js）**必填**参数；贯穿工具 schema → `_approval()` → `confirm()` → `ApprovalFn` → 协议帧 → 扩展 UI 全链路。

## 改动详设

### 1. 工具 schema 加 `reason`，click 加 `trusted`

**`agent/tools/browser_write_tools.py`** —— `click` / `fill` / `navigate` 各加：
```python
"reason": {"type": "string", "description": "为什么要做这个操作（一句话，给用户审批看）"}
```
（required 列表加入 `"reason"`）

`click` 额外加：
```python
"trusted": {"type": "boolean", "default": False,
            "description": "true=CDP 真实输入事件(isTrusted=true，过严格反检测，会弹调试警告)；false=软模拟DOM事件序列(默认)"}
```

**`agent/tools/computer_control_tools.py.InjectJsTool`** —— 同样加 `reason` required。

`_WriteBrowserTool._approval()` 返回 `(summary, reason_text)`，detail 拼上 reason。

### 2. 审批链路贯通 reason

**`agent/context.py`** —— `confirm()` 和 `ApprovalFn` 协议加 `reason: str = ""` 关键字参数。

**`bridge/protocol.py`** —— `approval()` 帧构建器加 `reason: str = ""` 字段，进 frame dict。

**`bridge/browser_bridge.py`** —— `request_approval(*, tool, summary, detail, reason, ...)` 转发到协议帧。

### 3. CLI 审批 UI 显示 reason + 判定提示增强

**`cli.py:_make_approval`** —— Panel 体加一行 `[dim]意图：{reason}[/dim]`；`_auto_judge` 提示词改为：

```
判断以下浏览器操作是否安全可自动同意。先给理由，再给结论「安全」或「不安全」。
工具: {tool}
操作: {summary}
意图: {reason}
目标: {detail[:300]}

判定「安全」的标准：后果可控可撤销，或属于常规低风险操作
（清缓存、关弹窗、取消订阅、登出、滚动、筛选、展开折叠、翻页、同意 cookie 等）。
涉及支付/转账/删除账户/提交订单/修改密码/发送消息/同意条款 → 「不安全」。
```

### 4. 面板路径加 LLM 自动同意

**`bridge/session_runner.py:_build_context.approve`** —— 当前只把审批转给面板。改为：先调 `_llm_safety_judge(tool, summary, detail, reason)`，若 `is_safe` 直接返回 `YES`（不弹面板）；否则照旧转面板让用户决定。

### 5. 扩展端：onApproval 显 reason + click 软模拟 + CDP trusted

**`ModelBridge-extension/manifest.json`** —— `permissions` 加 `"debugger"`。

**`ModelBridge-extension/sidepanel.js`** ——
- `onApproval(msg)`：卡片加 `<div class="reason">意图：{msg.reason}</div>`（msg.reason 为空时省略）。
- `pageToolDispatcher.click`（默认软模拟）：`scrollIntoView` → 随机 sleep → 在元素中心点派发 `mouseover → mouseenter → mousemove → mousedown → focus → mouseup → click` 全序列，带 `clientX/clientY` 和随机 50-200ms 间隔。
- `onToolCall` 路由层：若 `name==="click" && args.trusted===true`，走新 `trustedClick(tab, args)` —— `chrome.debugger.attach` → 用 executeScript 取元素中心坐标 → `Input.dispatchMouseEvent` 派发 mouseMoved/mousePressed/mouseReleased → `chrome.debugger.detach`。

### 6. 不做

- 不改读工具（read_page / get_selection / query_dom / extract），它们本就无需确认。
- 不动 `--yes` 全局自动同意、`/auto` 全局开关的现有语义，只在 LLM judge 提示词层增强。
- 不实现代码内的关键词规则引擎（用户明确选 LLM 语义判断路线）。

## 验证

1. `python -c "import modelbridge"` 无错。
2. `ruff check modelbridge` 通过。
3. `pytest tests/` 全绿（更新 browser_write_tools / computer_control_tools 相关测试的 reason 必填）。
4. 手测：浏览器侧边栏让 AI 点「清缓存」按钮 → 审批卡片显示「意图：清缓存以释放空间」→ 自动同意不弹；让 AI 点「删除账户」→ 不自动同意，弹面板等用户。
