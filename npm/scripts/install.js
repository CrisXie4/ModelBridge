"use strict";
/**
 * postinstall: download the matching self-contained mbridge binary for this
 * platform/arch from the GitHub Release and unpack it into ./vendor.
 *
 * The binary is a PyInstaller "onedir" bundle that ships its OWN Python
 * runtime, so the end user needs **no Python installed** — npm just delivers
 * the right prebuilt binary. The Python project itself is unchanged; this
 * folder is only a thin delivery shell.
 *
 * Env overrides (mostly for testing / mirrors):
 *   MBRIDGE_SKIP_DOWNLOAD=1     skip entirely (CI/dev where the binary isn't needed)
 *   MBRIDGE_DOWNLOAD_BASE=<url> override the release base url (custom mirror)
 *   MBRIDGE_FORCE_PLATFORM=...  override process.platform (win32|darwin|linux)
 *   MBRIDGE_FORCE_ARCH=...      override process.arch (x64|arm64)
 */

const fs = require("fs");
const path = require("path");
const https = require("https");
const crypto = require("crypto");
const { spawnSync } = require("child_process");

const pkg = require("../package.json");
const VERSION = pkg.version;
const REPO = "CrisXie4/ModelBridge";
const BASE =
  process.env.MBRIDGE_DOWNLOAD_BASE ||
  `https://github.com/${REPO}/releases/download/v${VERSION}`;

const ROOT = path.join(__dirname, "..");
const VENDOR = path.join(ROOT, "vendor");
const MARKER = path.join(VENDOR, ".mbridge-version");

// Platform/arch combos we actually build in release.yml. Others get a clear
// "use pip instead" message rather than a confusing 404.
const SUPPORTED = new Set(["windows-x86_64", "macos-arm64", "linux-x86_64"]);

function targetTriple() {
  const platform = process.env.MBRIDGE_FORCE_PLATFORM || process.platform;
  const arch = process.env.MBRIDGE_FORCE_ARCH || process.arch;

  let osName;
  if (platform === "win32") osName = "windows";
  else if (platform === "darwin") osName = "macos";
  else if (platform === "linux") osName = "linux";
  else throw new Error(`unsupported platform: ${platform}`);

  let archName;
  if (arch === "x64") archName = "x86_64";
  else if (arch === "arm64") archName = osName === "linux" ? "aarch64" : "arm64";
  else throw new Error(`unsupported arch: ${arch}`);

  return { osName, archName, platform };
}

function httpGet(url, { redirects = 6, text = false } = {}) {
  return new Promise((resolve, reject) => {
    const go = (u, left) => {
      const req = https.get(
        u,
        { headers: { "User-Agent": "mbridge-npm-installer", Accept: "application/octet-stream" } },
        (res) => {
          if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
            res.resume();
            if (left <= 0) return reject(new Error("too many redirects"));
            return go(new URL(res.headers.location, u).toString(), left - 1);
          }
          if (res.statusCode !== 200) {
            res.resume();
            return reject(Object.assign(new Error(`HTTP ${res.statusCode} for ${u}`), {
              statusCode: res.statusCode,
            }));
          }
          resolve(res);
        }
      );
      req.on("error", reject);
    };
    go(url, redirects);
  }).then((res) => {
    if (!text) return res; // caller pipes it
    return new Promise((resolve, reject) => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (data += c));
      res.on("end", () => resolve(data));
      res.on("error", reject);
    });
  });
}

function downloadToFile(url, dest) {
  return httpGet(url).then(
    (res) =>
      new Promise((resolve, reject) => {
        const out = fs.createWriteStream(dest);
        res.pipe(out);
        out.on("finish", () => out.close(() => resolve()));
        out.on("error", reject);
        res.on("error", reject);
      })
  );
}

function sha256File(p) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash("sha256");
    const s = fs.createReadStream(p);
    s.on("data", (d) => h.update(d));
    s.on("end", () => resolve(h.digest("hex")));
    s.on("error", reject);
  });
}

function parseSum(text, name) {
  for (const line of text.split(/\r?\n/)) {
    const m = line.trim().match(/^([0-9a-fA-F]{64})\s+\*?(.+)$/);
    if (m && path.basename(m[2].trim()) === name) return m[1].toLowerCase();
  }
  return null;
}

function readMarker() {
  try {
    return fs.readFileSync(MARKER, "utf8").trim();
  } catch {
    return null;
  }
}

async function main() {
  if (process.env.MBRIDGE_SKIP_DOWNLOAD === "1") {
    console.log("[mbridge] MBRIDGE_SKIP_DOWNLOAD=1 → 跳过二进制下载。");
    return;
  }

  const { osName, archName, platform } = targetTriple();
  const triple = `${osName}-${archName}`;
  const binName = platform === "win32" ? "mbridge.exe" : "mbridge";
  const binAbs = path.join(VENDOR, "mbridge", binName);

  if (!SUPPORTED.has(triple)) {
    throw new Error(
      `没有为你的平台预编译的二进制：${triple}。\n` +
        `npm 包目前支持：windows-x86_64 / macos-arm64 / linux-x86_64。\n` +
        `其它平台请改用： pipx install modelbridge （需要 Python 3.10+），\n` +
        `或见 https://github.com/${REPO}/releases`
    );
  }

  if (fs.existsSync(binAbs) && readMarker() === VERSION) {
    console.log(`[mbridge] 已安装 v${VERSION}，跳过下载。`);
    return;
  }

  fs.mkdirSync(VENDOR, { recursive: true });

  const assetName = `mbridge-${VERSION}-${triple}.tar.gz`;
  const url = `${BASE}/${assetName}`;
  // Download into VENDOR with a colon-free relative name. tar is then invoked
  // with cwd=VENDOR and a relative archive name, so a drive-letter path
  // (C:\...) never reaches tar — GNU tar would otherwise read "C:" as a
  // remote host ("Cannot connect to C:"); bsdtar is fine either way.
  const dlName = "_download.tar.gz";
  const tmp = path.join(VENDOR, dlName);

  console.log(`[mbridge] 下载二进制：${url}`);
  try {
    await downloadToFile(url, tmp);
  } catch (e) {
    if (e.statusCode === 404) {
      throw new Error(
        `release v${VERSION} 里没有 ${assetName}。\n` +
          `如果这是一个早于 npm 支持的旧版本，请安装较新的版本，或见\n` +
          `https://github.com/${REPO}/releases`
      );
    }
    throw e;
  }

  // Best-effort checksum verification (SHA256SUMS.txt is attached to releases
  // built after the npm integration landed; older releases simply skip it).
  try {
    const sums = await httpGet(`${BASE}/SHA256SUMS.txt`, { text: true });
    const want = parseSum(sums, assetName);
    if (want) {
      const got = (await sha256File(tmp)).toLowerCase();
      if (got !== want) {
        try { fs.unlinkSync(tmp); } catch {}
        throw new Error(`SHA256 校验失败：期望 ${want}，实际 ${got}（已删除下载文件）`);
      }
      console.log("[mbridge] SHA256 校验通过。");
    }
  } catch (e) {
    if (e.statusCode === 404) {
      console.warn("[mbridge] 该 release 未提供 SHA256SUMS.txt，跳过校验。");
    } else if (/SHA256 校验失败/.test(e.message)) {
      throw e;
    } else {
      console.warn(`[mbridge] 校验步骤出错（忽略，继续安装）：${e.message}`);
    }
  }

  // Clean a previous extraction, then unpack via tar (present on Windows 10
  // 1803+, macOS and Linux). Run with cwd=VENDOR and a relative archive name
  // so no absolute drive-letter path is passed to tar.
  try {
    fs.rmSync(path.join(VENDOR, "mbridge"), { recursive: true, force: true });
  } catch {}
  const r = spawnSync("tar", ["-xzf", dlName], { cwd: VENDOR, stdio: "inherit" });
  try { fs.unlinkSync(tmp); } catch {}
  if (r.error || r.status !== 0) {
    throw new Error(
      `解压失败（需要 tar；Windows 10 1803+ 自带）：` +
        (r.error ? r.error.message : `tar 退出码 ${r.status}`)
    );
  }
  if (!fs.existsSync(binAbs)) {
    throw new Error(`解压后未找到二进制：${binAbs}`);
  }
  if (platform !== "win32") {
    try { fs.chmodSync(binAbs, 0o755); } catch {}
  }
  try { fs.writeFileSync(MARKER, VERSION); } catch {}
  console.log(`[mbridge] 安装完成 → ${binAbs}`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`[mbridge] 安装失败：${err.message}`);
    process.exit(1);
  });
}

module.exports = { main, targetTriple };
