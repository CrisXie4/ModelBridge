"use client";

import { useEffect, useState } from "react";
import { FloppyDisk, CircleNotch, FileText, Scroll } from "@phosphor-icons/react";
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

  if (!data)
    return (
      <div className="page">
        <div className="card skeleton">
          <div className="sk-line sk-lg" />
          <div className="sk-track" />
          <div className="sk-line sk-md" />
        </div>
      </div>
    );

  const lineCount = draft ? draft.split("\n").length : 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">系统提示词</h1>
          <p className="page-sub">
            编辑全局 system.md / rules.md。所有项目共用，项目级规则文件可覆盖。
          </p>
        </div>
        <button className="btn" onClick={save} disabled={saving}>
          {saving ? (
            <>
              <CircleNotch size={14} className="spin" /> 保存中…
            </>
          ) : (
            <>
              <FloppyDisk size={14} /> 保存 {tab}.md
            </>
          )}
        </button>
      </div>

      <div className="editor-card">
        <div className="editor-chrome">
          <div className="seg">
            <button
              className={tab === "system" ? "seg-item active" : "seg-item"}
              onClick={() => setTab("system")}
            >
              <FileText size={13} style={{ verticalAlign: -2 }} /> system.md
            </button>
            <button
              className={tab === "rules" ? "seg-item active" : "seg-item"}
              onClick={() => setTab("rules")}
            >
              <Scroll size={13} style={{ verticalAlign: -2 }} /> rules.md
            </button>
          </div>
          <div className="editor-meta mono">
            {lineCount} 行 · {draft.length} 字符
          </div>
        </div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="editor-body mono"
          spellCheck={false}
        />
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
