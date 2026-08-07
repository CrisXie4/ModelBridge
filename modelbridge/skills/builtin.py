"""Built-in skills shipped with ModelBridge.

These skills are bundled in the package itself (no file I/O) so every
installation gets a useful baseline. They have the lowest precedence:
a user-provided skill of the same name (global or project) always wins.

Sources (adapted under MIT / Apache-2.0, structure preserved):
  * obra/superpowers           — iron-law + red-flag + rationalization style
  * anthropics/skills          — frontend-design, webapp-testing
  * wshobson/agents            — checklists (code-review, api, security, a11y)

The bodies use the "iron law + phases + red flags + anti-patterns" format
because that structure measurably changes agent behaviour — vague platitudes
("think carefully") do not.
"""

from __future__ import annotations

from .discovery import Skill

# ---------------------------------------------------------------------------
# Skill bodies
# ---------------------------------------------------------------------------

_SYSTEMATIC_DEBUGGING = """\
# 系统化调试 / Systematic Debugging

适用于：遇到任何 bug、测试失败、或非预期行为时，**在提出修复方案之前**必须加载。

## 铁律（不可违反）

**没有根因调查，就不准修复。** NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.

## 四个阶段（必须依次完成，不能跳过）

### 阶段 1 — 定位根因
- 把错误信息**完整读完**，不要只看第一行。
- 稳定复现问题（至少复现一次）。
- 检查最近的改动（`git diff` / `git log`）。
- 在每个组件边界收集证据：记录数据"进入时"和"离开时"的样子。
- 从报错点**反向**追踪数据流，直到源头。

### 阶段 2 — 找参照
- 在同一代码库中找一个能正常工作的类似实现。
- **完整**阅读参照实现，不要只扫一眼。
- 列出当前代码与参照代码的**每一处**差异。

### 阶段 3 — 形成假设
- 形成且只形成**一个**假设："X 是根因，因为 Y"。
- 用**最小改动**测试（一次只改一个变量）。
- 确认假设成立后，才进入下一阶段。

### 阶段 4 — 实施修复
- **先写一个会失败的测试**来锁定 bug。
- 一次只修一处，不要"顺手"改别的地方。
- 修复后验证：原本失败的测试现在通过，且没有破坏其他测试。

## 红旗清单（看到这些立刻回到阶段 1）

- "先快速修一下试试"
- "随便改个 X 看看"
- "一次多改几处再跑测试"
- "跳过测试，我手动验证一下"
- "我猜大概是 X 的问题"
- "再试一次修复"（当已经试过 2 次以上）

## 架构熔断规则（重要）

**如果连续 ≥3 次修复都失败** → 停下来。每次修复都暴露出新问题、且问题出现在不同地方 = 这是架构错了，不是假设错了。先和用户讨论，不要继续盲目修。

## 反模式

- 在没读完错误信息前就猜测原因。
- 同时改多个地方再跑测试（无法定位是哪一处起作用）。
- "我已经知道问题在哪了"（跳过阶段 1）。
- 修复后不验证其他测试是否被破坏。
"""


_TEST_DRIVEN_DEVELOPMENT = """\
# 测试驱动开发 / Test-Driven Development

适用于：实现任何功能或修 bug 时，**在写实现代码之前**必须加载。

## 铁律

**没有失败的测试，就不准写生产代码。** NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST.

如果你已经先把代码写出来了 → **删掉它**，从头来。
- 不要留作"参考"
- 不要"边写测试边改它"
- 不要看它
- 删就是删。

## 红-绿-重构循环

### RED（红）— 写一个最小测试
- 一个测试只验证**一个行为**。
- 测试名要清楚描述被测行为。
- 用真实代码，不要用 mock 偷懒。

### 验证 RED（必做）
- 运行它，确认它**失败**（是 fail，不是 error）。
- 失败的原因必须是"功能还没实现"，而不是拼写错误或 import 错。
- 如果测试直接通过 = 你在测已有的行为，测试写错了，修测试。

### GREEN（绿）— 写最简单的代码让它通过
- 只写刚好够过的代码。
- 不要加"将来可能用到"的功能（YAGNI）。
- 不要顺手重构别的代码。

### 验证 GREEN（必做）
- 新测试通过。
- **其他所有测试仍然通过**。
- 输出干净（没有意外的警告/日志）。

### REFACTOR（重构）— 只在绿之后
- 保持测试一直是绿的。
- 不引入新行为。

## 狡辩反驳表（遇到这些念头要警觉）

| 你心里的声音 | 事实 |
|---|---|
| "这太简单了不用测" | 简单的代码也会挂。测试只要 30 秒。 |
| "我写完再测" | 写完再测的测试会直接通过——证明不了任何东西。 |
| "先留着当参考，我先写测试" | 你一定会改它。那就是"写完再测"。删就是删。 |
| "TDD 太慢了" | TDD 就是务实路径。"务实捷径"= 生产环境调试，更慢。 |
| "这次例外" | 没有例外。 |

## 完成检查清单

- [ ] 每个函数都有测试
- [ ] 每个测试都先看到它失败
- [ ] 失败的原因是对的
- [ ] 用了最小实现
- [ ] 所有测试通过
- [ ] 输出干净
- [ ] 用真实代码不是 mock
- [ ] 边界情况已覆盖
"""


_CODE_REVIEW = """\
# 代码审查 / Code Review Excellence

适用于：审查 PR、合并前自检、建立审查规范。

## 四阶段流程（含时间预算）

### 1. 上下文收集（2-3 分钟）
- 读 PR 描述 + 关联的 issue。
- **超过 400 行的 diff 直接打回**，要求拆分。
- 检查 CI 状态。

### 2. 高层审查（5-10 分钟）
- 架构是否合理？
- 文件组织/模块划分是否清晰？
- 测试策略是否充分？

### 3. 逐行审查（10-20 分钟）

按以下分桶逐项检查：

- **逻辑**：边界条件、off-by-one、空值/null 检查、竞态条件。
- **安全**：输入校验、SQL 注入、XSS、数据泄露。
- **性能**：N+1 查询、不必要的循环、内存泄漏、阻塞操作。
- **可维护性**：命名、单一职责、魔法数字。

### 4. 总结与决策（2-3 分钟）
- 给出明确结论：合并 / 需修改 / 拒绝。

## 严重等级标签（必须用这些）

| 标签 | 含义 | 处理 |
|---|---|---|
| 🔴 [blocking] | 必须修，否则不准合并 | 立即修 |
| 🟡 [important] | 应该修，有异议可讨论 | 合并前修 |
| 🟢 [nit] | 锦上添花，不阻塞 | 记下来以后改 |
| 💡 [suggestion] | 另一种思路 | 供参考 |
| 📚 [learning] | 知识分享，无需行动 | 仅供参考 |
| 🎉 [praise] | 写得好 | 鼓励 |

## 提问优于断言

❌ "如果列表是空的这里会崩。"
✅ "`items` 为空数组时会怎样？"

## 语言相关陷阱

- **Python**：可变默认参数、裸 `except`、可变类属性。
- **TypeScript**：`any` 类型、未处理的 async 错误、直接改 props。

## 要避免的坑

- 完美主义（没必要在 nit 上死磕）。
- 范围蔓延（"顺便把那个也改了"）。
- 橡皮图章（没仔细看就批准）。
- 钻牛角尖（在无关紧要的细节上争论）。
"""


_FRONTEND_DESIGN = """\
# 前端设计 / Frontend Design

适用于：构建新 UI、重塑现有界面时，让设计有"刻意感"而非 AI 模板感。

## 核心原则：别用默认值当设计

AI 生成的设计会聚拢到三种"模板脸"：
1. 暖米色背景(~#F4F1EA) + 高对比衬线标题 + 赤陶色强调。
2. 近黑背景 + 单一亮绿/朱红强调色。
3. 报纸式版面、细分割线、零圆角、密集分栏。

这三种都是**默认产物**，不是设计选择。当需求留白时，不要把自由度花在这些默认值上。

## 两遍工作法

### 第一遍：搭一套紧凑的设计 token

- **颜色**：4-6 个命名的 hex（不是随意渐变）。
- **字体**：至少 2 个角色——有性格的展示字（克制使用）+ 互补的正文字 + 工具字。
- **布局**：一句话描述 + ASCII 线框图。
- **签名元素**：这个页面会被记住的**那一个**独特元素。

### 第二遍：对照需求复审

在动手写之前，问自己：这套方案有没有哪部分读起来像"我对任何类似页面都会产出的默认值"？如果有 → 改。

## 具体规则

- **Hero 区是论点**：用主题世界里最有特征的东西开场。
- **结构即信息**：编号标记(01/02/03)只在内容确实是序列时才用。
- **大胆只花在一处**：让签名元素成为唯一的记忆点，周围保持安静。
- **质量底线（不声张地做到）**：移动端响应式、可见的键盘焦点、尊重 `prefers-reduced-motion`。

## UI 文案规则

- **按用户能控制的东西命名**，不按系统结构。用户"管理通知"，不是"配置 webhook"。
- **同一个动作全程同名**：按钮写"发布"，提示就写"已发布"。
- **错误不要道歉，也不要含糊**：说清楚发生了什么、怎么补救。
"""


_VERIFICATION_BEFORE_COMPLETION = """\
# 完成前验证 / Verification Before Completion

适用于：即将声称工作"完成/修好了/测试通过了"时，**在提交或创建 PR 之前**。
这是最通用的 skill，几乎所有任务结束前都该加载。

## 铁律

**没有新鲜的验证证据，就不准声称完成。** NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE.

## 门控函数（在**任何**状态声明之前）

1. **识别**：哪条命令能证明这个声明？
2. **运行**：执行**完整的**命令（新鲜的、完整的，不是上次的）。
3. **阅读**：完整输出，检查退出码，数失败数。
4. **验证**：输出是否确认了声明？
   - 否 → 如实报告实际状态 + 证据。
   - 是 → 才可以声明，且声明要带证据。
5. **然后**才能说"完成"。

跳过任何一步 = 在撒谎，不是在验证。

## 声明 → 必需的证据

| 声明 | 需要的证据（仅"应该通过"不算） |
|---|---|
| "测试通过" | 测试命令输出：0 失败 |
| "lint 干净" | linter 输出：0 错误 |
| "构建成功" | 构建命令：退出码 0 |
| "bug 修好了" | 原症状测试通过 |
| "回归测试有效" | 红绿验证过（不只是跑过一次） |
| "Agent 完成了" | VCS diff 显示了改动（不是 agent 说"成功"） |
| "需求满足了" | 逐条对照清单 |

## 红旗（看到立刻停）

- "应该"、"大概"、"看起来"
- 在验证前说"好了！"/"完成！"
- 准备提交但还没验证
- 信任 agent 的自我报告

## 回归测试验证法

写测试 → 运行(通过) → **还原修复** → 运行(必须失败) → **恢复修复** → 运行(通过)。
"""


_API_DESIGN = """\
# API 设计 / API Design Principles

适用于：设计新 API、审查 API 规范、建立 API 设计标准。

## REST 核心规则

- 资源用**名词**不用动词：`/users` 不是 `/getUsers`。
- HTTP 方法语义：
  - `GET`（幂等、安全）/ `POST`（创建）/ `PUT`（整体替换，幂等）/ `PATCH`（部分更新）/ `DELETE`（幂等）。
- 集合用**复数**：`/users` 不是 `/user`。

## 必做项

- **从第一天就版本化**（URL/header/query 三选一，例：`/v1/users`）。
- **永远分页**（不要返回无上限的列表）。
- **限流**（rate limit）。
- 用 **OpenAPI/Swagger** 写文档。
- 用正确的 HTTP 状态码。
- 无状态（每个请求自包含）。

## 常见陷阱

- 过度/不足获取（over/under-fetching）。
- 不版本化就做破坏性改动。
- 错误格式不统一。
- 漏限流。
- API 结构照搬数据库结构（紧耦合）。
- 幂等操作用 POST。

## 错误格式统一示例

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "email 格式不正确",
    "field": "email"
  }
}
```
"""


_SECURITY_REVIEW = """\
# 安全审查 / STRIDE Security Review

适用于：分析系统安全、做威胁建模、写安全文档。

## STRIDE 矩阵（六个维度逐一过，不准跳过）

| 类别 | 关键问题 | 控制域 |
|---|---|---|
| **S** 伪装 | 攻击者能冒充别人吗？ | 认证 |
| **T** 篡改 | 数据在传输/存储中能被改吗？ | 完整性 |
| **R** 否认 | 攻击者能否认操作吗？ | 日志/审计 |
| **I** 信息泄露 | 攻击者能访问未授权数据吗？ | 加密 |
| **D** 拒绝服务 | 攻击者能中断可用性吗？ | 限流 |
| **E** 权限提升 | 攻击者能获得更高权限吗？ | 授权 |

## 审查清单

### 认证/授权
- [ ] 每个端点都校验身份了吗？
- [ ] 权限按角色正确划分了吗？
- [ ] Session/Token 有过期机制吗？

### 输入校验
- [ ] 所有外部输入都校验类型/长度/格式了吗？
- [ ] SQL 用参数化查询了吗（不是字符串拼接）？
- [ ] 用户输出做了 HTML 转义了吗（防 XSS）？

### 数据保护
- [ ] 密钥/密码没有硬编码在代码里吧？
- [ ] 敏感数据传输走 HTTPS 了吗？
- [ ] 日志里没有打印密钥/PII 吧？

### 常见漏洞
- [ ] `eval` / 动态代码执行？
- [ ] CSRF 防护？
- [ ] 限流防暴力破解？
- [ ] 文件上传校验？

## 要做的

- 系统化（六个类别全覆盖）。
- 按影响排优先级。
- 当成活文档持续维护。

## 不要做的

- 跳过某个类别。
- "应该安全吧"（假设安全）。
- 忽略低概率/高影响的风险。
- 只识别不缓解。
"""


_ACCESSIBILITY = """\
# 无障碍 / Accessibility Compliance

适用于：审查无障碍、实现 ARIA、为屏幕阅读器开发、确保包容性体验。

## 八条最佳实践

1. **语义 HTML 优先于 ARIA**——能用 `<button>` 就别用 `<div role="button">`。
2. **用真实用户测试**，不只是自动化工具。
3. **键盘优先**——所有交互都能用键盘完成。
4. **样式化焦点，不要删掉** `:focus` outline。
5. **所有非文本内容都要有文字替代**（alt 文本）。
6. **支持 200% 缩放**不破版。
7. 动态内容用 **live region** 通知辅助技术。
8. 尊重 `prefers-reduced-motion` / `prefers-contrast`。

## 常见问题清单

- [ ] 图片缺 alt 文本。
- [ ] 对比度不足（正文 < 4.5:1）。
- [ ] 键盘陷阱（Tab 进去出不来）。
- [ ] 表单缺 label。
- [ ] 自动播放媒体。
- [ ] 用 div 重造原生控件（丢掉无障碍语义）。
- [ ] 缺 skip link（跳过导航到主内容）。
- [ ] 焦点顺序 ≠ 视觉顺序。

## 测试工具栈

- **自动化**：axe DevTools / WAVE / Lighthouse。
- **手动**：VoiceOver (macOS) / NVDA (Win) / TalkBack (Android)。
- **模拟器**：NoCoffee（模拟视觉障碍）。
"""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def builtin_skills() -> list[Skill]:
    """Return all built-in skills (fresh copies each call).

    Each skill's ``path`` points back to this module so diagnostics can
    identify the source. Scope is ``"builtin"``.
    """
    from pathlib import Path

    src = Path(__file__)
    defs = [
        ("systematic-debugging", "遇到 bug、测试失败或非预期行为时使用，在提出修复前必须先做根因调查。", _SYSTEMATIC_DEBUGGING),
        ("tdd", "实现任何功能或修 bug 时使用，在写实现代码前先写失败的测试（红绿重构）。", _TEST_DRIVEN_DEVELOPMENT),
        ("code-review", "审查 PR、合并前自检、建立审查规范时使用。提供四阶段流程与严重等级标签。", _CODE_REVIEW),
        ("frontend-design", "构建新 UI 或重塑界面时使用，让设计有刻意感而非 AI 模板感。", _FRONTEND_DESIGN),
        ("verify-completion", "即将声称工作完成/修好/测试通过时使用，提交前必须跑验证拿证据。最通用。", _VERIFICATION_BEFORE_COMPLETION),
        ("api-design", "设计新 API、审查 API 规范时使用。REST 规则、版本化、分页、限流。", _API_DESIGN),
        ("security-review", "分析系统安全、做威胁建模时使用。STRIDE 六维度逐一覆盖。", _SECURITY_REVIEW),
        ("accessibility", "审查无障碍、实现 ARIA、为屏幕阅读器开发时使用。WCAG 2.2 实践。", _ACCESSIBILITY),
    ]
    return [
        Skill(name=name, description=desc, body=body, path=src, scope="builtin")
        for name, desc, body in defs
    ]


def builtin_skill_names() -> set[str]:
    return {s.name for s in builtin_skills()}


__all__ = ["builtin_skill_names", "builtin_skills"]
