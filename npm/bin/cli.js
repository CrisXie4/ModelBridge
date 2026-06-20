#!/usr/bin/env node
"use strict";
/**
 * Thin launcher: forward `mbridge <args>` to the downloaded self-contained
 * binary under ./vendor. No Python involved — the binary bundles its own.
 */

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const binName = process.platform === "win32" ? "mbridge.exe" : "mbridge";
const binary = path.join(ROOT, "vendor", "mbridge", binName);

function ensureInstalled() {
  if (fs.existsSync(binary)) return true;
  // The postinstall step may have been skipped (e.g. `npm i --ignore-scripts`).
  // Bootstrap on first run instead of failing.
  process.stderr.write("[mbridge] 首次运行，正在下载二进制…\n");
  const r = spawnSync(process.execPath, [path.join(ROOT, "scripts", "install.js")], {
    stdio: "inherit",
  });
  return r.status === 0 && fs.existsSync(binary);
}

if (!ensureInstalled()) {
  console.error(
    "[mbridge] 二进制未安装。请重装：npm i -g mbridge\n" +
      "（若用了 --ignore-scripts，请手动运行： node <安装目录>/scripts/install.js）"
  );
  process.exit(1);
}

const res = spawnSync(binary, process.argv.slice(2), { stdio: "inherit" });
if (res.error) {
  console.error(`[mbridge] 无法启动二进制：${res.error.message}`);
  process.exit(1);
}
// Mirror the child's exit code (signal → conventional 1).
process.exit(res.status === null ? 1 : res.status);
