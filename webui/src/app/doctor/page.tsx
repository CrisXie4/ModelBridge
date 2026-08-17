"use client";

import { useEffect, useState } from "react";
import {
  CheckCircle,
  XCircle,
  ShieldCheck,
  Play,
  CircleNotch,
  TerminalWindow,
} from "@phosphor-icons/react";
import { api, DoctorCheck, ModelOut } from "@/lib/api";

export default function DoctorPage() {
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, any>>({});

  useEffect(() => {
    Promise.all([
      api<{ checks: DoctorCheck[] }>("/doctor"),
      api<{ models: ModelOut[] }>("/models"),
    ])
      .then(([d, m]) => {
        setChecks(d.checks);
        setModels(m.models);
      })
      .finally(() => setLoading(false));
  }, []);

  async function testModel(name: string) {
    setTesting(name);
    setResult({ ...result, [name]: { loading: true } });
    try {
      const r = await api(`/doctor/${name}`, { method: "POST" });
      setResult({ ...result, [name]: r });
    } catch (e: any) {
      setResult({ ...result, [name]: { error: e.message } });
    } finally {
      setTesting(null);
    }
  }

  async function testAll() {
    for (const m of models) {
      // eslint-disable-next-line no-await-in-loop
      await testModel(m.name);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">自检 / 健康状态</h1>
          <p className="page-sub">
            环境检查 + 单模型连通性测试（会发起真实 API 调用）。
          </p>
        </div>
        {models.length > 0 && (
          <button
            className="btn btn-secondary"
            onClick={testAll}
            disabled={!!testing}
          >
            <Play size={13} /> 测试全部模型
          </button>
        )}
      </div>

      <div className="card">
        <h2 className="card-title">
          <ShieldCheck size={14} className="icon" /> 环境检查
        </h2>
        {loading ? (
          <div className="skeleton">
            <div className="sk-line sk-md" />
            <div className="sk-line sk-sm" />
          </div>
        ) : (
          <div className="check-list">
            {checks.map((c) => (
              <div className="check-row" key={c.name}>
                {c.ok ? (
                  <CheckCircle size={16} weight="fill" className="check-ok" />
                ) : (
                  <XCircle size={16} weight="fill" className="check-err" />
                )}
                <span className="mono check-name">{c.name}</span>
                <span className="check-detail">{c.detail}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h2 className="card-title">
          <TerminalWindow size={14} className="icon" /> 模型连通性
        </h2>
        {models.length === 0 ? (
          <div className="empty">
            <div className="empty-icon">
              <TerminalWindow size={20} />
            </div>
            <div className="empty-title">未配置模型</div>
            <div className="empty-hint">
              先到「渠道 / 模型」页添加至少一个渠道，再回来测试连通性。
            </div>
          </div>
        ) : (
          <table style={{ marginTop: -6 }}>
            <thead>
              <tr>
                <th>模型</th>
                <th>状态</th>
                <th className="num">延迟</th>
                <th>推理</th>
                <th>JSON</th>
                <th>工具</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const r = result[m.name];
                return (
                  <tr key={m.name}>
                    <td className="mono" style={{ fontWeight: 600 }}>
                      {m.name}
                    </td>
                    <td>
                      {r?.loading ? (
                        <span className="badge badge-warn">测试中…</span>
                      ) : r?.status ? (
                        <span
                          className={
                            r.chat_ok ? "badge badge-ok" : "badge badge-err"
                          }
                        >
                          {r.status}
                        </span>
                      ) : (
                        <span className="badge badge-muted">未测</span>
                      )}
                    </td>
                    <td className="mono num">
                      {r?.chat_latency_ms != null ? (
                        <span
                          style={{
                            color:
                              r.chat_latency_ms > 3000
                                ? "var(--mb-warn)"
                                : undefined,
                          }}
                        >
                          {r.chat_latency_ms}ms
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{r?.has_reasoning ? "✓" : r?.status ? "—" : ""}</td>
                    <td>{r?.json_ok ? "✓" : r?.json_ok === false ? "✗" : ""}</td>
                    <td>{r?.tools_ok ? "✓" : r?.tools_ok === false ? "✗" : ""}</td>
                    <td>
                      <button
                        className="btn btn-sm btn-secondary"
                        disabled={testing === m.name}
                        onClick={() => testModel(m.name)}
                      >
                        {testing === m.name ? (
                          <CircleNotch size={12} className="spin" />
                        ) : (
                          "测试"
                        )}
                      </button>
                      {r?.error && (
                        <div className="test-error">{r.error}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
