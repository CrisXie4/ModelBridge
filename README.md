# ModelBridge 浏览器侧边栏

> 本分支 (`extension`) **只包含浏览器扩展**，独立于主仓库历史维护——
> 提交、版本都只涉及扩展本身。Python 端（引擎 / 宿主 / CLI）在
> [`main` 分支](https://github.com/CrisXie4/ModelBridge/tree/main)。

用聊天窗口驱动国产模型 **读写当前网页**。聊天 UI 跑在 Chrome/Edge 侧边栏，
通过 **Native Messaging** 连到本地 **LocalBridge** 宿主，复用 `mbridge` 引擎
（providers / router / agent loop）。

```
侧边栏聊天 UI  ──connectNative──►  LocalBridge 宿主 (Python)  ──►  国产模型
   │                                     │
   └─ content 脚本读/操作 DOM  ◄─tool_call─┘  (read_page / click / fill …)
```

## 安装（一次性）

### 1. 装好引擎并配置模型

```bash
# 在 main 分支的仓库目录 (或直接装 Release 安装包)
pip install -e .
mbridge init
mbridge model init          # 加一个模型 (DeepSeek / Qwen / …)
```

> ⚠️ **API key 必须存在 keyring 或 config.yaml 里**。浏览器从 GUI 启动宿主，
> 读不到你在终端 `export` 的环境变量。用 `mbridge model init` 添加的 key 会进
> keyring，没问题；手动设的 `*_API_KEY` 环境变量则**不会**被宿主看到。

### 2. 加载扩展

1. 打开 `chrome://extensions`（Edge 是 `edge://extensions`）。
2. 右上角打开 **开发者模式**。
3. 点 **加载已解压的扩展程序**，选择**本分支检出的目录**（manifest.json 所在的根目录）。

> manifest.json 里固定了 `key`（公钥），所以扩展 ID 恒为
> `pcnidhfpkombmcnpcojlpdokckenlkop`——无论 unpacked 还是 crx 安装，
> 在哪台机器上都一样，不需要复制 ID。

### 3. 注册 Native Messaging 宿主

```bash
mbridge bridge install        # 默认就用上面的官方扩展 ID，无需参数
```

这会写入宿主 manifest + 启动器，并在注册表（Windows）/ 配置目录（mac/Linux）
注册 Chrome 和 Edge。只想注册 Chrome 加 `--chrome-only`；自己 fork 改了 key
的话用 `--extension-id <你的ID>` 覆盖。

### 4. 重新加载扩展并使用

回到 `chrome://extensions` 点扩展的 **刷新**，然后点工具栏图标打开侧边栏。
顶部状态点变绿、模型下拉框有内容即表示宿主连上了。

## 用法

- 直接聊天即可。
- 让它读页面：「**总结这个页面**」「这篇文章讲了什么」——它会调用 `read_page`。
- 让它操作页面：「**在搜索框填 hello 并点搜索**」——`fill` / `click` 会先在
  侧边栏弹出**确认卡片**（同意 / 本会话总是 / 拒绝），同意后才执行。
- 可用工具：`read_page` `get_selection` `query_dom` `extract`（读）、
  `click` `fill` `navigate`（写，需确认）。

## 让 `mbridge` 命令行也能操作网页 — 默认关闭

侧边栏是独立用的；但你也可以让**主命令 `mbridge` 会话**在干活时顺手操作浏览器——
比如让 AI 一边看代码一边去网页上查文档、点按钮。浏览器操作是 `mbridge` 里的**工具**
（和 `read_file` / `run_bash` 并列），AI 自己判断何时需要，调用插件去执行。

浏览器工具在 `mbridge` 里**默认就有**（不需要任何 flag）。只要一次性开启安全闸 +
打开侧边栏即可用：

```bash
# 一次性：开启命令行联动 (生成 token)，然后打开浏览器侧边栏让宿主带开关重启
mbridge bridge control on

# 之后正常用 mbridge —— 浏览器工具自动就在
mbridge
you: 看看当前页面讲了什么，把要点记到 notes.md
[tool · read_page] ...
[tool · write_file (notes.md)]  执行? [y/N/a]

# 不用了就关掉（可选）
mbridge bridge control off
mbridge bridge control status     # 查看开关状态
```

- `mbridge` 启动时会打印一行 `网页控制: 已连接侧边栏` 或 `未连接 (…)`，一眼看出状态。
- 浏览器工具：`read_page` `get_selection` `query_dom` `extract`（读）、
  `click` `fill` `navigate`（写，调用前在**终端**里 `[y]es / [N]o / [a]lways` 确认；
  对任一网页写操作选 `a`，本会话所有网页写操作都免确认）。
- DOM 操作在浏览器当前标签页执行，所以**侧边栏/浏览器要开着**。
- 没开启联动 / 没开侧边栏时，工具仍在，调用会返回友好提示，不影响文件/shell 等其它功能。
- 文件工具、shell（`--allow-bash`）、浏览器工具同一会话里混用。
- 工具卡在慢页面时按 **Ctrl-C** 可中断本轮、回到输入，REPL 不退出。

> 原理与安全：Native Messaging 是浏览器发起的，`mbridge` 进程挤不进那条管道。开启后
> 宿主才会额外监听一个 `127.0.0.1` 本地端口；`mbridge` 的浏览器工具把**单个动作**中
> 转给宿主，宿主再转给插件执行。连接要带 `mbridge bridge control on` 生成的 token
> （存在 `~/.modelbridge/bridge_control.json`，**不写进端点文件**）。**默认关闭 = 不
> 监听任何端口、不接受任何连接**。`off` 后重开侧边栏即彻底停止监听。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| 状态点红、提示连接断开 | 没注册宿主或扩展 ID 变了。重跑 `mbridge bridge install --extension-id <ID>` 再刷新扩展。`mbridge bridge status` 看当前状态。 |
| 回复报「未找到模型 / 无 default_model」 | 跑 `mbridge model init`、`mbridge model list` 配置模型。 |
| 回复报鉴权 401/403 | API key 不在 keyring/config（见上面的 ⚠️）。 |
| 「当前页面不允许脚本注入」 | 在 `chrome://`、扩展商店等内部页上无法操作 DOM，切到普通网页。 |
| 改了宿主 Python 代码不生效 | 宿主进程随侧边栏启动；关掉侧边栏再打开会重启宿主。 |
| 浏览器工具报「联动未启用」 | 先 `mbridge bridge control on`，再重开侧边栏。 |
| 浏览器工具报「未找到宿主端点」 | 已开启但侧边栏没开（宿主没在跑），或开启后没重开侧边栏。打开/重开侧边栏。 |
| `mbridge` 启动提示「网页控制: 未连接」 | control 没开或侧边栏没开。工具仍注册，连上后即可用，不影响其它功能。 |
| `mbridge browser` 报「busy」 | 侧边栏或另一个 CLI 正在跑一个回合；等它结束。一次只允许一个回合。 |

## 卸载

```bash
mbridge bridge uninstall
```

再到 `chrome://extensions` 移除扩展即可。

## 开发说明

- **本分支**只有扩展：`manifest.json` / `background.js` / `sidepanel.{html,js,css}` /
  `icons/`。DOM 执行逻辑在 `sidepanel.js` 的 `pageToolDispatcher`（注入到页面运行）。
- **main 分支**（Python 端）：帧协议 / 宿主 / 安装器在 `modelbridge/bridge/`，
  远程浏览器工具在 `modelbridge/agent/tools/browser_tools.py` +
  `browser_write_tools.py`，测试 `tests/test_bridge_*.py`。
- 协议（消息类型 / 字段）改动需要**两个分支配合**：main 改 `protocol.py`，
  本分支改 `sidepanel.js` 的 `handleFrame`。
- 打包 crx：`chrome.exe --pack-extension=<本分支目录> --pack-extension-key=<extension.pem>`
  （私钥不在 git 里，自行妥善保管）。产物挂 GitHub Release。
