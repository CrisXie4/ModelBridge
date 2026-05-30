# ModelBridge 打包指南

这个目录里的脚本把 ModelBridge 打包成可独立运行的二进制 / 安装器，分发给**没有 Python 环境**的最终用户。

```
packaging/
├─ mbridge_entry.py        # PyInstaller 的入口脚本
├─ mbridge.spec            # PyInstaller 配置 (hidden imports、onedir 布局)
├─ installer.iss           # Inno Setup 配置 (Windows setup.exe，含加 PATH)
├─ build_windows.ps1       # 一键打 Windows 安装包 (PowerShell — 主脚本)
├─ build_windows.bat       # PS 脚本的极简转发器 (双击 .bat 也能用)
├─ build_macos.sh          # 一键打 macOS tar.gz
├─ build_linux.sh          # 一键打 Linux tar.gz
└─ Output/                 # 构建产物 (gitignore)
```

---

## TL;DR

| 目标平台 | 在哪台机器构建 | 一行命令 | 产物 |
|---|---|---|---|
| Windows | Win10+ | `packaging\build_windows.bat` （或直接双击）| `Output\ModelBridge-Setup-0.4.0.exe` |
| macOS | mac 11+ (任意 CPU 架构) | `bash packaging/build_macos.sh` | `Output/mbridge-0.4.0-macos-<arch>.tar.gz` |
| Linux | Ubuntu 22.04+ (任意 CPU 架构) | `bash packaging/build_linux.sh` | `Output/mbridge-0.4.0-linux-<arch>.tar.gz` |
| **三平台同时** | GitHub Actions | `git tag v0.4.0 && git push --tags` | GitHub Release 自动挂三份 |

> **PyInstaller 不能跨平台**：Windows 上打的 exe 只能在 Windows 跑，mac 上打的 tar.gz 只能 mac 跑。要同时分发三平台，要么自己跑三台机器，要么用 CI（推荐）。

---

## 前置条件

### Windows（打 setup.exe）
- Python 3.10+ on PATH
- `pip install -e .` 从仓库根目录运行（让本地 Python 能 import modelbridge）
- **Inno Setup 6**：免费工具，下载 https://jrsoftware.org/isdl.php ，6 MB，下一步到底装。装好后默认路径 `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`。如果装在别处，编辑 `build_windows.bat` 顶部的 `ISCC_PATH`。

### macOS（打 tar.gz）
- Python 3.10+
- `pip install -e .`
- 不需要额外工具

### Linux（打 tar.gz）
- Python 3.10+
- `pip install -e .`
- 想要更广兼容性，在 `manylinux2014` Docker 容器里跑（PyInstaller 链 glibc，构建机 glibc 太新会让旧发行版跑不起来）

---

## 各平台流程详解

### Windows

```cmd
cd E:\ModelBridge
packaging\build_windows.bat
```

输出：
1. `dist\mbridge\mbridge.exe` —— 可独立运行的 onedir 包（拷走整个 `dist\mbridge\` 文件夹就能用，免安装）
2. `packaging\Output\ModelBridge-Setup-0.4.0.exe` —— 真正的安装器

把 `ModelBridge-Setup-0.4.0.exe` 发给最终用户，他双击：
- 默认装到 `%LOCALAPPDATA%\Programs\ModelBridge\`（**不需要管理员权限**）
- 可以勾选"Install for all users"装到 `C:\Program Files\ModelBridge\`（需要 UAC）
- 自动把安装目录加到 PATH（用户或系统级，取决于上面那个勾）
- 开始菜单建 `ModelBridge Shell` 快捷方式（一个预打开的 CMD 窗口）
- 卸载时自动从 PATH 移除

装完后**新开一个 CMD 窗口**（不是装之前已开的那个 —— 老窗口的 PATH 是装之前的快照），输入：
```cmd
mbridge version
mbridge init
mbridge model init
```

### macOS

```bash
cd /path/to/ModelBridge
bash packaging/build_macos.sh
```

输出：`packaging/Output/mbridge-0.4.0-macos-arm64.tar.gz`（Apple Silicon）或 `-x86_64.tar.gz`（Intel）

最终用户解压 + 加 PATH：
```bash
tar xzf mbridge-0.4.0-macos-arm64.tar.gz -C ~/.local
echo 'export PATH="$HOME/.local/mbridge:$PATH"' >> ~/.zshrc
source ~/.zshrc
mbridge version
```

**Gatekeeper 提示**：未签名的 binary 第一次跑会被拦截。绕过方法：右键 → 打开（一次性放行）。或者跑 `xattr -d com.apple.quarantine ~/.local/mbridge/mbridge`。要彻底解决得申请 Apple Developer ID 做签名 + 公证 —— 是钱事不是技术事，先不做。

### Linux

```bash
cd /path/to/ModelBridge
bash packaging/build_linux.sh
```

输出：`packaging/Output/mbridge-0.4.0-linux-x86_64.tar.gz`

最终用户：
```bash
tar xzf mbridge-0.4.0-linux-x86_64.tar.gz -C ~/.local
ln -sf ~/.local/mbridge/mbridge ~/.local/bin/mbridge   # 假设 ~/.local/bin 已在 PATH
mbridge version
```

或者系统级：`tar xzf ... -C /opt && sudo ln -sf /opt/mbridge/mbridge /usr/local/bin/mbridge`。

---

## CI 自动构建（推荐分发方式）

`.github/workflows/release.yml` 已经配好：

```bash
# 在本地：
git tag v0.4.0
git push --tags

# 等 ~5 分钟，CI 三个 runner (windows-latest / macos-latest / ubuntu-22.04)
# 同时跑 PyInstaller + Inno Setup，自动建 GitHub Release，挂上三份产物：
#   - ModelBridge-Setup-0.4.0.exe   (Windows installer)
#   - mbridge-0.4.0-macos-arm64.tar.gz / -x86_64.tar.gz
#   - mbridge-0.4.0-linux-x86_64.tar.gz
```

用户只要去 `https://github.com/<your-org>/modelbridge/releases` 下他平台对应的那份。

**手动触发**：Actions 标签页 → release workflow → Run workflow。手动跑不会建 Release，只把产物挂 artifact 给你下，方便构建出问题时调试。

---

## 常见问题

### Q: 安装包多大？
A: Windows setup.exe ≈ 20 MB；解压后 `mbridge.exe + _internal/` 约 55 MB（含 Python 运行时 + 所有依赖）。可以加 UPX 压缩，但启动会变慢，不值。

### Q: 启动速度？
A: onedir 模式冷启动 ≈ 200ms。onefile 模式 ≈ 1-2 秒（每次要解压到临时目录），所以我们用 onedir。

### Q: Windows SmartScreen 警告"未知发布者"？
A: 正常 —— 我们没买代码签名证书。用户点"More info" → "Run anyway"。如果以后想消掉，买个 OV/EV 代码签名证书（约 ¥1500/年），在 CI 里签。

### Q: 用户已有 Python，能直接 `pip install`？
A: 能。`pip install modelbridge` 或者 `pipx install modelbridge`（推荐 pipx，自动隔离环境）。这俩路线和 setup.exe 不冲突，可以同时分发。pipx 路线不需要本目录这些脚本。

### Q: macOS 上 PyInstaller 报 "ModuleNotFoundError: No module named 'XXX'"？
A: 把缺的模块加到 `mbridge.spec` 的 `hiddenimports`。绝大多数情况是 lazy import（运行时才 import 的子模块），PyInstaller 静态分析看不到。

### Q: 用户配置文件去哪了？
A: 跟 dev 模式一样：`~/.modelbridge/`。`pyinstaller` 不动这个，`utils.get_app_dir()` 仍然按用户 HOME 解析。所以装上 / 卸载 / 升级都**不会丢用户的 models.yaml / config.yaml / budget.json**。

### Q: 卸载会删 `~/.modelbridge` 吗？
A: **不会**（installer.iss 里特意没写）。里面有用户的 API key + 预算记录，删了就找不回。用户想清理自己 `rmdir /s %USERPROFILE%\.modelbridge`。

### Q: 怎么改安装包图标？
A: 1) 准备一个 `.ico` 文件放 `packaging/icon.ico`；2) 在 `mbridge.spec` 里给 `EXE(...)` 加 `icon='packaging/icon.ico'`；3) 在 `installer.iss` 加 `SetupIconFile=icon.ico`。

### Q: 怎么改安装目录名？
A: `installer.iss` 顶部的 `#define MyAppName "ModelBridge"` —— Inno Setup 会把它用作 `DefaultDirName={autopf}\{#MyAppName}`。
