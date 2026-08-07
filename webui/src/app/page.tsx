"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ModelOut, SkillOut, DoctorCheck } from "@/lib/api";

export default function HomePage() {
  const [models, setModels] = useState<ModelOut[]>([]);
  const [skills, setSkills] = useState<SkillOut[]>([]);
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api<ModelOut[]>("/models").then((r: any) => r.models),
      api<SkillOut[]>("/skills").then((r: any) => r.skills),
      api<{ checks: DoctorCheck[] }>("/doctor"),
    ])
      .then(([m, s, d]) => {
        setModels(m);
        setSkills(s);
        setChecks(d.checks);
      })
      .finally(() => setLoading(false));
  }, []);

  const okCount = checks.filter((c) => c.ok).length;
  const builtinSkills = skills.filter((s) => s.scope === "builtin").length;

  return (
    <div>
      <h1 className="page-title">概览</h1>
      <p className="page-sub">ModelBridge 本地配置一览</p>

      {loading ? (
        <div className="card">加载中…</div>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 16,
              marginBottom: 24,
            }}
          >
            <StatCard label="已配置模型" value={models.length} href="/models" />
            <StatCard label="可用 Skill" value={skills.length} href="/skills" />
            <StatCard
              label="内置 Skill"
              value={builtinSkills}
              href="/skills"
            />
            <StatCard
              label="自检通过"
              value={`${okCount}/${checks.length}`}
              href="/doctor"
              ok={okCount === checks.length}
            />
          </div>

          <div className="card">
            <h2 className="card-title">快捷操作</h2>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <Link href="/models" className="btn">
                + 添加渠道
              </Link>
              <Link href="/routing" className="btn btn-secondary">
                配置路由
              </Link>
              <Link href="/prompts" className="btn btn-secondary">
                编辑提示词
              </Link>
              <Link href="/doctor" className="btn btn-secondary">
                运行自检
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  href,
  ok,
}: {
  label: string;
  value: string | number;
  href: string;
  ok?: boolean;
}) {
  return (
    <Link href={href} style={{ textDecoration: "none", color: "inherit" }}>
      <div className="card" style={{ marginBottom: 0, cursor: "pointer" }}>
        <div style={{ color: "var(--mb-muted)", fontSize: 12 }}>{label}</div>
        <div
          style={{
            fontSize: 28,
            fontWeight: 700,
            marginTop: 6,
            color: ok === undefined ? "var(--mb-text)" : ok ? "#3fb950" : "#e5484d",
          }}
        >
          {value}
        </div>
      </div>
    </Link>
  );
}
