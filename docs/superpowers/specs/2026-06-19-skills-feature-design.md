# ModelBridge Skills — 设计文档

- 日期: 2026-06-19
- 范围: 让 mbridge 的 AI 能发现、按需加载、使用**用户自定义 skill**(Claude Code 兼容)
- 状态: 设计已获用户批准(2026-06-19)

## 1. 目标与背景

用户希望给 mbridge 的 AI 加「skill」能力:用户放入任意的 skill(指令文件),AI 启动时知道有哪些,需要时把某个 skill 的完整指令拉进对话并照做。**与 Claude Code 的 skill 格式兼容**(可直接复用现成 skill)。

**安全前提(用户明确要求):** skill 本质是「会被 AI 读取并执行的任意指令」,可能含恶意内容(prompt injection / 让 AI 干坏事)。因此:加 skill 时必须**醒目警告 + 免责声明**;AI 加载某 skill 前必须**用户确认**。维护者不对用户自加的恶意 skill 负责。

## 2. Skill 格式(Claude Code 兼容)

- 一个 skill = 一个文件夹 `<skill-name>/SKILL.md`。
- `SKILL.md` 顶部 YAML frontmatter:`name`(slug)、`description`(一行,用于索引与相关性判断);其后是 markdown 正文(给 AI 的指令)。
- 文件夹内可带附属文件(正文里引用)。
- 解析容错:frontmatter 缺 `name`/`description` 或 YAML 损坏 → 跳过该 skill + 启动时告警,不崩。

## 3. 发现(新模块 `modelbridge/skills/`)

- 扫描两处(镜像现有规则文件发现):
  - 全局:`~/.modelbridge/skills/`
  - 项目:`<project>/.modelbridge/skills/`(同名覆盖全局)
- 每个子目录的 `SKILL.md` → `Skill` dataclass:`name`、`description`、`body`、`path`、`scope`(global/project)。
- 提供 `discover_skills(project_path) -> list[Skill]`、`find_skill(name) -> Skill | None`。

## 4. 索引注入(系统提示)

- 把每个 skill 的 `- <name>: <description>` 拼成紧凑索引,作为 `PromptBuilder` 的一个新 section(放在规则/项目之后)。
- 索引文案告诉 AI:「以下是可用 skill;判断相关时调用 `use_skill("<name>")` 加载其完整指令(会请求用户确认)。不要凭名字猜测内容。」
- **缓存权衡(已接受):** 索引进系统提示,skill 增删会动前缀缓存;skill 很少变,可接受。无 skill 时不注入该 section。

## 5. `use_skill` 工具(新 agent 工具)

- 子类化 `agent.tools.base.Tool`,注册进 `ToolRegistry` → 经 `registry.openai_tools()` 走 `tools=` 给模型(与 read_file 等一视同仁)。
- `json_schema`: `{name: string}`(必填)。
- `execute(args, ctx)`:
  1. `find_skill(name)`;找不到 → `Tool.err("未找到 skill <name>")`。
  2. `ctx.confirm(tool="use_skill", summary="加载 skill <name>", detail="<来源路径> · 该 skill 的指令将进入对话并被执行,请确认你信任它")`,`allow_always=True`(支持「本会话对该 skill 始终同意」,按 skill 名分组)。
  3. 拒绝 → `Tool.err("用户拒绝加载 skill <name>")`。
  4. 同意 → 返回 `ToolResult(content=<SKILL.md 正文>)`(进入对话,AI 随后照做);正文按 `_MAX_RESULT_CHARS` 截断保护上下文。

## 6. CLI 命令组 `mbridge skill`

- `mbridge skill list` — 列出已发现 skill(name / description / scope / path)。
- `mbridge skill show <name>` — 打印某 skill 的正文。
- `mbridge skill add <path>` — 把本地 skill 文件夹拷进 `~/.modelbridge/skills/`。**触发安全警告 + 免责**(见 §7);**仅本地路径,不支持 URL 下载**(远程更危险,v1 不做)。
- `mbridge skill remove <name>` — 删除一个 skill(确认后)。
- 用户也可直接把 skill 文件夹丢进 skills 目录;`add` 只是便利封装。

## 7. 安全 / 免责

- **`skill add` 警告(醒目,红/黄)**:
  `⚠ skill 是会被 AI 读取并执行的任意指令,可能包含恶意内容(诱导 AI 删文件、泄露密钥、执行命令等)。请自行核实来源与内容是否安全。一切后果由你自负,ModelBridge 维护者不承担责任。`
  添加前要求用户键入确认(如 `yes`)。
- **`use_skill` 加载前确认**(§5 步骤 2):显示来源路径 + 信任提示。
- **REPL 启动提示**:若加载了 N 个 skill,系统行提示「已加载 N 个用户 skill(`/skills` 或 `mbridge skill list` 查看)」。
- **README**:新增「Skills」章节 + 上述免责声明。

## 8. 错误处理

- 坏 SKILL.md:跳过 + 启动告警。
- `use_skill` 未知名 / 缺参:返回工具错误给模型(不崩循环,沿用 Tool 边界)。
- 名字冲突(项目 vs 全局):项目覆盖全局,记日志。
- skills 目录不存在:视为空,不报错。

## 9. 测试策略

- 发现:解析 frontmatter、全局+项目两处、同名覆盖、坏 SKILL.md 跳过。
- 索引:`PromptBuilder` 注入该 section;无 skill 时不注入。
- `use_skill` 工具:确认 yes → 返回正文;no → 错误;未知名 → 错误;always → 二次不再问。
- CLI:`skill list/show/add/remove`;`add` 触发警告且需键入确认;`remove` 删除生效。

## 10. 非目标(YAGNI)

- 不做 skill 市场 / 远程注册 / URL 下载(v1 只本地文件)。
- 不做代码沙箱(skill 是指令不是可执行代码;安全模型 = 加时警告 + 用时确认)。
- 不自动执行未确认的 skill。
- 不改 skill 自身的执行语义(skill 正文就是普通指令,AI 照常理解)。
