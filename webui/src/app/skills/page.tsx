"use client";

import { useEffect, useState } from "react";
import { api, SkillOut } from "@/lib/api";

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillOut[]>([]);
  const [active, setActive] = useState<SkillOut | null>(null);

  useEffect(() => {
    api<{ skills: SkillOut[] }>("/skills").then((r) => setSkills(r.skills));
  }, []);

  const scopeBadge = (scope: string) => {
    if (scope === "builtin")
      return <span className="badge badge-accent">内置·免确认</span>;
    if (scope === "project")
      return <span className="badge badge-ok">项目</span>;
    return <span className="badge badge-muted">全局</span>;
  };

  return (
    <div>
      <h1 className="page-title">Skills</h1>
      <p className="page-sub">
        AI 会话中可加载的技能指令。内置 skill 随包发布、免确认；用户 skill
        可用 <code className="mono">mbridge skill add</code> 安装。
      </p>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>作用域</th>
              <th>描述</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={`${s.scope}/${s.name}`}>
                <td className="mono" style={{ fontWeight: 600 }}>
                  {s.name}
                </td>
                <td>{scopeBadge(s.scope)}</td>
                <td>{s.description}</td>
                <td>
                  <button
                    className="btn btn-sm btn-secondary"
                    onClick={() =>
                      api<SkillOut>(`/skills/${s.name}`).then(setActive)
                    }
                  >
                    查看
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
          }}
          onClick={() => setActive(null)}
        >
          <div
            className="card"
            style={{ width: 640, maxHeight: "80vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="card-title">
              {active.name}{" "}
              <span style={{ fontSize: 12, color: "var(--mb-muted)" }}>
                [{active.scope}]
              </span>
            </h2>
            <p style={{ color: "var(--mb-muted)", marginBottom: 12 }}>
              {active.description}
            </p>
            <pre
              className="mono"
              style={{
                whiteSpace: "pre-wrap",
                background: "var(--mb-bg)",
                padding: 16,
                borderRadius: 8,
                border: "1px solid var(--mb-border)",
                lineHeight: 1.6,
              }}
            >
              {active.body}
            </pre>
            <button
              className="btn btn-secondary"
              style={{ marginTop: 12 }}
              onClick={() => setActive(null)}
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
