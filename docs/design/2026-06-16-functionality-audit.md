# 功能体检 — 已验证发现清单

- 日期: 2026-06-16
- 方法: 11 子系统并行审计 → 每条对抗式验证(69 个 agent)
- 结果: 58 条原始 → **51 条确认** / 7 条被验证毙掉
- 说明: 按主题聚类;每条标注 严重度(high/med/low)· 类别 · 工作量(S/M/L)· 证据(file:line)

---

## 主题 1 · Windows/CJK 编码串码(平台 · 国产+Windows 最该修)

同根:多处 `subprocess(text=True)` / 流重配未显式 `encoding='utf-8'`,中文系统默认 GBK → 串码/崩溃。`mcp/transport/stdio.py:104-106` 已有正确写法可抄。

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | S | `bash_tool` subprocess 无 encoding,CJK 输出串码 | agent/tools/bash_tool.py:96-104 |
| high | S | `executor/runner.py` Popen 无 encoding/errors,串码+解码崩溃 | executor/runner.py:78-84 |
| high | S | rules 截断按**字符**算,但预算是**字节** → CJK 超额 3 倍 | prompt/rules_loader.py:221-226 |
| med | S | `bash_tool` 截断提示写「字节」实为「字符」 | agent/tools/bash_tool.py:115-122 |
| med | S | `cli_console` reconfigure 失败静默 pass,管道里仍 GBK | cli_console.py:18-24 |
| med | S | `mcp/server` reconfigure 只 catch AttributeError,漏 ValueError | mcp/server/server.py:119-125 |
| med | S | REPL 输入 UnicodeDecodeError 静默返回 ""，用户莫名被忽略 | cli.py:549-551 |
| med | L | rich Live CJK 歧义宽度叠表头(已有 v4 尾视图缓解,根因未除) | agent/ui.py:136-139,204-205 |

## 主题 2 · 路由正确性(旗舰功能有真 bug)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | S | 同一文件出现两次 → 误判多文件 → 升到昂贵的 AGENT(`findall` 取的是扩展名不是文件) | router/classifier.py:212-216 |
| high | S | LLM 分类器**完全忽略** `context_tokens`,关键词分类器却用它 → 两条路结果不一致 | router/llm_classifier.py:110 |
| high | S | MCP 的 route 工具用关键词分类器,CLI 用 LLM 分类器 → 同输入不同结果 | mcp/server/builtin.py:56 |
| high | S | 贪婪 JSON 正则 `{.*}` 抓到非法 JSON,错误信息又截断 200 字遮住真相 | router/llm_classifier.py:91 |
| high | M | 子串匹配无词边界:「这链接安全吗」命中「安全」→ 误升 EXPERT | router/classifier.py:67-119,188 |
| high | S | 用 `assert` 做运行时校验,`python -O` 下变 AttributeError 崩溃 | cli.py:1367,1392 |

## 主题 3 · 预算/成本没真正保护用户(花钱)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| **high** | M | **主 REPL(无子命令的主入口)完全不调 check_guard/add_spend → 预算形同虚设,可无限花钱** | agent/loop.py:163-305; cli.py:701-721 |
| med | M | 非 hard_stop 时只事后告警、从不拦截,超支静默发生 | cost/budget.py:293-312 |
| high | S | cache 节省额硬编码 0.75,无视用户配的 `cache_hit_input_per_1m` | cost/estimator.py:53; cli.py:1244-1249 |
| high | S | token 估算把标点 //4,JSON/代码类低估 28-40% | cost/estimator.py:235-237 |
| low | S | `_CJK_RANGE` 漏 Hangul/Thai/Arabic(本项目中文为主,低优先) | cost/estimator.py:201-206 |

## 主题 4 · 配置/Profile 静默失效(可靠性)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | M | 激活 profile 不校验所引用模型是否存在 → 配置进入「指向不存在模型」的坏态,路由后续莫名失败 | config.py:335-337 |
| high | M | `RoutingLevels` 字段无校验器,任意字符串可落盘 | models.py:172-177 |
| med | S | profile 删除/改名后 `active_profile` 成悬空引用,无自愈/无 doctor 检查 | config.py:318-324 |
| high | S | MCP config 数字字段遇非法 YAML 类型直接 ValueError 崩(`mbridge mcp list` 就触发) | mcp/config.py:191-197 |
| low | M | 交互建 profile 后不复核模型仍存在 | cli.py:2456-2509 |

## 主题 5 · MCP 边界(可靠性/安全)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | S | 版本不匹配:注释说「非致命」,代码却无条件 raise 断开连接 → 自相矛盾且挡掉前向兼容 server | mcp/session/handshake.py:42-48 |
| high | S | sampling 计数器读改写无锁 → 并发下可突破 `sampling_max_calls` 上限(刚加固的配额被绕过) | mcp/sampling.py:90,99 |
| high | M | 多 server 同 URI 资源静默只留第一个(tools/prompts 有去重日志,resources 没有)→ 静默丢数据 | mcp/manager/catalog.py:66-67,97-101 |

## 主题 6 · 工具/Provider 入参硬化(防御性崩溃)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | S | `list_dir` 的 `max_entries` 非数字 → 未捕获 ValueError 崩(bash_tool 已有正确范式可抄) | agent/tools/file_tools.py:110 |
| high | M | `health_check` 把 `/models` 返回 404 当健康(404<500)→ 假阳性「健康」 | providers/base.py:87-101 |
| med | S | 流式 tool_calls 的 index 非数字 → `int()` 崩、断流 | providers/base.py:467 |
| med | S | 流式 tool_call id 被覆盖而非累加(name/args 是累加的) | providers/base.py:472-473 |
| med | S | 非流式 `tool_calls: []` 未归一为 None,与流式路径不一致 | providers/base.py:173 |
| med | S | 流式 raw 只存 `{chunks: N}`,丢原始块 → 难排障 | providers/base.py:507 |

## 主题 7 · 更新无签名校验(安全 · 独立项)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | M | `mbridge update` 下载的二进制**无任何签名/哈希校验** → MITM/被黑 CDN 可投毒 | updater.py:267-293 |

## 主题 8 · CLI 体验 papercut(UX)

| 严重 | 工作量 | 问题 | 证据 |
|---|---|---|---|
| high | S | help 里写 `mbridge browser "..."` 但**该命令不存在** → 照做报「No such command」(与 IA 重构 R1 修 help 重叠) | bridge/cli.py:86,110 |
| high | S | `bridge_endpoint.json` 缺 `port` 键 → KeyError 崩,无法重连 | bridge/control.py:270-272 |
| high | S | 未知模型名静默回落 32K 上下文窗口,无告警 | context/windows.py:130-167 |
| high | ux | `~/.modelbridge/system.md` 读失败静默回落默认,用户毫不知情 | prompt/rules_loader.py:132-135 |
| high | S | `--route` + `--dry-run` 报错指向 `mbridge route`,没解释二者本质冲突 | cli.py:1060-1065 |
| med | bug | 前缀 hash 把空 section 也拼进去,空→非空时 hash 漂移,破坏缓存稳定 | prompt/builder.py:317-319 |
| med | ux | `--yes` 顶层 help 说「自动同意全部」,实际 edit 仍跑安全检查 → 描述误导 | cli.py:282 vs 2830-2832 |
| med | M | 超时默认值四处不一(REPL 120s / model test 30s / doctor all 20s)无说明 | cli.py:298,1753,1895 |
| med | ux | doctor 把已有详细 hint 再包一层「详情:」→ 嵌套冗余 | doctor.py:319 |
| med | reliability | history 里 system 消息被静默过滤,无告警 | prompt/builder.py:168-172 |
| med | bug | `model init` Ctrl-C 用退出码 130 且 noqa 注释贴错 | cli.py:1669 |
| med | reliability | Windows CR+LF flush 的 `except Exception: pass` 吞所有 I/O 错 | cli.py:543-547 |
| low | ux | doctor 超时的 hint 落在 detail 列而非 hint 列,建议不一致 | doctor.py:234-239 |

---

## 被毙掉的 7 条
对抗验证判定为误读/不成立(`is_real=false`),已剔除,不在上表。
