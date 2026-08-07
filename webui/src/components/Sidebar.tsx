"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const NAV = [
  { href: "/", label: "概览", icon: "◈" },
  { href: "/activity", label: "Agent 活动", icon: "◉" },
  { href: "/models", label: "渠道 / 模型", icon: "▤" },
  { href: "/routing", label: "路由配置", icon: "⇄" },
  { href: "/skills", label: "Skills", icon: "✦" },
  { href: "/prompts", label: "提示词", icon: "✎" },
  { href: "/doctor", label: "自检", icon: "✓" },
  { href: "/usage", label: "成本 / 缓存", icon: "￥" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">◆</span>
        <span className="sidebar-name">ModelBridge</span>
      </div>
      <nav className="sidebar-nav">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={active ? "nav-item active" : "nav-item"}
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <ApiStatus />
      </div>
      <style jsx>{`
        .sidebar {
          width: 220px;
          border-right: 1px solid var(--mb-border);
          background: var(--mb-panel);
          display: flex;
          flex-direction: column;
          flex-shrink: 0;
          padding: 20px 0;
        }
        .sidebar-brand {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 0 20px 20px;
          border-bottom: 1px solid var(--mb-border);
        }
        .sidebar-logo {
          color: var(--mb-accent);
          font-size: 20px;
        }
        .sidebar-name {
          font-weight: 700;
          font-size: 15px;
        }
        .sidebar-nav {
          flex: 1;
          padding: 12px 8px;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .sidebar-foot {
          padding: 8px;
          border-top: 1px solid var(--mb-border);
        }
        :global(.nav-item) {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 9px 12px;
          border-radius: 8px;
          color: var(--mb-muted);
          text-decoration: none;
          font-size: 13px;
          font-weight: 500;
          transition: all 0.12s;
        }
        :global(.nav-item:hover) {
          background: rgba(255, 255, 255, 0.04);
          color: var(--mb-text);
        }
        :global(.nav-item.active) {
          background: rgba(79, 140, 255, 0.14);
          color: var(--mb-accent);
        }
        :global(.nav-icon) {
          width: 18px;
          text-align: center;
          font-size: 13px;
        }
      `}</style>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// ApiStatus — 近实时轮询 /api/health：
//   • 3s 轮询 + 标签页重新可见时立即刷新（切回来即时感知）
//   • 滚动延迟采样 → 迷你 sparkline，一眼看出健康趋势
//   • 按延迟分级 online / degraded(缓慢) / offline
//   • 相对时间「N秒前」每秒跳动，间歇期也保持“活着”的感觉
// ---------------------------------------------------------------------------
const POLL_MS = 3000;
const SPARK_MAX = 24;
const DEGRADED_MS = 300;

const STATUS_COLOR: Record<ApiState, string> = {
  checking: "#8b93a7",
  online: "#3fb950",
  degraded: "#d29922",
  offline: "#e5484d",
};

type ApiState = "checking" | "online" | "degraded" | "offline";

function relativeTime(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 1) return "刚刚";
  if (s < 60) return `${s}秒前`;
  return `${Math.round(s / 60)}分钟前`;
}

function Sparkline({ data, color }: { data: number[]; color: string }) {
  const w = 54;
  const h = 16;
  const pad = 2;
  const ih = h - pad * 2;
  const iw = w - pad * 2;

  if (data.length < 2) {
    return (
      <svg width={w} height={h} className="spark" aria-hidden="true">
        <line
          x1={pad}
          y1={h - pad}
          x2={w - pad}
          y2={h - pad}
          stroke={color}
          strokeOpacity="0.4"
          strokeWidth="1.5"
          strokeDasharray="2 3"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const step = iw / (data.length - 1);
  const pts = data.map((v, i) => {
    const x = pad + i * step;
    const y = pad + (1 - (v - min) / range) * ih;
    return [x, y] as const;
  });
  const line = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
    .join(" ");
  const last = pts[pts.length - 1];
  const first = pts[0];
  const area = `${line} L${last[0].toFixed(1)} ${h - pad} L${first[0].toFixed(
    1
  )} ${h - pad} Z`;

  return (
    <svg width={w} height={h} className="spark" aria-hidden="true">
      <defs>
        <linearGradient id="sparkfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.32" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#sparkfill)" />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r="1.8" fill={color} />
    </svg>
  );
}

function ApiStatus() {
  const [status, setStatus] = useState<ApiState>("checking");
  const [latency, setLatency] = useState<number | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const [lastCheckAt, setLastCheckAt] = useState<number | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      const t0 = performance.now();
      let ok = false;
      let ms: number | null = null;
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        const data = await res.json();
        ms = Math.round(performance.now() - t0);
        ok = res.ok && !!data?.ok;
      } catch {
        ok = false;
      }
      if (cancelled) return;

      if (ok && ms !== null) {
        setLatency(ms);
        setHistory((h) => [...h, ms].slice(-SPARK_MAX));
        setStatus(ms > DEGRADED_MS ? "degraded" : "online");
      } else {
        setLatency(null);
        setStatus("offline");
      }
      setLastCheckAt(Date.now());
    }

    check();
    const timer = setInterval(check, POLL_MS);
    const ticker = setInterval(
      () => setTick((t) => (t + 1) % 1_000_000),
      1000
    );

    function onVisible() {
      if (document.visibilityState === "visible") check();
    }
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      clearInterval(timer);
      clearInterval(ticker);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const label =
    status === "online"
      ? "后端在线"
      : status === "degraded"
      ? "响应缓慢"
      : status === "offline"
      ? "后端离线"
      : "连接中…";

  const rel = lastCheckAt !== null ? relativeTime(Date.now() - lastCheckAt) : null;
  const latencyText =
    latency !== null ? `${latency}ms` : status === "checking" ? "—" : "超时";
  const title = `后端状态：${label}${
    latency !== null ? `（${latency}ms）` : ""
  }`;

  return (
    <div className={`api-status ${status}`} title={title}>
      <div className="api-row">
        <span className={`api-dot ${status}`} />
        <span className="api-label">{label}</span>
        <span className={`api-latency ${status}`}>{latencyText}</span>
      </div>
      <div className="api-row api-row-sub">
        <Sparkline data={history} color={STATUS_COLOR[status]} />
        <span className="api-rel">{rel ?? "等待中"}</span>
      </div>
      <style jsx>{`
        .api-status {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 10px 12px;
          margin: 0 8px;
          border-radius: 10px;
          background: rgba(255, 255, 255, 0.025);
          border: 1px solid var(--mb-border);
          min-height: 60px;
        }
        .api-row {
          display: flex;
          align-items: center;
          gap: 9px;
        }
        .api-row-sub {
          justify-content: space-between;
        }
        .api-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
          position: relative;
          background: ${STATUS_COLOR["checking"]};
        }
        .api-dot::after {
          content: "";
          position: absolute;
          inset: -3px;
          border-radius: 50%;
          border: 1.5px solid transparent;
          opacity: 0;
        }
        .api-dot.online {
          background: ${STATUS_COLOR["online"]};
          box-shadow: 0 0 8px ${STATUS_COLOR["online"]}99;
        }
        .api-dot.online::after {
          border-color: ${STATUS_COLOR["online"]};
          animation: pulse 2.4s ease-out infinite;
        }
        .api-dot.degraded {
          background: ${STATUS_COLOR["degraded"]};
          box-shadow: 0 0 8px ${STATUS_COLOR["degraded"]}99;
        }
        .api-dot.degraded::after {
          border-color: ${STATUS_COLOR["degraded"]};
          animation: pulse 1.1s ease-out infinite;
        }
        .api-dot.checking::after {
          border-color: ${STATUS_COLOR["checking"]};
          animation: pulse 2.4s ease-out infinite;
        }
        .api-dot.offline {
          background: ${STATUS_COLOR["offline"]};
        }
        .api-label {
          flex: 1;
          font-size: 13px;
          font-weight: 600;
          color: var(--mb-text);
        }
        .api-latency {
          font-size: 12px;
          font-weight: 600;
          font-variant-numeric: tabular-nums;
          color: var(--mb-muted);
          min-width: 44px;
          text-align: right;
        }
        .api-latency.online {
          color: ${STATUS_COLOR["online"]};
        }
        .api-latency.degraded {
          color: ${STATUS_COLOR["degraded"]};
        }
        .api-latency.offline {
          color: ${STATUS_COLOR["offline"]};
        }
        .api-rel {
          font-size: 11px;
          color: var(--mb-muted);
          font-variant-numeric: tabular-nums;
          flex-shrink: 0;
        }
        :global(.spark) {
          display: block;
          flex: 1;
          min-width: 0;
        }
        @keyframes pulse {
          0% {
            opacity: 0.55;
            transform: scale(0.75);
          }
          100% {
            opacity: 0;
            transform: scale(1.9);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .api-dot.online::after,
          .api-dot.degraded::after,
          .api-dot.checking::after {
            animation: none;
            display: none;
          }
        }
      `}</style>
    </div>
  );
}
