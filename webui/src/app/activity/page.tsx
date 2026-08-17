"use client";

import { useEffect, useState } from "react";
import { Pulse, FolderOpen, ListChecks } from "@phosphor-icons/react";
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
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Agent 活动</h1>
          <p className="page-sub">
            正在运行的 <code>mbridge</code> REPL 实时状态，每 {POLL_MS / 1000}{" "}
            秒刷新。
          </p>
        </div>
      </div>

      {error ? (
        <div className="card" style={{ borderColor: "var(--mb-err)" }}>
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
    <div className="card" style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
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
          <FolderOpen size={12} style={{ verticalAlign: -1.5 }} /> {data.cwd}
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
  const grade = pct < 50 ? "ok" : pct < 80 ? "warn" : "critical";
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
        <ListChecks size={14} className="icon" /> 待办计划
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
        <div className="empty-icon">
          <ListChecks size={20} />
        </div>
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
        <div className="empty-icon">
          <Pulse size={20} />
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
  idle: { label: "空闲", color: "#46c07a" },
  working: { label: "处理中", color: "#e0b45c" },
  offline: { label: "离线", color: "#6d7a90" },
};

const GRADE_COLOR: Record<string, string> = {
  ok: "#46c07a",
  warn: "#e0b45c",
  critical: "#f0565c",
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
