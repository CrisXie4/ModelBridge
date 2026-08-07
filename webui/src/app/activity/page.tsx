"use client";

import { useEffect, useState } from "react";
import { api, SessionLive } from "@/lib/api";

const POLL_MS = 2000;

export default function ActivityPage() {
  const [data, setData] = useState<SessionLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const d = await api<SessionLive>("/session/live");
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      }
    }

    load();
    const timer = setInterval(load, POLL_MS);
    const ticker = setInterval(
      () => setTick((t) => (t + 1) % 1_000_000),
      1000
    );
    const onVis = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelled = true;
      clearInterval(timer);
      clearInterval(ticker);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  return (
    <div>
      <h1 className="page-title">Agent 活动</h1>
      <p className="page-sub">
        正在运行的 <span className="mono">mbridge</span> REPL 实时状态 ——
        上下文用量、当前主题、AI 的待办计划。每 {POLL_MS / 1000} 秒刷新。
      </p>

      {error ? (
        <div className="card" style={{ borderColor: "#e5484d" }}>
          连接失败：{error}
        </div>
      ) : !data ? (
        <Skeleton />
      ) : !data.online ? (
        <OfflineCard />
      ) : (
        <LiveView data={data} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 在线视图
// ---------------------------------------------------------------------------

function LiveView({ data }: { data: SessionLive }) {
  const ctx = data.context;
  return (
    <>
      <StatusHeader data={data} />

      {ctx && (
        <div className="card">
          <h2 className="card-title">上下文用量</h2>
          <ContextBar
            used={ctx.used_tokens}
            total={ctx.context_window}
            pct={ctx.used_pct}
            free={ctx.free_tokens}
            msgs={ctx.message_count}
          />
        </div>
      )}

      <div className="card">
        <TodoHeader summary={data.todo_summary} />
        <TodoList todos={data.todos} />
      </div>
    </>
  );
}

function StatusHeader({ data }: { data: SessionLive }) {
  const statusMeta = STATUS_META[data.status] ?? STATUS_META.idle;
  const rel = data.updated_at ? relativeTime(data.age_seconds) : null;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          className={`status-dot ${data.status}`}
          title={statusMeta.label}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
            <span className="status-label">{statusMeta.label}</span>
            {data.model && (
              <span className="mono" style={{ color: "var(--mb-muted)" }}>
                {data.model}
              </span>
            )}
          </div>
          <div className="topic">
            {data.topic ? (
              <>
                <span className="topic-kicker">主题</span>
                {data.topic}
              </>
            ) : (
              <span style={{ color: "var(--mb-muted)" }}>等待输入…</span>
            )}
          </div>
        </div>
        {rel && (
          <span className="rel-time" title={data.updated_at ?? ""}>
            {rel}
          </span>
        )}
      </div>
      {data.cwd && (
        <div className="cwd-line mono" title={data.cwd}>
          📁 {data.cwd}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 上下文用量条
// ---------------------------------------------------------------------------

function ContextBar({
  used,
  total,
  pct,
  free,
  msgs,
}: {
  used: number;
  total: number;
  pct: number;
  free: number;
  msgs: number;
}) {
  const pctClamped = Math.min(100, Math.max(0, pct));
  const grade =
    pct < 50 ? "ok" : pct < 80 ? "warn" : "critical";
  const color = GRADE_COLOR[grade];

  return (
    <div>
      <div className="ctx-track">
        <div
          className={`ctx-fill ${grade}`}
          style={{ width: `${pctClamped}%` }}
        />
      </div>
      <div className="ctx-meta">
        <span style={{ color }}>
          <strong>{fmtTokens(used)}</strong> / {fmtTokens(total)} tokens
        </span>
        <span className="ctx-pct" style={{ color }}>
          {pct.toFixed(1)}% 已用
        </span>
        <span style={{ color: "var(--mb-muted)" }}>
          剩余 <strong>{fmtTokens(free)}</strong> · {msgs} 条消息
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 待办列表
// ---------------------------------------------------------------------------

function TodoHeader({
  summary,
}: {
  summary: SessionLive["todo_summary"];
}) {
  const { total, done, in_progress, pending } = summary;
  const completePct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className="todo-head">
      <h2 className="card-title" style={{ margin: 0 }}>
        待办计划
      </h2>
      {total > 0 ? (
        <div className="todo-meta">
          <span className="badge badge-ok">{done} 完成</span>
          {in_progress > 0 && (
            <span className="badge badge-accent">{in_progress} 进行中</span>
          )}
          {pending > 0 && (
            <span className="badge badge-muted">{pending} 待办</span>
          )}
          <span className="todo-pct">{completePct}%</span>
        </div>
      ) : null}
    </div>
  );
}

function TodoList({ todos }: { todos: SessionLive["todos"] }) {
  if (todos.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">✦</div>
        <div className="empty-title">AI 还没有写下计划</div>
        <div className="empty-hint">
          在 REPL 里让 AI 处理一个多步任务，它会用 <code>todo</code> 工具拆解
          步骤并实时同步到这里。例如：
        </div>
        <code className="empty-example">帮我重构 auth 模块并写好计划</code>
      </div>
    );
  }

  // 排序：进行中 → 待办 → 完成；同状态按优先级（high→normal→low）
  const order: Record<string, number> = {
    in_progress: 0,
    pending: 1,
    done: 2,
  };
  const prio: Record<string, number> = { high: 0, normal: 1, low: 2 };
  const sorted = [...todos].sort(
    (a, b) =>
      (order[a.status] ?? 9) - (order[b.status] ?? 9) ||
      (prio[a.priority] ?? 9) - (prio[b.priority] ?? 9) ||
      a.id - b.id
  );

  return (
    <ul className="todo-list">
      {sorted.map((t) => (
        <li key={t.id} className={`todo-item ${t.status}`}>
          <span className="todo-mark">{TODO_MARK[t.status]}</span>
          <span className="todo-content">{t.content}</span>
          {t.priority === "high" && t.status !== "done" && (
            <span className="badge badge-err">高</span>
          )}
          <span className="todo-id">#{t.id}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// 离线 / 骨架
// ---------------------------------------------------------------------------

function OfflineCard() {
  return (
    <div className="card">
      <div className="empty">
        <div className="empty-icon" style={{ color: "#8b93a7" }}>
          ○
        </div>
        <div className="empty-title">没有正在运行的 REPL</div>
        <div className="empty-hint">
          在项目目录里启动一个会话，状态会出现在这里：
        </div>
        <code className="empty-example">mbridge</code>
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <>
      <div className="card skeleton">
        <div className="sk-line sk-lg" />
        <div className="sk-line sk-md" />
      </div>
      <div className="card skeleton">
        <div className="sk-line sk-sm" />
        <div className="sk-track" />
        <div className="sk-line sk-sm" />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const STATUS_META: Record<
  string,
  { label: string; color: string }
> = {
  idle: { label: "空闲", color: "#3fb950" },
  working: { label: "处理中", color: "#d29922" },
  offline: { label: "离线", color: "#8b93a7" },
};

const GRADE_COLOR: Record<string, string> = {
  ok: "#3fb950",
  warn: "#d29922",
  critical: "#e5484d",
};

const TODO_MARK: Record<string, string> = {
  done: "✓",
  in_progress: "▸",
  pending: "○",
};

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 100) / 10}K`;
  return String(n);
}

function relativeTime(s: number | null): string {
  if (s === null) return "";
  if (s < 2) return "刚刚";
  if (s < 60) return `${Math.round(s)}秒前`;
  if (s < 3600) return `${Math.round(s / 60)}分钟前`;
  return `${Math.round(s / 3600)}小时前`;
}

// ---------------------------------------------------------------------------
// 局部样式
// ---------------------------------------------------------------------------

<style jsx>{`
  .status-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
    background: ${STATUS_META.idle.color};
    position: relative;
  }
  .status-dot.working {
    background: ${STATUS_META.working.color};
    box-shadow: 0 0 10px ${STATUS_META.working.color}aa;
  }
  .status-dot.working::after {
    content: "";
    position: absolute;
    inset: -4px;
    border-radius: 50%;
    border: 2px solid ${STATUS_META.working.color};
    animation: pulse 1.2s ease-out infinite;
  }
  @keyframes pulse {
    0% {
      opacity: 0.6;
      transform: scale(0.7);
    }
    100% {
      opacity: 0;
      transform: scale(1.9);
    }
  }
  .status-label {
    font-size: 16px;
    font-weight: 700;
  }
  .topic {
    margin-top: 6px;
    font-size: 14px;
    color: var(--mb-text);
    line-height: 1.5;
    word-break: break-word;
  }
  .topic-kicker {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--mb-muted);
    margin-right: 8px;
    padding: 1px 6px;
    border: 1px solid var(--mb-border);
    border-radius: 4px;
    vertical-align: middle;
  }
  .rel-time {
    font-size: 12px;
    color: var(--mb-muted);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
    white-space: nowrap;
  }
  .cwd-line {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--mb-border);
    font-size: 12px;
    color: var(--mb-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .ctx-track {
    height: 10px;
    background: var(--mb-bg);
    border: 1px solid var(--mb-border);
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 10px;
  }
  .ctx-fill {
    height: 100%;
    border-radius: 5px;
    transition: width 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    background: ${GRADE_COLOR.ok};
  }
  .ctx-fill.warn {
    background: ${GRADE_COLOR.warn};
  }
  .ctx-fill.critical {
    background: ${GRADE_COLOR.critical};
  }
  .ctx-meta {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    font-size: 13px;
    align-items: baseline;
  }
  .ctx-pct {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .todo-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .todo-meta {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .todo-pct {
    font-size: 13px;
    font-weight: 700;
    color: var(--mb-muted);
    margin-left: 4px;
    font-variant-numeric: tabular-nums;
  }
  :global(.todo-list) {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .todo-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 4px;
    border-bottom: 1px solid var(--mb-border);
    font-size: 13px;
  }
  .todo-item:last-child {
    border-bottom: none;
  }
  .todo-mark {
    width: 18px;
    text-align: center;
    font-weight: 700;
    flex-shrink: 0;
    color: var(--mb-muted);
  }
  .todo-item.in_progress .todo-mark {
    color: var(--mb-accent);
  }
  .todo-item.done .todo-mark {
    color: #3fb950;
  }
  .todo-content {
    flex: 1;
    min-width: 0;
    word-break: break-word;
  }
  .todo-item.done .todo-content {
    color: var(--mb-muted);
    text-decoration: line-through;
  }
  .todo-id {
    font-size: 11px;
    color: var(--mb-muted);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  .empty {
    text-align: center;
    padding: 32px 16px;
  }
  .empty-icon {
    font-size: 28px;
    margin-bottom: 12px;
  }
  .empty-title {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .empty-hint {
    font-size: 13px;
    color: var(--mb-muted);
    max-width: 440px;
    margin: 0 auto 14px;
    line-height: 1.6;
  }
  .empty-example,
  :global(code) {
    background: var(--mb-bg);
    border: 1px solid var(--mb-border);
    border-radius: 6px;
    padding: 2px 7px;
    font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
    font-size: 12px;
  }
  .empty-example {
    display: inline-block;
    padding: 6px 14px;
  }
  .skeleton {
    min-height: 80px;
  }
  .sk-line {
    height: 14px;
    background: linear-gradient(
      90deg,
      var(--mb-border) 25%,
      rgba(255, 255, 255, 0.06) 50%,
      var(--mb-border) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.4s ease-in-out infinite;
    border-radius: 5px;
    margin-bottom: 10px;
  }
  .sk-lg {
    width: 45%;
    height: 20px;
  }
  .sk-md {
    width: 70%;
  }
  .sk-sm {
    width: 30%;
  }
  .sk-track {
    height: 10px;
    border-radius: 6px;
    background: var(--mb-bg);
    border: 1px solid var(--mb-border);
    margin-bottom: 10px;
  }
  @keyframes shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .status-dot.working::after,
    .sk-line {
      animation: none;
    }
    .ctx-fill {
      transition: none;
    }
  }
`}</style>
