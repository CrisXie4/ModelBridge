"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChatCircleDots, PaperPlaneRight, Play, Trash } from "@phosphor-icons/react";
import { api } from "@/lib/api";

type Ev = { seq: number; type: string; [k: string]: unknown };
type ChatMeta = { id: string; title: string; busy?: boolean };
type ModelOut = { name: string };

/* 极简 Markdown → HTML：围栏代码块、行内代码、粗体、标题、有序/无序列表。
   其余按原样段落渲染。所有插值都先做 HTML 转义，防注入。 */
function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s: string): string {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function mdToHtml(md: string): string {
  const out: string[] = [];
  const lines = md.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++; // closing fence (if any)
      out.push(
        `<pre data-lang="${esc(lang)}"><code>${esc(body.join("\n"))}</code></pre>`
      );
      continue;
    }
    if (/^#{1,4} /.test(line)) {
      const lvl = line.match(/^#+/)![0].length;
      out.push(`<h${lvl + 2} class="md-h">${inlineMd(line.slice(lvl + 1))}</h${lvl + 2}>`);
      i++;
      continue;
    }
    if (/^\s*[-*] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*] /.test(lines[i]))
        items.push(`<li>${inlineMd(lines[i++].replace(/^\s*[-*] /, ""))}</li>`);
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)] /.test(lines[i]))
        items.push(`<li>${inlineMd(lines[i++].replace(/^\s*\d+[.)] /, ""))}</li>`);
      out.push(`<ol>${items.join("")}</ol>`);
      continue;
    }
    if (line.trim() === "") {
      i++;
      continue;
    }
    // 段落：连续非空非结构行合并为一段。
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(```|#{1,4} |\s*[-*] |\s*\d+[.)] )/.test(lines[i])
    )
      para.push(lines[i++]);
    out.push(`<p>${inlineMd(para.join(" "))}</p>`);
  }
  return out.join("");
}

export default function ChatPage() {
  const [chats, setChats] = useState<ChatMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [liveTail, setLiveTail] = useState<{ reasoning: string; content: string }>({
    reasoning: "",
    content: "",
  });
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ id: string; tool: string; summary: string } | null>(null);
  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelOut[]>([]);
  const [model, setModel] = useState<string>("");
  const [error, setError] = useState("");

  const tailRef = useRef<HTMLDivElement>(null);
  const seenSeq = useRef(0);

  const refreshChats = useCallback(() => {
    api<{ chats: ChatMeta[] }>("/chats")
      .then((r) => setChats(r.chats))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshChats();
    api<{ models: ModelOut[] }>("/models")
      .then((r) => {
        setModels(r.models);
        if (r.models.length) setModel(r.models[0].name);
      })
      .catch(() => undefined);
  }, [refreshChats]);

  // 事件轮询：活跃会话每 400ms 拉增量。
  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    seenSeq.current = 0;
    setEvents([]);
    setLiveTail({ reasoning: "", content: "" });

    const pump = async () => {
      try {
        const snap = await api<{
          busy: boolean;
          pending: { id: string; tool: string; summary: string } | null;
          events: Ev[];
        }>(`/chats/${activeId}/events?after=${seenSeq.current}`);
        if (stop) return;
        setBusy(snap.busy);
        setPending(snap.pending);
        if (snap.events.length) {
          for (const ev of snap.events) {
            seenSeq.current = Math.max(seenSeq.current, ev.seq);
            if (ev.type === "delta") {
              setLiveTail((t) =>
                ev.kind === "reasoning"
                  ? { ...t, reasoning: t.reasoning + String(ev.text ?? "") }
                  : { ...t, content: t.content + String(ev.text ?? "") }
              );
              continue;
            }
            setLiveTail((t) => (t.content || t.reasoning ? { reasoning: "", content: "" } : t));
            setEvents((prev) => [...prev, ev]);
            void refreshChats();
          }
        }
      } catch {
        /* 会话被删除等——静默 */
      }
    };

    void pump();
    const timer = setInterval(() => void pump(), 400);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [activeId, refreshChats]);

  useEffect(() => {
    tailRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length, liveTail]);

  const newChat = async () => {
    const r = await api<{ id: string }>("/chats", { method: "POST", body: JSON.stringify({ title: "" }) });
    await refreshChats();
    setActiveId(r.id);
  };

  const removeChat = async (id: string) => {
    await api(`/chats/${id}`, { method: "DELETE" }).catch(() => undefined);
    if (activeId === id) setActiveId(null);
    refreshChats();
  };

  const send = async () => {
    if (!activeId || !input.trim() || busy) return;
    const text = input.trim();
    setInput("");
    try {
      await api(`/chats/${activeId}/send`, {
        method: "POST",
        body: JSON.stringify({ text, model: model || null }),
      });
      setBusy(true);
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  };

  const answer = async (decision: "y" | "n") => {
    if (!pending) return;
    await api(`/chats/approvals/${pending.id}`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }).catch(() => undefined);
  };

  const renderEvent = (ev: Ev) => {
    switch (ev.type) {
      case "user":
        return (
          <div className="chat-row chat-user">
            <div className="bubble-user">{String(ev.text)}</div>
          </div>
        );
      case "assistant":
        return (
          <div className="chat-row chat-ai">
            <div className="bubble-ai md" dangerouslySetInnerHTML={{ __html: mdToHtml(String(ev.content || "")) }} />
          </div>
        );
      case "tool":
        return (
          <div className={`chat-row chat-tool ${ev.error ? "is-err" : ""}`}>
            {ev.error ? "!" : "+"} {String(ev.name)} <span className="dim">{String(ev.args)}</span>
          </div>
        );
      case "notice":
        return <div className="chat-row chat-notice">{String(ev.text)}</div>;
      case "approval":
      case "decision":
        return null; // 由 pending 卡片或 decision 单独行处理
      default:
        return null;
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">AI 对话</h1>
          <p className="page-sub">
            直接驱动本机 agent：可以读写项目文件、执行命令。写操作与命令会弹出确认卡，
            全程在本机 <code>mbridge web</code> 进程内运行。
          </p>
        </div>
        <div className="chat-head-right">
          <select value={model} onChange={(e) => setModel(e.target.value)} className="chat-model">
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </select>
          <button className="btn btn-secondary" onClick={() => void newChat()}>
            <Play size={14} /> 新会话
          </button>
        </div>
      </div>

      {error ? <div className="card chat-error">{error}</div> : null}

      <div className="chat-layout">
        <aside className="chat-list card">
          <div className="chat-list-title">会话</div>
          {chats.map((c) => (
            <div
              key={c.id}
              className={`chat-item ${c.id === activeId ? "active" : ""}`}
              onClick={() => setActiveId(c.id)}
            >
              <ChatCircleDots size={15} />
              <span className="chat-item-title">{c.title}</span>
              <button
                className="chat-del"
                title="删除"
                onClick={(e) => {
                  e.stopPropagation();
                  void removeChat(c.id);
                }}
              >
                <Trash size={13} />
              </button>
            </div>
          ))}
          {chats.length === 0 ? <div className="dim">还没有会话，点“新会话”。</div> : null}
        </aside>

        <section className="chat-main card">
          <div className="chat-transcript">
            {!activeId ? (
              <div className="empty">
                <div className="empty-icon">
                  <ChatCircleDots size={20} />
                </div>
                <div className="empty-title">选择或新建一个会话</div>
                <div className="empty-hint">消息直接发给本机 agent，支持读写文件与 shell。</div>
              </div>
            ) : null}
            {events.map((ev) => (
              <div key={ev.seq}>{renderEvent(ev)}</div>
            ))}
            {liveTail.reasoning || liveTail.content ? (
              <>
                {liveTail.reasoning ? <div className="chat-row chat-reason">{liveTail.reasoning}</div> : null}
                <div className="chat-row chat-ai">
                  <div className="bubble-ai md">{liveTail.content}</div>
                </div>
              </>
            ) : null}
            {busy && !liveTail.content && !pending ? <div className="typing">• • •</div> : null}
            <div ref={tailRef} />
          </div>

          {pending ? (
            <div className="chat-approve">
              <div className="chat-approve-title">需要确认 · {pending.tool}</div>
              <div className="chat-approve-body">{pending.summary}</div>
              <div className="rail-actions">
                <button className="btn" onClick={() => void answer("y")}>
                  同意
                </button>
                <button className="btn btn-secondary" onClick={() => void answer("n")}>
                  拒绝
                </button>
              </div>
            </div>
          ) : null}

          <div className="chat-composer">
            <textarea
              rows={1}
              placeholder={activeId ? "向 AI 下达任务…" : "先选择会话"}
              disabled={!activeId || busy}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button className="btn" disabled={!activeId || busy || !input.trim()} onClick={() => void send()}>
              <PaperPlaneRight size={15} /> 发送
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
