# ModelBridge 文档索引

本目录存放 ModelBridge 的设计与架构文档。按三类组织：

- [`architecture/`](architecture/) — 系统级架构总览（已实现模块的设计蓝图）
- [`design/`](design/) — 功能设计文档（spec，描述「做什么、怎么决策」）
- [`plans/`](plans/) — 实施计划（plan，描述「分几步做、每步改哪些文件」）

> 项目主文档在仓库根：[`../README.md`](../README.md)（用户面向）、[`../AGENT.md`](../AGENT.md)（Agent 运行时规则）。

---

## architecture/

| 文档 | 说明 | 状态 |
|---|---|---|
| [mcp-architecture.md](architecture/mcp-architecture.md) | MCP 模块架构设计（传输 / 握手 / 工具 / 资源 / prompts / 多 server 治理 / sampling / 反向 server），M0–M7 | 已实现 |

## design/

| 文档 | 日期 | 说明 |
|---|---|---|
| [2026-06-16-cli-ia-redesign.md](design/2026-06-16-cli-ia-redesign.md) | 2026-06-16 | CLI 信息架构重构（命令命名 / 分组 / 可见性，不改行为） |
| [2026-06-16-functionality-audit.md](design/2026-06-16-functionality-audit.md) | 2026-06-16 | 功能体检已验证发现清单（51 条确认问题，按主题 + 严重度） |
| [2026-06-19-multimodal-image-input.md](design/2026-06-19-multimodal-image-input.md) | 2026-06-19 | 多模态图像识别支持设计 |
| [2026-06-19-skills-feature.md](design/2026-06-19-skills-feature.md) | 2026-06-19 | Skills 特性设计（Claude-Code 兼容的 `SKILL.md`） |
| [2026-08-07-cli-info-display.md](design/2026-08-07-cli-info-display.md) | 2026-08-07 | CLI 信息展示优化（气泡色条 / 上下文面板 / 斜杠补全） |
| [2026-08-11-search-removal-and-repl-ux.md](design/2026-08-11-search-removal-and-repl-ux.md) | 2026-08-11 | 移除收费联网搜索 + REPL 体验优化（规则覆盖 / 子命令补全 / AI 内联补全） |

## plans/

| 文档 | 日期 | 说明 |
|---|---|---|
| [2026-06-16-batch1-subprocess-stream-utf8.md](plans/2026-06-16-batch1-subprocess-stream-utf8.md) | 2026-06-16 | 子进程与流的 UTF-8 正确性（CJK / Windows GBK 修复） |
| [2026-06-16-batch2-router-correctness.md](plans/2026-06-16-batch2-router-correctness.md) | 2026-06-16 | 路由分类器正确性（文件去重 / 词边界 / JSON 提取 / context_tokens） |
| [2026-06-16-batch3-input-hardening.md](plans/2026-06-16-batch3-input-hardening.md) | 2026-06-16 | 工具与流式输入硬化 |
| [2026-06-19-multimodal-image-input.md](plans/2026-06-19-multimodal-image-input.md) | 2026-06-19 | 多模态图像识别实施计划（对应同名 design） |
| [2026-06-19-skills-feature.md](plans/2026-06-19-skills-feature.md) | 2026-06-19 | Skills 特性实施计划（对应同名 design） |

---

## 约定

- **新增 design 文档**命名：`YYYY-MM-DD-<topic>.md`，放在 `design/`。
- **新增 plan 文档**命名：`YYYY-MM-DD-<topic>.md`，放在 `plans/`，与对应 design 同名以便配对。
- **新增架构总览**放 `architecture/`，无需日期前缀（架构文档长期演进）。
- 文档语言以中文为主（与项目一致），代码示例与命令保留英文。
