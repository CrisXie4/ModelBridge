"use client";

import { useEffect, useState } from "react";
import { api, CatalogEntry, ModelOut } from "@/lib/api";

const PROVIDERS = [
  "openai-compatible",
  "deepseek",
  "qwen",
  "kimi",
  "mimo",
  "glm",
  "minimax",
  "hunyuan",
  "ollama",
  "vllm",
  "lmstudio",
  "openai",
  "custom",
];

const LEVELS = ["tiny", "cheap", "coder", "agent", "expert"];

const CAP_FLAGS = [
  "tools",
  "json",
  "vision",
  "reasoning",
  "reasoning_content_back",
  "cache",
  "local",
  "streaming",
];

export default function ModelsPage() {
  const [models, setModels] = useState<ModelOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ModelOut | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);
  const [prefill, setPrefill] = useState<CatalogEntry | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  function load() {
    setLoading(true);
    api<{ models: ModelOut[] }>("/models")
      .then((r) => setModels(r.models))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  function flash(msg: string, ok = true) {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 2500);
  }

  async function remove(name: string) {
    if (!confirm(`确认删除模型「${name}」？`)) return;
    try {
      await api(`/models/${name}`, { method: "DELETE" });
      flash(`已删除 ${name}`);
      load();
    } catch (e: any) {
      flash(e.message, false);
    }
  }

  return (
    <div>
      <h1 className="page-title">渠道 / 模型管理</h1>
      <p className="page-sub">
        增删改 models.yaml —— 每个「渠道」对应一个 provider 模型配置（base_url +
        api_key + 模型ID）。
      </p>

      <div style={{ marginBottom: 16, display: "flex", gap: 10 }}>
        <button
          className="btn"
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
        >
          + 手动添加
        </button>
        <button
          className="btn btn-secondary"
          onClick={() => setShowCatalog(true)}
        >
          ✦ 从目录添加
        </button>
      </div>

      {loading ? (
        <div className="card">加载中…</div>
      ) : models.length === 0 ? (
        <div className="card" style={{ color: "var(--mb-muted)" }}>
          还没有配置任何模型。点「添加渠道」创建第一个。
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>Provider</th>
                <th>模型 ID</th>
                <th>Base URL</th>
                <th>等级</th>
                <th>Key</th>
                <th>能力</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.name}>
                  <td className="mono" style={{ fontWeight: 600 }}>
                    {m.name}
                  </td>
                  <td>
                    <span className="badge badge-accent">{m.provider}</span>
                  </td>
                  <td className="mono">{m.model}</td>
                  <td className="mono" style={{ color: "var(--mb-muted)" }}>
                    {m.base_url}
                  </td>
                  <td>
                    <span className="badge badge-muted">{m.level}</span>
                  </td>
                  <td>
                    {m.has_api_key ? (
                      <span className="badge badge-ok">已设</span>
                    ) : (
                      <span className="badge badge-err">缺失</span>
                    )}
                  </td>
                  <td>
                    {CAP_FLAGS.filter((f) => m.capabilities?.[f]).map((f) => (
                      <span
                        key={f}
                        className="badge badge-muted"
                        style={{ marginRight: 4 }}
                      >
                        {f}
                      </span>
                    ))}
                  </td>
                  <td>
                    <button
                      className="btn btn-sm btn-secondary"
                      style={{ marginRight: 6 }}
                      onClick={() => {
                        setEditing(m);
                        setShowForm(true);
                      }}
                    >
                      编辑
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => remove(m.name)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <ModelForm
          initial={editing}
          prefill={prefill}
          onClose={() => {
            setShowForm(false);
            setPrefill(null);
          }}
          onSaved={() => {
            setShowForm(false);
            setPrefill(null);
            load();
          }}
          flash={flash}
        />
      )}

      {showCatalog && (
        <CatalogPicker
          onClose={() => setShowCatalog(false)}
          onPick={(entry) => {
            setPrefill(entry);
            setEditing(null);
            setShowCatalog(false);
            setShowForm(true);
          }}
        />
      )}

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function ModelForm({
  initial,
  prefill,
  onClose,
  onSaved,
  flash,
}: {
  initial: ModelOut | null;
  prefill?: CatalogEntry | null;
  onClose: () => void;
  onSaved: () => void;
  flash: (msg: string, ok?: boolean) => void;
}) {
  const [form, setForm] = useState({
    name: initial?.name || prefill?.model || "",
    provider: initial?.provider || prefill?.provider || "deepseek",
    base_url: initial?.base_url || prefill?.base_url || "",
    model: initial?.model || prefill?.model || "",
    api_key: "",
    api_key_env: initial?.api_key_env || prefill?.api_key_env || "",
    level: initial?.level || prefill?.default_level || "cheap",
  });
  const [caps, setCaps] = useState<Record<string, boolean>>(
    initial?.capabilities || {}
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!form.name || !form.base_url || !form.model) {
      flash("名称、Base URL、模型 ID 必填", false);
      return;
    }
    setSaving(true);
    try {
      const body: any = {
        name: form.name,
        provider: form.provider,
        type: "openai-compatible",
        base_url: form.base_url,
        model: form.model,
        level: form.level,
        capabilities: caps,
        api_key_env: form.api_key_env || null,
      };
      // 只有填了才发 api_key（编辑时空着 = 不改）
      if (form.api_key) body.api_key = form.api_key;

      const method = initial ? "PUT" : "POST";
      const path = initial ? `/models/${initial.name}` : "/models";
      await api(path, { method, body: JSON.stringify(body) });
      flash(initial ? "已更新" : "已创建");
      onSaved();
    } catch (e: any) {
      flash(e.message, false);
    } finally {
      setSaving(false);
    }
  }

  return (
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
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: 520, maxHeight: "85vh", overflowY: "auto" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="card-title">
          {initial ? "编辑渠道" : prefill ? "从目录添加" : "手动添加渠道"}
        </h2>

        {prefill && !initial && (
          <div
            style={{
              background: "rgba(79,140,255,0.08)",
              border: "1px solid rgba(79,140,255,0.3)",
              borderRadius: 8,
              padding: "10px 12px",
              marginBottom: 16,
              fontSize: 12,
            }}
          >
            <strong>{prefill.model}</strong> · {prefill.currency}{" "}
            {prefill.input_per_1m}/{prefill.output_per_1m} per 1M · ctx{" "}
            {fmtCtx(prefill.context_window)}
            {prefill.cache_hit_input_per_1m != null &&
              ` · cache ${prefill.cache_hit_input_per_1m}`}{" "}
            <span style={{ color: "var(--mb-muted)" }}>
              [{prefill.pricing_source}]
            </span>
          </div>
        )}

        <div className="field">
          <label>名称（唯一标识）</label>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="deepseek-chat"
            disabled={!!initial}
          />
        </div>

        <div className="row">
          <div className="field">
            <label>Provider</label>
            <select
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>等级</label>
            <select
              value={form.level}
              onChange={(e) => setForm({ ...form, level: e.target.value })}
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <label>Base URL</label>
          <input
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="https://api.deepseek.com"
            className="mono"
          />
        </div>

        <div className="field">
          <label>模型 ID（provider 侧）</label>
          <input
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="deepseek-chat"
            className="mono"
          />
        </div>

        <div className="field">
          <label>
            API Key{initial ? "（留空 = 不修改）" : ""}
          </label>
          <input
            type="password"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            placeholder="sk-..."
            className="mono"
          />
          <div style={{ fontSize: 11, color: "var(--mb-muted)", marginTop: 4 }}>
            存储时自动加密（keyring / Fernet），不会明文落盘。
          </div>
        </div>

        <div className="field">
          <label>或用环境变量名</label>
          <input
            value={form.api_key_env}
            onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
            placeholder="DEEPSEEK_API_KEY"
            className="mono"
          />
        </div>

        <div className="field">
          <label>能力标志</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {CAP_FLAGS.map((f) => (
              <label
                key={f}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  cursor: "pointer",
                  color: "var(--mb-text)",
                  fontSize: 13,
                  margin: 0,
                }}
              >
                <input
                  type="checkbox"
                  checked={!!caps[f]}
                  onChange={(e) =>
                    setCaps({ ...caps, [f]: e.target.checked })
                  }
                  style={{ width: "auto" }}
                />
                {f}
              </label>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
          <button className="btn" onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
          <button className="btn btn-secondary" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CatalogPicker — 从内置模型目录里挑一个，一键预填表单
// ---------------------------------------------------------------------------

function CatalogPicker({
  onClose,
  onPick,
}: {
  onClose: () => void;
  onPick: (entry: CatalogEntry) => void;
}) {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState("all");

  useEffect(() => {
    api<{ catalog: CatalogEntry[]; count: number }>("/models/catalog")
      .then((r) => setCatalog(r.catalog))
      .finally(() => setLoading(false));
  }, []);

  const providers = ["all", ...new Set(catalog.map((c) => c.provider))];

  const filtered = catalog.filter((c) => {
    if (provider !== "all" && c.provider !== provider) return false;
    if (query) {
      const q = query.toLowerCase();
      return (
        c.model.toLowerCase().includes(q) ||
        c.provider.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 999,
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          width: "min(920px, 92vw)",
          maxHeight: "85vh",
          display: "flex",
          flexDirection: "column",
          padding: 0,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--mb-border)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <h2 className="card-title" style={{ margin: 0 }}>
            模型目录
          </h2>
          <span style={{ color: "var(--mb-muted)", fontSize: 12 }}>
            {catalog.length} 个内置模型 · 点击一行预填表单
          </span>
          <div style={{ flex: 1 }} />
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            style={{ width: "auto", minWidth: 120 }}
          >
            {providers.map((p) => (
              <option key={p} value={p}>
                {p === "all" ? "全部厂商" : p}
              </option>
            ))}
          </select>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索模型 / 厂商…"
            style={{ width: 200 }}
            autoFocus
          />
        </div>

        <div style={{ overflowY: "auto", flex: 1 }}>
          {loading ? (
            <div style={{ padding: 24, color: "var(--mb-muted)" }}>
              加载目录…
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: 24, color: "var(--mb-muted)" }}>
              没有匹配的模型。
            </div>
          ) : (
            <table style={{ fontSize: 12 }}>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>厂商</th>
                  <th>输入价</th>
                  <th>输出价</th>
                  <th>缓存</th>
                  <th>上下文</th>
                  <th>端点</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr
                    key={c.model}
                    style={{ cursor: "pointer" }}
                    onClick={() => onPick(c)}
                    className="catalog-row"
                  >
                    <td className="mono" style={{ fontWeight: 600 }}>
                      {c.model}
                      {c.is_local && (
                        <span
                          className="badge badge-muted"
                          style={{ marginLeft: 6, fontSize: 10 }}
                        >
                          本地
                        </span>
                      )}
                    </td>
                    <td>
                      <span className="badge badge-accent">{c.provider}</span>
                    </td>
                    <td className="mono">
                      {fmtPrice(c.input_per_1m, c.currency)}
                    </td>
                    <td className="mono">
                      {fmtPrice(c.output_per_1m, c.currency)}
                    </td>
                    <td className="mono" style={{ color: "var(--mb-muted)" }}>
                      {c.cache_hit_input_per_1m != null
                        ? fmtPrice(c.cache_hit_input_per_1m, c.currency)
                        : "—"}
                    </td>
                    <td className="mono">{fmtCtx(c.context_window)}</td>
                    <td
                      className="mono"
                      style={{
                        color: "var(--mb-muted)",
                        maxWidth: 200,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {c.base_url}
                    </td>
                    <td>
                      <button className="btn btn-sm">选用</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <style jsx>{`
          :global(.catalog-row:hover) {
            background: rgba(79, 140, 255, 0.06);
          }
        `}</style>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function fmtCtx(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`;
  if (n >= 1_000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function fmtPrice(v: number, currency: string): string {
  const sym = currency === "CNY" ? "¥" : "$";
  if (v < 0.01) return `${sym}${v.toFixed(4)}`;
  return `${sym}${v.toFixed(2)}`;
}
