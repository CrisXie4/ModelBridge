"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Plus,
  Sparkle,
  MagnifyingGlass,
  Key,
  LockKey,
  LockKeyOpen,
  PencilSimple,
  Trash,
  X,
} from "@phosphor-icons/react";
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
  const [query, setQuery] = useState("");
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

  const filtered = useMemo(() => {
    if (!query) return models;
    const q = query.toLowerCase();
    return models.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q) ||
        m.model.toLowerCase().includes(q)
    );
  }, [models, query]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">渠道 / 模型</h1>
          <p className="page-sub">
            增删改 models.yaml，每个「渠道」= base_url + api_key + 模型 ID。
          </p>
        </div>
        <div className="toolbar">
          <button
            className="btn"
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
          >
            <Plus size={14} weight="bold" /> 手动添加
          </button>
          <button className="btn btn-secondary" onClick={() => setShowCatalog(true)}>
            <Sparkle size={14} /> 从目录添加
          </button>
        </div>
      </div>

      {models.length > 0 && (
        <div className="model-search">
          <MagnifyingGlass size={14} className="model-search-icon" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索已配置的模型 / 厂商…"
            className="model-search-input"
          />
        </div>
      )}

      {loading ? (
        <div className="card skeleton">
          <div className="sk-line sk-md" />
          <div className="sk-line sk-lg" />
          <div className="sk-line sk-md" />
        </div>
      ) : models.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">
              <Plus size={20} />
            </div>
            <div className="empty-title">还没有配置任何模型</div>
            <div className="empty-hint">
              点右上角「从目录添加」，从内置厂商目录一键创建第一个渠道；
              或「手动添加」填写 base_url 与 API Key。
            </div>
          </div>
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
              {filtered.map((m) => (
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
                      <span
                        className="key-ok"
                        title="API Key 已配置（加密存储）"
                      >
                        <LockKey size={14} weight="fill" /> 就绪
                      </span>
                    ) : (
                      <span className="key-miss" title="未配置 API Key">
                        <LockKeyOpen size={14} /> 缺失
                      </span>
                    )}
                  </td>
                  <td>
                    <span className="cap-chips">
                      {CAP_FLAGS.filter((f) => m.capabilities?.[f]).map((f) => (
                        <span key={f} className="cap-chip">
                          {f}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="btn btn-sm btn-ghost"
                        onClick={() => {
                          setEditing(m);
                          setShowForm(true);
                        }}
                      >
                        <PencilSimple size={13} /> 编辑
                      </button>
                      <button
                        className="btn btn-sm btn-danger btn-outline"
                        onClick={() => remove(m.name)}
                      >
                        <Trash size={13} /> 删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="muted" style={{ padding: 20, textAlign: "center" }}>
              没有匹配「{query}」的模型。
            </div>
          )}
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
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        style={{ width: 560, padding: 24 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 className="modal-title">
            {initial ? "编辑渠道" : prefill ? "从目录添加" : "手动添加渠道"}
          </h2>
          <button className="btn btn-sm btn-ghost modal-close" onClick={onClose}>
            <X size={15} />
          </button>
        </div>

        {prefill && !initial && (
          <div className="prefill-note">
            <strong className="mono">{prefill.model}</strong>
            <span>
              {prefill.currency} {prefill.input_per_1m} /{" "}
              {prefill.output_per_1m} 每 1M · 上下文 {fmtCtx(prefill.context_window)}
              {prefill.cache_hit_input_per_1m != null &&
                ` · 缓存 ${prefill.cache_hit_input_per_1m}`}
            </span>
            <span className="muted">价格来源 [{prefill.pricing_source}]</span>
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

        <div className="row">
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
          </div>
          <div className="field">
            <label>
              <Key size={12} style={{ verticalAlign: -1 }} /> 或环境变量名
            </label>
            <input
              value={form.api_key_env}
              onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
              placeholder="DEEPSEEK_API_KEY"
              className="mono"
            />
          </div>
        </div>
        <div className="field-hint" style={{ marginTop: -8, marginBottom: 14 }}>
          存储时自动加密（keyring / Fernet），不会明文落盘。
        </div>

        <div className="field">
          <label>能力标志</label>
          <div className="cap-switches">
            {CAP_FLAGS.map((f) => (
              <button
                type="button"
                key={f}
                className={caps[f] ? "cap-switch on" : "cap-switch"}
                onClick={() => setCaps({ ...caps, [f]: !caps[f] })}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={save} disabled={saving}>
            {saving ? "保存中…" : initial ? "保存修改" : "创建渠道"}
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
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
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal catalog-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="catalog-head">
          <div>
            <h2 className="modal-title">模型目录</h2>
            <div className="muted" style={{ fontSize: 12 }}>
              {catalog.length} 个内置模型 · 点击行预填表单
            </div>
          </div>
          <div className="spacer" />
          <div className="model-search" style={{ width: 220 }}>
            <MagnifyingGlass size={14} className="model-search-icon" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索模型 / 厂商…"
              className="model-search-input"
              autoFocus
            />
          </div>
          <button className="btn btn-sm btn-ghost modal-close" onClick={onClose}>
            <X size={15} />
          </button>
        </div>

        <div className="catalog-filters">
          {providers.map((p) => (
            <button
              key={p}
              className={provider === p ? "filter-chip on" : "filter-chip"}
              onClick={() => setProvider(p)}
            >
              {p === "all" ? "全部" : p}
            </button>
          ))}
        </div>

        <div className="catalog-body">
          {loading ? (
            <div className="muted" style={{ padding: 24 }}>
              加载目录…
            </div>
          ) : filtered.length === 0 ? (
            <div className="muted" style={{ padding: 24 }}>
              没有匹配的模型。
            </div>
          ) : (
            <table style={{ fontSize: 12.5 }}>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>厂商</th>
                  <th className="num">输入价</th>
                  <th className="num">输出价</th>
                  <th className="num">缓存</th>
                  <th className="num">上下文</th>
                  <th>端点</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr
                    key={c.model}
                    style={{ cursor: "pointer" }}
                    onClick={() => onPick(c)}
                  >
                    <td className="mono" style={{ fontWeight: 600 }}>
                      {c.model}
                      {c.is_local && (
                        <span className="badge badge-muted" style={{ marginLeft: 8 }}>
                          本地
                        </span>
                      )}
                    </td>
                    <td>
                      <span className="badge badge-accent">{c.provider}</span>
                    </td>
                    <td className="mono num">
                      {fmtPrice(c.input_per_1m, c.currency)}
                    </td>
                    <td className="mono num">
                      {fmtPrice(c.output_per_1m, c.currency)}
                    </td>
                    <td className="mono num" style={{ color: "var(--mb-muted)" }}>
                      {c.cache_hit_input_per_1m != null
                        ? fmtPrice(c.cache_hit_input_per_1m, c.currency)
                        : "—"}
                    </td>
                    <td className="mono num">{fmtCtx(c.context_window)}</td>
                    <td
                      className="mono"
                      style={{
                        color: "var(--mb-muted)",
                        maxWidth: 220,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {c.base_url}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
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
