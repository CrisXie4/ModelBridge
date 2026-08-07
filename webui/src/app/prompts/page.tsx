"use client";

import { useEffect, useState } from "react";
import { api, PromptFiles } from "@/lib/api";

export default function PromptsPage() {
  const [tab, setTab] = useState<"system" | "rules">("system");
  const [data, setData] = useState<PromptFiles | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    api<PromptFiles>("/prompts").then((d) => {
      setData(d);
      setDraft(d.system);
    });
  }, []);

  useEffect(() => {
    if (data) setDraft(tab === "system" ? data.system : data.rules);
  }, [tab, data]);

  function flash(msg: string, ok = true) {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 2500);
  }

  async function save() {
    setSaving(true);
    try {
      await api(`/prompts/${tab}`, {
        method: "PUT",
        body: JSON.stringify({ content: draft }),
      });
      flash(`${tab}.md 已保存`);
    } catch (e: any) {
      flash(e.message, false);
    } finally {
      setSaving(false);
    }
  }

  if (!data) return <div className="card">加载中…</div>;

  return (
    <div>
      <h1 className="page-title">系统提示词</h1>
      <p className="page-sub">
        编辑全局 system.md / rules.md。所有项目共用，项目级规则文件可覆盖。
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          className={tab === "system" ? "btn" : "btn btn-secondary"}
          onClick={() => setTab("system")}
        >
          system.md
        </button>
        <button
          className={tab === "rules" ? "btn" : "btn btn-secondary"}
          onClick={() => setTab("rules")}
        >
          rules.md
        </button>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{
            minHeight: 480,
            border: "none",
            borderRadius: 12,
            background: "var(--mb-panel)",
            lineHeight: 1.7,
            padding: 20,
            resize: "vertical",
          }}
          className="mono"
        />
      </div>

      <div style={{ display: "flex", gap: 12, marginTop: 16 }}>
        <button className="btn" onClick={save} disabled={saving}>
          {saving ? "保存中…" : `保存 ${tab}.md`}
        </button>
        <span
          style={{ color: "var(--mb-muted)", fontSize: 12, alignSelf: "center" }}
        >
          {draft.length} 字符
        </span>
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
