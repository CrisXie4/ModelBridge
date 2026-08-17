"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Gauge,
  Pulse,
  PlugsConnected,
  Path,
  NotePencil,
  Sparkle,
  ShieldCheck,
  Coins,
} from "@phosphor-icons/react";

const NAV_GROUPS: {
  label: string;
  items: { href: string; label: string; Icon: typeof Gauge }[];
}[] = [
  {
    label: "监控",
    items: [
      { href: "/", label: "概览", Icon: Gauge },
      { href: "/activity", label: "Agent 活动", Icon: Pulse },
    ],
  },
  {
    label: "配置",
    items: [
      { href: "/models", label: "渠道 / 模型", Icon: PlugsConnected },
      { href: "/routing", label: "路由配置", Icon: Path },
      { href: "/prompts", label: "提示词", Icon: NotePencil },
      { href: "/skills", label: "Skills", Icon: Sparkle },
    ],
  },
  {
    label: "系统",
    items: [
      { href: "/doctor", label: "自检", Icon: ShieldCheck },
      { href: "/usage", label: "成本 / 缓存", Icon: Coins },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/icon.png" alt="ModelBridge" className="sidebar-logo" />
        <div className="sidebar-name">
          ModelBridge
          <span className="sidebar-tag">本地管理台</span>
        </div>
      </div>
      <nav className="sidebar-nav">
        {NAV_GROUPS.map((group) => (
          <div className="nav-group" key={group.label}>
            <div className="nav-group-label">{group.label}</div>
            {group.items.map(({ href, label, Icon }) => {
              const active =
                href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={active ? "nav-item active" : "nav-item"}
                >
                  <Icon size={16} weight={active ? "fill" : "regular"} />
                  <span>{label}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <ApiStatus />
      </div>
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
  checking: "#6d7a90",
  online: "#46c07a",
  degraded: "#e0b45c",
  offline: "#f0565c",
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
    </div>
  );
}
