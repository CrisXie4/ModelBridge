"use client";

import { useEffect, useState } from "react";
import {
  Coins,
  ChartLine,
  Database,
  Calculator,
  CircleNotch,
} from "@phosphor-icons/react";
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
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">成本 / 缓存</h1>
          <p className="page-sub">缓存命中统计 + 单次调用成本估算。</p>
        </div>
      </div>

      <div className="card">
        <h2 className="card-title">
          <Database size={14} className="icon" /> 缓存命中
        </h2>
        {stats ? (
          <div className="usage-metrics">
            <div className="usage-hero">
              <span className="metric-label">
                <ChartLine size={14} /> 命中率
              </span>
              <span className="usage-hero-value">
                {(stats.hit_rate * 100).toFixed(1)}
                <small>%</small>
              </span>
            </div>
            <div className="usage-grid">
              <Metric label="命中" value={stats.hits} />
              <Metric label="未命中" value={stats.misses} />
              <Metric label="节省 token" value={fmtNum(stats.saved_tokens)} />
              <Metric
                label="节省费用"
                value={`${stats.estimated_savings.toFixed(4)} ${stats.currency}`}
              />
              <Metric label="计费 prompt" value={fmtNum(stats.billed_tokens)} />
              <Metric
                label="输入花费"
                value={`${stats.spend.toFixed(4)} ${stats.currency}`}
              />
              <Metric
                label="前缀稳定度"
                value={`${(stats.prefix_stability * 100).toFixed(1)}%`}
              />
            </div>
          </div>
        ) : (
          <div className="skeleton">
            <div className="sk-line sk-md" />
            <div className="sk-line sk-sm" />
          </div>
        )}
      </div>

      <div className="card">
        <h2 className="card-title">
          <Calculator size={14} className="icon" /> 单次成本估算
        </h2>
        <div className="cost-layout">
          <div>
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
                style={{ minHeight: 160, resize: "vertical" }}
              />
            </div>
            <button className="btn" onClick={estimate} disabled={estimating}>
              {estimating ? (
                <>
                  <CircleNotch size={14} className="spin" /> 估算中…
                </>
              ) : (
                <>
                  <Coins size={14} /> 估算成本
                </>
              )}
            </button>
          </div>

          <div className="cost-result">
            {!cost ? (
              <div className="muted" style={{ padding: "24px 0", textAlign: "center" }}>
                估算结果会显示在这里
              </div>
            ) : cost.ok === false ? (
              <div className="cost-error">{cost.error}</div>
            ) : (
              <>
                <div className="cost-total">
                  <span className="muted">预计费用</span>
                  <span className="cost-total-value mono">
                    {cost.estimated_cost.toFixed(6)}{" "}
                    <small>{cost.currency}</small>
                  </span>
                </div>
                <div className="cost-rows">
                  <Row label="输入合计" value={cost.input_tokens} />
                  <Row
                    label="系统提示词 (system.md+rules.md)"
                    value={cost.prefix_tokens}
                    muted
                  />
                  <Row label="用户输入" value={cost.user_tokens} muted />
                  <Row label="输出 token（估算）" value={cost.output_tokens} />
                  <Row
                    label="单价（每 1M）"
                    value={`${cost.input_per_1m} / ${cost.output_per_1m}`}
                  />
                  <Row label="价格来源" value={cost.pricing_source} muted />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  muted,
}: {
  label: string;
  value: any;
  muted?: boolean;
}) {
  return (
    <div className="cost-row">
      <span className="muted">{label}</span>
      <span className={muted ? "mono muted" : "mono"}>{value}</span>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="usage-cell">
      <div className="usage-cell-label">{label}</div>
      <div className="usage-cell-value">{value}</div>
    </div>
  );
}

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}
