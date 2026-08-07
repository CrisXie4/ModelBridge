"use client";

import { useEffect, useState } from "react";
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

  return (
    <div>
      <h1 className="page-title">自检 / 健康状态</h1>
      <p className="page-sub">
        环境检查 + 单模型连通性测试（会发起真实 API 调用）。
      </p>

      <div className="card">
        <h2 className="card-title">环境检查</h2>
        {loading ? (
          <div>加载中…</div>
        ) : (
          <table>
            <tbody>
              {checks.map((c) => (
                <tr key={c.name}>
                  <td className="mono">{c.name}</td>
                  <td style={{ width: 60 }}>
                    {c.ok ? (
                      <span className="badge badge-ok">OK</span>
                    ) : (
                      <span className="badge badge-err">FAIL</span>
                    )}
                  </td>
                  <td style={{ color: "var(--mb-muted)" }}>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2 className="card-title">模型连通性</h2>
        {models.length === 0 ? (
          <div style={{ color: "var(--mb-muted)" }}>未配置模型。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>模型</th>
                <th>状态</th>
                <th>延迟</th>
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
                    <td className="mono">{m.name}</td>
                    <td>
                      {r?.loading ? (
                        <span className="badge badge-muted">测试中…</span>
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
                    <td className="mono">
                      {r?.chat_latency_ms != null
                        ? `${r.chat_latency_ms}ms`
                        : "-"}
                    </td>
                    <td>
                      {r?.has_reasoning ? "✓" : r?.status ? "—" : ""}
                    </td>
                    <td>{r?.json_ok ? "✓" : r?.json_ok === false ? "✗" : ""}</td>
                    <td>{r?.tools_ok ? "✓" : r?.tools_ok === false ? "✗" : ""}</td>
                    <td>
                      <button
                        className="btn btn-sm btn-secondary"
                        disabled={testing === m.name}
                        onClick={() => testModel(m.name)}
                      >
                        {testing === m.name ? "…" : "测试"}
                      </button>
                      {r?.error && (
                        <div
                          style={{
                            color: "#e5484d",
                            fontSize: 11,
                            marginTop: 4,
                          }}
                        >
                          {r.error}
                        </div>
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
