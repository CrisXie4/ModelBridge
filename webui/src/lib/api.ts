// 统一的 API 客户端：所有页面都通过这里访问后端。
// 基址可通过环境变量 MBRIDGE_API 覆盖（默认同源 / 由 next.config.js rewrites 代理）。

const BASE = process.env.NEXT_PUBLIC_API || "";

export async function api<T = any>(
  path: string,
  opts: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || j.message || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ---- 类型定义 ----
export interface ModelOut {
  name: string;
  provider: string;
  type: string;
  base_url: string;
  model: string;
  api_key_env: string | null;
  has_api_key: boolean;
  level: string;
  capabilities: Record<string, boolean>;
  extra: Record<string, unknown>;
}

export interface SkillOut {
  name: string;
  description: string;
  scope: string;
  path: string;
  body?: string;
}

export interface ConfigOut {
  default_model: string | null;
  routing_mode: string;
  levels: {
    tiny: string | null;
    cheap: string | null;
    coder: string | null;
    agent: string | null;
    expert: string | null;
  };
  profiles: Record<string, any>;
  active_profile: string | null;
}

export interface PromptFiles {
  system: string;
  rules: string;
}

export interface CacheStats {
  hits: number;
  misses: number;
  saved_tokens: number;
  estimated_savings: number;
  billed_tokens: number;
  spend: number;
  currency: string;
  hit_rate: number;
  prefix_stability: number;
}

export interface DoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  hint: string | null;
}

// ---- 模型目录（/models/catalog）----

export interface CatalogEntry {
  model: string;
  provider: string;
  base_url: string;
  api_key_env: string | null;
  currency: string;
  input_per_1m: number;
  output_per_1m: number;
  cache_hit_input_per_1m: number | null;
  context_window: number;
  pricing_source: string;
  default_level: string;
  is_local: boolean;
}

// ---- 实时会话状态（/session/live）----

export interface TodoItem {
  id: number;
  content: string;
  status: "pending" | "in_progress" | "done";
  priority: "low" | "normal" | "high";
  created_at: string;
  updated_at: string;
}

export interface ContextStats {
  used_tokens: number;
  context_window: number;
  used_pct: number;
  free_tokens: number;
  message_count: number;
}

export interface SessionLive {
  online: boolean;
  status: "offline" | "idle" | "working";
  topic: string | null;
  model: string | null;
  cwd: string | null;
  context: ContextStats | null;
  todos: TodoItem[];
  todo_summary: {
    total: number;
    done: number;
    in_progress: number;
    pending: number;
  };
  updated_at: string | null;
  age_seconds: number | null;
  is_stale: boolean;
}
