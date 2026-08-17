"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  PlugsConnected,
  Sparkle,
  ShieldCheck,
  Path,
  NotePencil,
  ArrowRight,
  Cube,
  Faders,
} from "@phosphor-icons/react";
import { api, ModelOut, SkillOut, DoctorCheck, ConfigOut } from "@/lib/api";

export default function HomePage() {
  const [models, setModels] = useState<ModelOut[]>([]);
  const [skills, setSkills] = useState<SkillOut[]>([]);
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [cfg, setCfg] = useState<ConfigOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api<ModelOut[]>("/models").then((r: any) => r.models),
      api<SkillOut[]>("/skills").then((r: any) => r.skills),
      api<{ checks: DoctorCheck[] }>("/doctor"),
      api<ConfigOut>("/config").catch(() => null),
    ])
      .then(([m, s, d, c]) => {
        setModels(m);
        setSkills(s);
        setChecks(d.checks);
        setCfg(c);
      })
      .finally(() => setLoading(false));
  }, []);

  const okCount = checks.filter((c) => c.ok).length;
  const missingKey = models.filter((m) => !m.has_api_key).length;
  const boundLevels = cfg
    ? Object.values(cfg.levels || {}).filter(Boolean).length
    : null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">概览</h1>
          <p className="page-sub">ModelBridge 本地配置一览</p>
        </div>
        <div className="toolbar">
          <Link href="/models" className="btn">
            <PlugsConnected size={14} weight="bold" /> 添加渠道
          </Link>
          <Link href="/doctor" className="btn btn-secondary">
            <ShieldCheck size={14} /> 运行自检
          </Link>
        </div>
      </div>

      {loading ? (
        <div className="card skeleton">
          <div className="sk-line sk-lg" />
          <div className="sk-line sk-md" />
        </div>
      ) : (
        <>
          <div className="home-metrics">
            <Link
              href="/models"
              className="metric"
              style={{ textDecoration: "none" }}
            >
              <span className="metric-label">
                <PlugsConnected size={14} /> 已配置模型
              </span>
              <span className="metric-value">{models.length}</span>
              <span className="metric-hint">
                {missingKey > 0
                  ? `${missingKey} 个缺 API Key`
                  : "全部 Key 就绪"}
              </span>
            </Link>
            <Link
              href="/skills"
              className="metric"
              style={{ textDecoration: "none" }}
            >
              <span className="metric-label">
                <Sparkle size={14} /> 可用 Skill
              </span>
              <span className="metric-value">{skills.length}</span>
              <span className="metric-hint">
                含内置技能，随包发布免确认
              </span>
            </Link>
            <Link
              href="/doctor"
              className="metric"
              style={{ textDecoration: "none" }}
            >
              <span className="metric-label">
                <ShieldCheck size={14} /> 自检通过
              </span>
              <span
                className={
                  okCount === checks.length ? "metric-value ok" : "metric-value err"
                }
              >
                {okCount}/{checks.length}
              </span>
              <span className="metric-hint">
                {okCount === checks.length
                  ? "环境就绪"
                  : "存在未通过项，点击查看"}
              </span>
            </Link>
            {cfg && (
              <Link
                href="/routing"
                className="metric"
                style={{ textDecoration: "none" }}
              >
                <span className="metric-label">
                  <Path size={14} /> 路由绑定
                </span>
                <span className="metric-value">{boundLevels ?? 0}/5</span>
                <span className="metric-hint">
                  {cfg.routing_mode} 模式 · 默认 {cfg.default_model || "未设置"}
                </span>
              </Link>
            )}
          </div>

          <div className="home-grid">
            <div className="card">
              <h2 className="card-title">
                <Faders size={14} className="icon" /> 路由现状
              </h2>
              {cfg ? (
                <div className="route-snapshot">
                  <div className="route-row">
                    <span className="muted">默认模型</span>
                    <span className="mono">
                      {cfg.default_model || (
                        <span className="muted">未设置</span>
                      )}
                    </span>
                  </div>
                  <div className="route-row">
                    <span className="muted">路由模式</span>
                    <span className="badge badge-accent">{cfg.routing_mode}</span>
                  </div>
                  {(
                    ["tiny", "cheap", "coder", "agent", "expert"] as const
                  ).map((lvl) => (
                    <div className="route-row" key={lvl}>
                      <span className="muted">{lvl}</span>
                      {cfg.levels?.[lvl] ? (
                        <span className="mono">{cfg.levels[lvl]}</span>
                      ) : (
                        <span className="muted">未绑定</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="muted">未能读取配置。</div>
              )}
            </div>

            <div className="card">
              <h2 className="card-title">
                <Cube size={14} className="icon" /> 快捷入口
              </h2>
              <div className="quick-links">
                <QuickLink
                  href="/models"
                  title="渠道 / 模型"
                  desc="增删改渠道，从目录一键添加"
                />
                <QuickLink
                  href="/routing"
                  title="路由配置"
                  desc="五级任务绑定对应模型"
                />
                <QuickLink
                  href="/prompts"
                  title="提示词"
                  desc="编辑 system.md / rules.md"
                />
                <QuickLink
                  href="/usage"
                  title="成本 / 缓存"
                  desc="命中统计与费用估算"
                />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function QuickLink({
  href,
  title,
  desc,
}: {
  href: string;
  title: string;
  desc: string;
}) {
  return (
    <Link href={href} className="quick-link">
      <div>
        <div className="quick-link-title">{title}</div>
        <div className="quick-link-desc">{desc}</div>
      </div>
      <ArrowRight size={15} className="quick-link-arrow" />
    </Link>
  );
}
