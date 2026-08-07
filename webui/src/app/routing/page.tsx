"use client";

import { useEffect, useState } from "react";
import { api, ConfigOut, ModelOut } from "@/lib/api";

const LEVELS = ["tiny", "cheap", "coder", "agent", "expert"] as const;
const LEVEL_DESC: Record<string, string> = {
  tiny: "意图分类 / 是非判断",
  cheap: "普通问答 / 解释",
  coder: "单文件代码生成",
  agent: "多文件任务 / 工具",
  expert: "架构重构 / 安全审查",
};

export default function RoutingPage() {
  const [cfg, setCfg] = useState<ConfigOut | null>(null);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    Promise.all([
      api<ConfigOut>("/config"),
      api<{ models: ModelOut[] }>("/models"),
    ]).then(([c, m]) => {
      setCfg(c);
      setModels(m.models);
    });
  }, []);

  function flash(msg: string, ok = true) {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 2500);
  }

  async function save() {
    if (!cfg) return;
    setSaving(true);
    try {
      await api("/config", {
        method: "PUT",
        body: JSON.stringify(cfg),
      });
      flash("路由配置已保存");
    } catch (e: any) {
      flash(e.message, false);
    } finally {
      setSaving(false);
    }
  }

  if (!cfg) return <div className="card">加载中…</div>;
  const names = models.map((m) => m.name);

  return (
    <div>
      <h1 className="page-title">路由配置</h1>
      <p className="page-sub">
        按任务等级绑定模型。简单任务用便宜模型，复杂任务升级到强模型。
      </p>

      <div className="card">
        <div className="field">
          <label>路由模式</label>
          <select
            value={cfg.routing_mode}
            onChange={(e) => setCfg({ ...cfg, routing_mode: e.target.value })}
          >
            <option value="economy">economy（省钱优先）</option>
            <option value="balanced">balanced（默认·推荐）</option>
            <option value="powerful">powerful（少考虑成本）</option>
          </select>
        </div>

        <div className="field">
          <label>默认模型</label>
          <select
            value={cfg.default_model || ""}
            onChange={(e) =>
              setCfg({ ...cfg, default_model: e.target.value || null })
            }
          >
            <option value="">（未设置）</option>
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        <h2 className="card-title">五级模型绑定</h2>
        {LEVELS.map((lvl) => (
          <div className="field" key={lvl}>
            <label>
              <span className="badge badge-accent">{lvl}</span>{" "}
              {LEVEL_DESC[lvl]}
            </label>
            <select
              value={cfg.levels[lvl] || ""}
              onChange={(e) =>
                setCfg({
                  ...cfg,
                  levels: { ...cfg.levels, [lvl]: e.target.value || null },
                })
              }
            >
              <option value="">（未绑定）</option>
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <button className="btn" onClick={save} disabled={saving}>
          {saving ? "保存中…" : "保存配置"}
        </button>
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
