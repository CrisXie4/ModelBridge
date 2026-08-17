"use client";

import { useEffect, useState } from "react";
import {
  FloppyDisk,
  CircleNotch,
  Bird,
  ChatCircleDots,
  Code,
  Robot,
  Brain,
  Lightning,
  CrownSimple,
} from "@phosphor-icons/react";
import { api, ConfigOut, ModelOut } from "@/lib/api";

const LEVELS = ["tiny", "cheap", "coder", "agent", "expert"] as const;

const LEVEL_META: Record<
  string,
  { desc: string; Icon: typeof Bird }
> = {
  tiny: { desc: "意图分类 / 是非判断", Icon: Bird },
  cheap: { desc: "普通问答 / 解释", Icon: ChatCircleDots },
  coder: { desc: "单文件代码生成", Icon: Code },
  agent: { desc: "多文件任务 / 工具", Icon: Robot },
  expert: { desc: "架构重构 / 安全审查", Icon: Brain },
};

const MODE_DESC: Record<string, string> = {
  economy: "省钱优先，能用小模型就不升级",
  balanced: "默认推荐，成本与能力平衡",
  powerful: "少考虑成本，优先强模型",
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

  if (!cfg)
    return (
      <div className="page">
        <div className="card skeleton">
          <div className="sk-line sk-lg" />
          <div className="sk-line sk-md" />
          <div className="sk-line sk-sm" />
        </div>
      </div>
    );
  const names = models.map((m) => m.name);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">路由配置</h1>
          <p className="page-sub">
            按任务等级绑定模型。简单任务用便宜模型，复杂任务升级到强模型。
          </p>
        </div>
        <button className="btn" onClick={save} disabled={saving}>
          {saving ? (
            <>
              <CircleNotch size={14} className="spin" /> 保存中…
            </>
          ) : (
            <>
              <FloppyDisk size={14} /> 保存配置
            </>
          )}
        </button>
      </div>

      <div className="card">
        <h2 className="card-title">
          <Lightning size={14} className="icon" /> 路由模式
        </h2>
        <div className="mode-cards">
          {(["economy", "balanced", "powerful"] as const).map((mode) => (
            <button
              key={mode}
              className={
                cfg.routing_mode === mode ? "mode-card on" : "mode-card"
              }
              onClick={() => setCfg({ ...cfg, routing_mode: mode })}
            >
              <span className="mode-name">{mode}</span>
              <span className="mode-desc">{MODE_DESC[mode]}</span>
              {mode === "balanced" && (
                <span className="badge badge-accent mode-rec">推荐</span>
              )}
            </button>
          ))}
        </div>

        <div className="field" style={{ marginBottom: 0, maxWidth: 420 }}>
          <label>默认模型（未命中任何等级时的兜底）</label>
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
        <h2 className="card-title">
          <CrownSimple size={14} className="icon" /> 五级模型绑定
        </h2>
        <div className="level-list">
          {LEVELS.map((lvl) => {
            const { desc, Icon } = LEVEL_META[lvl];
            return (
              <div className="level-row" key={lvl}>
                <div className="level-icon">
                  <Icon size={18} />
                </div>
                <div className="level-info">
                  <span className="level-name mono">{lvl}</span>
                  <span className="level-desc">{desc}</span>
                </div>
                <div className="level-bind">
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
              </div>
            );
          })}
        </div>
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
