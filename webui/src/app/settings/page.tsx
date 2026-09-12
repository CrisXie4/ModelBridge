"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { api } from "@/lib/api";

// 与侧边栏 /ui 完全同一套旋钮（~/.modelbridge/ui.json）。
type FieldType = "number" | "bool" | "enum" | "color";

const FIELDS: {
  group?: string;
  key?: string;
  label?: string;
  type?: FieldType;
  min?: number;
  max?: number;
  step?: number;
  choices?: string[];
}[] = [
  { group: "布局" },
  { key: "rail_ratio", label: "右栏宽度比例", type: "number", min: 0.22, max: 0.55, step: 0.01 },
  { key: "rail_max", label: "右栏最大列数", type: "number", min: 40, max: 96, step: 1 },
  { key: "footer_hint", label: "底部按键提示行", type: "bool" },
  { group: "分组显示" },
  { key: "show_status", label: "状态区（目录/模型/上下文/缓存）", type: "bool" },
  { key: "show_tasks", label: "任务清单", type: "bool" },
  { key: "show_bg", label: "后台任务", type: "bool" },
  { key: "show_tools", label: "本轮工具调用", type: "bool" },
  { group: "对话流" },
  { key: "user_align", label: "用户消息对齐", type: "enum", choices: ["right", "left"] },
  { key: "stream_thinking", label: "流式显示思考内容", type: "bool" },
  { group: "配色" },
  { key: "color_accent", label: "强调色", type: "color" },
  { key: "color_good", label: "成功色", type: "color" },
  { key: "color_bad", label: "错误色", type: "color" },
  { key: "color_warn", label: "警示色", type: "color" },
  { key: "color_tool", label: "工具名颜色", type: "color" },
  { key: "color_dim", label: "次要文字灰", type: "color" },
];

// 与 agent/ui_prefs.py 的 UiPrefs 默认值保持一致（预览兜底用）。
const DEFAULTS: Record<string, unknown> = {
  rail_ratio: 0.4,
  rail_max: 72,
  footer_hint: true,
  show_status: true,
  show_tasks: true,
  show_bg: true,
  show_tools: true,
  user_align: "right",
  stream_thinking: true,
  color_accent: "#58a6ff",
  color_good: "#3fb950",
  color_bad: "#f85149",
  color_warn: "#d29922",
  color_tool: "#d2a8ff",
  color_dim: "#8a8f98",
};

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export default function SettingsPage() {
  const [prefs, setPrefs] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState("");
  const [msgKind, setMsgKind] = useState<"ok" | "err">("ok");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api<{ prefs: Record<string, unknown> }>("/ui-prefs")
      .then((r) => setPrefs(r.prefs))
      .catch((e) => {
        setMsgKind("err");
        setMsg(String(e.message || e));
      });
  }, []);

  const setValue = (key: string, v: unknown) => {
    setPrefs((p) => ({ ...(p || {}), [key]: v }));
    setDirty(true);
  };

  const save = async () => {
    try {
      const values: Record<string, unknown> = {};
      for (const f of FIELDS) {
        if (!f.group && f.key && prefs) values[f.key] = prefs[f.key];
      }
      const r = await api<{ saved: boolean; errors: Record<string, string>; prefs: Record<string, unknown> }>(
        "/ui-prefs",
        { method: "PUT", body: JSON.stringify({ values }) }
      );
      setPrefs(r.prefs);
      if (Object.keys(r.errors).length) {
        setMsgKind("err");
        setMsg("部分配置无效：" + Object.keys(r.errors).join(", "));
      } else {
        setMsgKind("ok");
        setMsg("已保存 ✓ 终端 TUI 与浏览器侧栏即时生效（下次渲染时热重载）");
        setDirty(false);
      }
    } catch (e) {
      setMsgKind("err");
      setMsg(String((e as Error).message || e));
    }
  };

  const reset = async () => {
    try {
      const r = await api<{ saved: boolean; errors: Record<string, string>; prefs: Record<string, unknown> }>(
        "/ui-prefs",
        { method: "PUT", body: JSON.stringify({ values: {}, reset: true }) }
      );
      setPrefs(r.prefs);
      setMsgKind("ok");
      setMsg("已恢复默认 ✓");
      setDirty(false);
    } catch (e) {
      setMsgKind("err");
      setMsg(String((e as Error).message || e));
    }
  };

  // 按 group 拆组
  const groups: { name: string; fields: (typeof FIELDS)[number][] }[] = [];
  for (const f of FIELDS) {
    if (f.group) groups.push({ name: f.group, fields: [] });
    else if (groups.length) groups[groups.length - 1].fields.push(f);
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">界面样式</h1>
          <p className="page-sub">
            配置终端 TUI（mbridge 全屏界面）的外观：右栏宽度、分组、配色。保存写入{" "}
            <code>~/.modelbridge/ui.json</code>，正在运行的终端与浏览器侧栏都会热重载。
          </p>
        </div>
      </div>

      {!prefs ? (
        msg ? (
          <div className="card">{msg}</div>
        ) : (
          <div className="card skeleton">
            <div className="sk-line sk-lg" />
            <div className="sk-line sk-md" />
          </div>
        )
      ) : (
        <div className="settings-layout">
          <div className="settings-left">
            {groups.map((g) => (
              <div className="card settings-card" key={g.name}>
                <div className="settings-group">{g.name}</div>
                {g.fields.map((f) => {
                  const key = f.key!;
                  const val = prefs[key];
                  return (
                    <div className="settings-row" key={key}>
                      <span className="settings-label">{f.label}</span>
                      {f.type === "bool" ? (
                        <label className="switch" title={f.label}>
                          <input
                            type="checkbox"
                            checked={Boolean(val)}
                            onChange={(e) => setValue(key, e.target.checked)}
                          />
                          <span className="switch-slider" />
                        </label>
                      ) : f.type === "enum" ? (
                        <select
                          value={String(val ?? "")}
                          onChange={(e) => setValue(key, e.target.value)}
                        >
                          {(f.choices || []).map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                      ) : f.type === "color" ? (
                        <ColorSwatch
                          value={
                            HEX_RE.test(String(val || "")) ? String(val) : "#000000"
                          }
                          onChange={(v) => setValue(key, v)}
                        />
                      ) : (
                        <input
                          type="number"
                          value={Number(val ?? 0)}
                          min={f.min}
                          max={f.max}
                          step={f.step}
                          onChange={(e) => setValue(key, Number(e.target.value))}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          <TuiPreview prefs={prefs} />

          <div className="settings-actions">
            <button className="btn" onClick={save} disabled={!dirty}>
              保存
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => {
                void reset();
              }}
            >
              恢复默认
            </button>
            <span className={msgKind === "err" ? "settings-msg err" : "settings-msg ok"}>{msg}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// 原生取色器藏在圆角色块下面，旁边展示 hex 值
function ColorSwatch({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <span className="color-swatch">
      <span className="color-chip" style={{ ["--swatch" as any]: value }} />
      <span className="color-hex">{value}</span>
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value.toLowerCase())}
        aria-label="选择颜色"
      />
    </span>
  );
}

// ---------------------------------------------------------------------------
// TuiPreview — 按 agent/tui.py 的右栏渲染规则模拟的实时预览。
// 颜色/开关/对齐绑定当前草稿值（未保存也生效）；右栏宽度按
// min(rail_max, 终端列数 × rail_ratio) 换算，模拟 110 列终端。
// ---------------------------------------------------------------------------

const TERM_COLS = 110;
const CELL_RATIO = 1 / TERM_COLS; // 1 列占屏宽比例

function num(prefs: Record<string, unknown>, key: string): number {
  const v = Number(prefs[key] ?? DEFAULTS[key]);
  return Number.isFinite(v) ? v : (DEFAULTS[key] as number);
}

function bool(prefs: Record<string, unknown>, key: string): boolean {
  if (prefs[key] === undefined) return DEFAULTS[key] as boolean;
  return Boolean(prefs[key]);
}

function color(prefs: Record<string, unknown>, key: string): string {
  const v = String(prefs[key] ?? "");
  return HEX_RE.test(v) ? v : (DEFAULTS[key] as string);
}

function TuiPreview({ prefs }: { prefs: Record<string, unknown> }) {
  const ratio = Math.min(0.55, Math.max(0.22, num(prefs, "rail_ratio")));
  const railMax = Math.round(
    Math.min(96, Math.max(40, num(prefs, "rail_max")))
  );
  const railCols = Math.min(railMax, Math.round(TERM_COLS * ratio));
  const alignRight = String(prefs.user_align ?? DEFAULTS.user_align) === "right";

  const style = {
    "--c-accent": color(prefs, "color_accent"),
    "--c-good": color(prefs, "color_good"),
    "--c-bad": color(prefs, "color_bad"),
    "--c-warn": color(prefs, "color_warn"),
    "--c-tool": color(prefs, "color_tool"),
    "--c-dim": color(prefs, "color_dim"),
  } as CSSProperties;

  const railStyle = { width: `${(railCols * CELL_RATIO * 100).toFixed(2)}%` } as CSSProperties;

  return (
    <div className="tui-preview">
      <div className="tui-frame">
        <div className="tui-titlebar">
          <span>mbridge — 全屏 TUI</span>
          <span>{TERM_COLS} 列模拟 · 右栏 {railCols} 列</span>
        </div>
        <div className="tui-screen" style={style}>
          <div className="tui-body">
            <div className="tui-transcript">
              <div className={`tui-user-line ${alignRight ? "is-right" : ""}`}>
                <span className="tui-user-bar">▎</span>
                <span>帮我修复缓存剪裁的越界问题</span>
              </div>
              {bool(prefs, "stream_thinking") && (
                <div className="tui-think">
                  思考中：先读 raw_logger 的剪裁条件，确认 checkpoint 边界…
                </div>
              )}
              <div className="tui-tool-line">
                <span className="mark" style={{ color: "var(--c-good)" }}>+</span>
                <span className="name">read</span>
                <span className="arg">modelbridge/raw_logger.py</span>
              </div>
              <div className="tui-tool-line">
                <span className="mark" style={{ color: "var(--c-bad)" }}>!</span>
                <span className="name">grep</span>
                <span className="arg">无匹配：checkpoint_ts</span>
              </div>
              <div className="tui-ai">
                找到问题了：<span className="hl">剪裁窗口</span>用了过期的 checkpoint
                行号，回滚时会把未持久化的行一起丢掉。修复方案分三步…
              </div>
            </div>

            <div className="tui-rail" style={railStyle}>
              {bool(prefs, "show_status") && (
                <>
                  <div className="tui-h">状态</div>
                  <div className="tui-rule" />
                  <div className="tui-kv">
                    <span className="k">目录</span>
                    <span className="v">D:\modelbridge</span>
                  </div>
                  <div className="tui-kv">
                    <span className="k">模型</span>
                    <span className="v" style={{ color: "var(--c-accent)", fontWeight: 600 }}>
                      deepseek-v4-pro
                    </span>
                  </div>
                  <div className="tui-kv">
                    <span className="k">上下文</span>
                    <span className="v" style={{ color: "var(--c-warn)" }}>68% 680k/1.0M</span>
                  </div>
                  <div className="tui-kv">
                    <span className="k">缓存</span>
                    <span className="v" style={{ color: "var(--c-good)" }}>95.5%</span>
                  </div>
                </>
              )}
              {bool(prefs, "show_tasks") && (
                <>
                  <div className="tui-h">任务</div>
                  <div className="tui-rule" />
                  <div className="tui-li" style={{ color: "var(--c-good)" }}>[x] 复现越界</div>
                  <div className="tui-li" style={{ color: "var(--c-accent)" }}>
                    {"[>]"} 修 checkpoint 行号
                  </div>
                  <div className="tui-li" style={{ color: "var(--c-dim)" }}>[ ] 回归 703 项测试</div>
                </>
              )}
              {bool(prefs, "show_bg") && (
                <>
                  <div className="tui-h">后台</div>
                  <div className="tui-rule" />
                  <div className="tui-li" style={{ color: "var(--c-good)" }}>✓ pytest tests/ 通过</div>
                  <div className="tui-li" style={{ color: "var(--c-dim)" }}>⟳ 文档索引构建中</div>
                </>
              )}
              {bool(prefs, "show_tools") && (
                <>
                  <div className="tui-h">本轮</div>
                  <div className="tui-rule" />
                  <div className="tui-li">
                    <span style={{ color: "var(--c-good)" }}>+ </span>
                    <span style={{ color: "var(--c-tool)", fontWeight: 600 }}>read</span>
                    <span style={{ color: "var(--c-dim)" }}> raw_logger.py</span>
                  </div>
                  <div className="tui-li">
                    <span style={{ color: "var(--c-good)" }}>+ </span>
                    <span style={{ color: "var(--c-tool)", fontWeight: 600 }}>str_replace</span>
                    <span style={{ color: "var(--c-dim)" }}> tui.py</span>
                  </div>
                </>
              )}
            </div>
          </div>
          {bool(prefs, "footer_hint") && (
            <div className="tui-foot">
              滚轮逐行 · PgUp/PgDn 翻页 · Ctrl-C 中断
            </div>
          )}
        </div>
      </div>
      <div className="tui-caption">
        实时预览（未保存也生效）。按 <span className="mono">{TERM_COLS}</span> 列终端模拟，
        右栏宽 = min(<span className="mono">{railMax}</span> 列上限, 全宽 ×{" "}
        <span className="mono">{ratio.toFixed(2)}</span>)。
      </div>
    </div>
  );
}
