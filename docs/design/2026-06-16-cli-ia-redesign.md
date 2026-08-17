# CLI 信息架构重构 — 设计文档

- 日期: 2026-06-16
- 范围: `mbridge` 命令行的信息架构(命令命名 / 分组 / 可见性),**不改命令自身的行为**
- 结构哲学: 方案 A —— 动作优先 + 精简名词组
- 兼容策略: 软弃用(旧命令保留为隐藏别名 + 弃用提示,v1.2 移除)
- 状态: 设计已获用户口头批准(2026-06-16),待用户复核本文档

## 1. 问题

当前 CLI 表面膨胀:7 个顶层命令 + 12 个子命令组 ≈ 55 个命令。用户视角下的真实问题:

1. **门面墙** —— `mbridge --help` 一上来约 19 个条目,新用户找不到「该用哪个」;且 root help 文案("init / model / doctor / route / cost / budget / cache / profile")与实际命令不符。
2. **命名撞车** —— `init` 出现 3 次(`init` / `model init` / `project init`);`run` 两义(白名单 shell `run` vs Native host `bridge run`);三个「edit」(`edit` / `prompt edit` / `patch`);`test` 散落(`model test` / `route test` / `doctor`)。
3. **调试命令混入日常** —— `chat [测试用]`、`prompt hash/diff`、`mcp call/ping/read/serve`、`cache reset/clean`、`patch` 全暴露在主 help。
4. **过深嵌套** —— `mbridge bridge control on` 三层,且是常用开关。

## 2. 目标信息架构(方案 A)

### 2.1 第 1 层 · 裸命令 + 日常动作(`--help` 顶层可见)

| 命令 | 说明 | 变化 |
|---|---|---|
| `mbridge` | 进入 REPL | 不变(主入口) |
| `mbridge ask "<prompt>"` | 单轮提问 / 可管道 | `chat` 改名转正,去掉「测试用」标记 |
| `mbridge edit "<需求>"` | AI 改代码(走 diff) | 不变;新增 `--undo`(见 2.4) |
| `mbridge run "<cmd>"` | 白名单 shell | 不变 |
| `mbridge route "<prompt>"` | 路由分析 | 不变;`route test` → `doctor route` |
| `mbridge doctor [model NAME\|all\|route]` | 环境/模型/路由自检 | 吸收 `model test`、`route test` |
| `mbridge init` / `update` / `version` | 生命周期 | 不变 |

### 2.2 第 2 层 · 管理名词组(7 组,可见)

```
mbridge model     add · list · remove
mbridge config    show · upgrade · profile {list,use,add,show,remove}
mbridge usage     (默认仪表盘) · cost · budget · cache
mbridge prompt    list · show · edit · set-system · reset
mbridge project   scan · rules
mbridge mcp       list · tools · resources · prompts
mbridge bridge    install · uninstall · status · on · off
```

### 2.3 隐藏但仍可用(不上 help;调用时打弃用提示并委托到新命令)

| 旧命令 | 去向 |
|---|---|
| `chat` | → `ask` |
| `model init` | → `model add`(本就是其别名) |
| `model test` | → `doctor model` |
| `cost estimate` | → `usage cost` |
| `budget show/set` | → `usage budget [set]` |
| `cache stats/reset/clean` | → `usage cache [...]` |
| `profile *` | → `config profile *` |
| `bridge control on/off/status` | → `bridge on/off/status` |
| `project init` | → `project rules init`(生成 AGENT.md) |
| `route test` | → `doctor route` |
| `prompt hash` / `prompt diff` | 隐藏(纯前缀缓存调试),保持原路径不弃用 |
| `mcp call/ping/read/serve` | 隐藏(高级/调试),保持原路径不弃用 |
| `patch preview/apply/rollback` | 隐藏(`edit` 的内部环节,降为高级),保持原路径不弃用 |
| `bridge run` | **仅隐藏,不改名、不弃用** —— 浏览器经 manifest/launcher 自动拉起的 Native Messaging 宿主入口,改名会破坏已安装的 launcher |

### 2.4 三个已锁定的判断

1. **`chat` → `ask` 转正**:作为正式的非交互单轮命令(脚本 / 管道友好),不再标「测试用」。
2. **`profile` 并入 `config`**(`config profile use X`,3 层):原则是「频繁操作拍平、罕见操作允许嵌套」,profile 属罕见,可接受 3 层;而 `bridge on/off` 是频繁开关,拍平到 2 层。
3. **`patch` 整组降为隐藏高级**,但把最常用的「回滚上一次 AI 改动」提为可见的 `mbridge edit --undo`(内部仍走 patch rollback 链路)。

### 2.5 净效果

- `--help` 顶层 ~19 → **约 11**(裸 + 6 动作 + doctor + 3 生命周期),其中真正「日常」的 5 个动作。
- 管理组 12 → **7**。
- 无频繁命令埋在 3 层(仅罕见的 `config profile` 允许 3 层)。
- 四个问题全部命中。

## 3. 弃用机制

集中一张「旧 → 新」映射表,由一个 helper 注册隐藏 Typer 命令:被调用时先向 **stderr** 打一行
`⚠ \`mbridge <旧>\` 已移至 \`mbridge <新>\`,将在 v1.2 移除。` 然后委托到新命令的实现函数(共享同一份逻辑,不复制实现)。v1.2 清理 = 删这张表 + 对应隐藏命令。

注意区分两类「隐藏」:
- **弃用别名**(打提示、有迁移目标):2.3 表上半。
- **纯隐藏**(不打提示、保持原路径,只是不上 help):`prompt hash/diff`、`mcp call/ping/read/serve`、`patch *`、`bridge run`。这些功能不变、不迁移,只是从主 help 收起。

## 4. 实现拆轮(每轮独立可发、测试保持绿)

| 轮 | 内容 | 风险 |
|---|---|---|
| **R1** | 给调试命令加 `hidden=True`;修 root help 文案使其与实际命令一致;搭「弃用别名」基础设施(helper + 空映射表) | 零行为变化 |
| **R2** | 建 `usage` 组(吸收 cost/budget/cache);`profile` 并入 `config`;`model test` → `doctor model`;旧命令留弃用别名 | 低 |
| **R3** | `chat` → `ask`;拍平 `bridge control` → `bridge on/off`;`project init` → `project rules init`;`route test` → `doctor route`;`bridge run` 转纯隐藏;`edit --undo` | 中 |
| **R4** | README / shell 补全 / help 文案同步;别名 + 弃用提示 + 隐藏可见性的测试 | 低 |

## 5. 非目标

- 不改任何命令**自身的行为 / 参数语义**(本次只动命名、分组、可见性)。
- v1.2 前不删除任何功能。
- 不触碰引擎 / providers / 路由的功能逻辑(那属另一条「功能体检」轨道,单独立项)。

## 6. 测试策略

- 新命令路径可用(`ask` / `usage cost` / `config profile use` / `bridge on` …)。
- 旧命令路径仍可用,**且向 stderr 输出弃用提示**(对弃用别名);纯隐藏命令仍可用且**不**输出提示。
- 隐藏命令不出现在 `--help` 顶层 / 组内 help,但可正常调用。
- `--help` 顶层条目数较重构前显著下降(断言上限)。
- `bridge run` 仍按原名可被 launcher 调起(回归)。

## 7. 风险与缓解

- **launcher 引用 `bridge run`** → 不改名、不弃用,仅隐藏。
- **shell 补全过期** → R4 重新生成并纳入测试。
- **help 文案与实际漂移** → R1 先修 root help;R4 统一校对。
- **用户肌肉记忆 / 外部脚本** → 软弃用过渡期 + stderr 提示,v1.2 才移除。
