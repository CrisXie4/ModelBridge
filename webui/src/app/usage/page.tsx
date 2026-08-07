"use client";

import { useEffect, useState } from "react";
import { api, CacheStats, ModelOut } from "@/lib/api";

export default function UsagePage() {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [costModel, setCostModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cost, setCost] = useState<any>(null);
  const [estimating, setEstimating] = useState(false);

  useEffect(() => {
    Promise.all([
      api<CacheStats>("/usage/cache"),
      api<{ models: ModelOut[] }>("/models"),
    ]).then(([s, m]) => {
      setStats(s);
      setModels(m.models);
      if (m.models.length) setCostModel(m.models[0].name);
    });
  }, []);

  async function estimate() {
    setEstimating(true);
    try {
      const r = await api("/usage/cost", {
        method: "POST",
        body: JSON.stringify({ model: costModel, prompt }),
      });
      setCost(r);
    } finally {
      setEstimating(false);
    }
  }

  return (
    <div>
      <h1 className="page-title">成本 / 缓存</h1>
      <p className="page-sub">缓存命中统计 + 单次调用成本估算。</p>

      <div className="card">
        <h2 className="card-title">缓存命中</h2>
        {stats ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3,1fr)",
              gap: 16,
            }}
          >
            <Metric label="命中率" value={`${(stats.hit_rate * 100).toFixed(1)}%`} />
            <Metric label="命中" value={stats.hits} />
            <Metric label="未命中" value={stats.misses} />
            <Metric label="节省 token" value={stats.saved_tokens} />
            <Metric
              label="节省费用"
              value={`${stats.estimated_savings.toFixed(4)} ${stats.currency}`}
            />
            <Metric
              label="前缀稳定度"
              value={`${(stats.prefix_stability * 100).toFixed(1)}%`}
            />
          </div>
        ) : (
          <div>加载中…</div>
        )}
      </div>

      <div className="card">
        <h2 className="card-title">单次成本估算</h2>
        <div className="field">
          <label>模型</label>
          <select
            value={costModel}
            onChange={(e) => setCostModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Prompt 文本</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="粘贴要估算的 prompt…"
            style={{ minHeight: 120 }}
          />
        </div>
        <button className="btn" onClick={estimate} disabled={estimating}>
          {estimating ? "估算中…" : "估算成本"}
        </button>

        {cost && (
          <div style={{ marginTop: 16 }}>
            {cost.ok === false ? (
              <div style={{ color: "#e5484d" }}>{cost.error}</div>
            ) : (
              <table>
                <tbody>
                  <tr>
                    <td>输入 token</td>
                    <td className="mono">{cost.input_tokens}</td>
                  </tr>
                  <tr>
                    <td>输出 token（估算）</td>
                    <td className="mono">{cost.output_tokens}</td>
                  </tr>
                  <tr>
                    <td>预计费用</td>
                    <td className="mono" style={{ fontWeight: 700 }}>
                      {cost.estimated_cost.toFixed(6)} {cost.currency}
                    </td>
                  </tr>
                  <tr>
                    <td>单价（每 1M）</td>
                    <td className="mono" style={{ color: "var(--mb-muted)" }}>
                      {cost.input_per_1m} / {cost.output_per_1m}
                    </td>
                  </tr>
                  <tr>
                    <td>来源</td>
                    <td className="mono" style={{ color: "var(--mb-muted)" }}>
                      {cost.pricing_source}
                    </td>
                  </tr>
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ color: "var(--mb-muted)", fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </div>
  );
}
