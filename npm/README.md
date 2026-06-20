# mbridge (npm)

One-command install for **ModelBridge** — 国产模型优先的 AI Coding Agent + CLI.

```bash
npm install -g mbridge
mbridge            # 直接进入会话；机器上无需安装 Python
```

## 它是怎么工作的（为什么不用 Python）

这个 npm 包**不是**用 JavaScript 重写的 ModelBridge。它是一个很薄的「投递壳」：

- 安装时（`postinstall`）按你的平台/架构，从 GitHub Release 下载对应的
  **自包含二进制**（PyInstaller 打包，**里面自带 Python 运行时**），解压到包内的
  `vendor/`。
- 你敲 `mbridge` 时，`bin/cli.js` 把命令原样转发给那个二进制。

所以最终用户**不需要安装 Python**，ModelBridge 的 Python 代码库也**完全没有改动**。
（这套做法和 esbuild / @biomejs/biome / Ruff 的 npm 包同理。）

## 平台支持

| 平台 | 架构 | 状态 |
|---|---|---|
| Windows | x64 | ✅ |
| macOS | Apple Silicon (arm64) | ✅ |
| Linux | x64 (glibc) | ✅ |
| macOS Intel / Linux arm64 | — | ❌（请改用 `pipx install modelbridge`，需 Python 3.10+）|

需要 `tar`（Windows 10 1803+ / macOS / Linux 自带）。

## 环境变量

| 变量 | 作用 |
|---|---|
| `MBRIDGE_SKIP_DOWNLOAD=1` | 跳过下载（CI/开发用） |
| `MBRIDGE_DOWNLOAD_BASE=<url>` | 自定义下载地址（镜像） |
| `MBRIDGE_FORCE_PLATFORM` / `MBRIDGE_FORCE_ARCH` | 强制平台/架构（测试用） |

## 升级 / 卸载

```bash
npm install -g mbridge@latest
npm uninstall -g mbridge
```

源码与文档：<https://github.com/CrisXie4/ModelBridge>
