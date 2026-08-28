"use client";

import { useEffect, useState } from "react";
import { Sparkle, X, Cube } from "@phosphor-icons/react";
import { api, SkillOut } from "@/lib/api";

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillOut[]>([]);
  const [active, setActive] = useState<SkillOut | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    api<{ skills: SkillOut[] }>("/skills")
      .then((r) => setSkills(r.skills))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  // 启停：写入 ~/.modelbridge/skills.json，下一次启动的 REPL 生效。
  const toggle = (name: string, enabled: boolean) => {
    api(`/skills/${name}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    })
      .then(() => load())
      .catch((e) => alert(String((e as Error).message || e)));
  };

  const scopeBadge = (scope: string) => {
    if (scope === "builtin") return <span className="badge badge-accent">内置</span>;
    if (scope === "project") return <span className="badge badge-ok">项目</span>;
    return <span className="badge badge-muted">全局</span>;
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Skills</h1>
          <p className="page-sub">
            AI 会话中可加载的技能指令。内置 skill 随包发布、免确认；用户 skill 可用{" "}
            <code>mbridge skill add</code> 安装。
          </p>
        </div>
      </div>

      {loading ? (
        <div className="card skeleton">
          <div className="sk-line sk-lg" />
          <div className="sk-line sk-md" />
        </div>
      ) : skills.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">
              <Sparkle size={20} />
            </div>
            <div className="empty-title">还没有安装任何 Skill</div>
            <div className="empty-hint">
              运行 <code>mbridge skill add &lt;目录&gt;</code> 把本地技能文件夹安装到全局。
            </div>
          </div>
        </div>
      ) : (
        <div className="skill-grid">
          {skills.map((s) => {
            const off = s.enabled === false;
            return (
              <div
                key={`${s.scope}/${s.name}`}
                className={`skill-card skill-toggle-card ${off ? "is-disabled" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() =>
                  api<SkillOut>(`/skills/${s.name}`).then(setActive)
                }
              >
                <div className="skill-icon">
                  <Cube size={17} />
                </div>
                <div className="skill-info">
                  <div className="skill-name-row">
                    <span className="mono skill-name">{s.name}</span>
                    {scopeBadge(s.scope)}
                  </div>
                  <div className="skill-desc">{s.description}</div>
                </div>
                <label
                  className="switch"
                  title={off ? "已禁用 — 点击启用" : "启用中 — 点击禁用"}
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    checked={!off}
                    onChange={(e) => toggle(s.name, e.target.checked)}
                  />
                  <span className="switch-slider" />
                </label>
              </div>
            );
          })}
        </div>
      )}

      {active && (
        <div className="modal-overlay" onClick={() => setActive(null)}>
          <div
            className="modal"
            style={{ width: 680, padding: 22 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2 className="modal-title mono">{active.name}</h2>
              <span className="badge badge-muted">{active.scope}</span>
              <div style={{ flex: 1 }} />
              <button
                className="btn btn-sm btn-ghost modal-close"
                onClick={() => setActive(null)}
              >
                <X size={15} />
              </button>
            </div>
            <p className="muted" style={{ margin: "0 0 14px", fontSize: 13 }}>
              {active.description}
            </p>
            <pre className="skill-body mono">{active.body}</pre>
            <div className="mono muted" style={{ fontSize: 11, marginTop: 10 }}>
              {active.path}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
