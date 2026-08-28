"use client";

import { useEffect, useState } from "react";
import { Palette } from "@phosphor-icons/react";
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
        <div className="page-actions">
          <Palette size={22} />
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
        <>
          <div className="card settings-card">
            {FIELDS.map((f, i) => {
              if (f.group)
                return (
                  <div className="settings-group" key={`g${i}`}>
                    <span>{f.group}</span>
                  </div>
                );
              const key = f.key!;
              const val = prefs[key];
              return (
                <label className="settings-row" key={key}>
                  <span className="settings-label">{f.label}</span>
                  {f.type === "bool" ? (
                    <input
                      type="checkbox"
                      checked={Boolean(val)}
                      onChange={(e) => setValue(key, e.target.checked)}
                    />
                  ) : f.type === "enum" ? (
                    <select value={String(val ?? "")} onChange={(e) => setValue(key, e.target.value)}>
                      {(f.choices || []).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : f.type === "color" ? (
                    <input
                      type="color"
                      value={/^#[0-9a-fA-F]{6}$/.test(String(val || "")) ? String(val) : "#000000"}
                      onChange={(e) => setValue(key, e.target.value.toLowerCase())}
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
                </label>
              );
            })}
          </div>

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
        </>
      )}
    </div>
  );
}
