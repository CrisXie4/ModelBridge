"use client";

import { useEffect, useState } from "react";
import { PlugsConnected } from "@phosphor-icons/react";
import { api, McpServerOut } from "@/lib/api";

export default function McpPage() {
  const [data, setData] = useState<{
    enabled: boolean;
    servers: McpServerOut[];
    note: string;
  } | null>(null);
  const [msg, setMsg] = useState("");
  const [msgErr, setMsgErr] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = () => {
    api<{ enabled: boolean; servers: McpServerOut[]; note: string }>("/mcp")
      .then(setData)
      .catch((e) => {
        setMsg(String((e as Error).message || e));
        setMsgErr(true);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const toggle = (serverId: string, enabled: boolean) => {
    api(`/mcp/${serverId}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    })
      .then(load)
      .catch((e) => {
        setMsg(String((e as Error).message || e));
        setMsgErr(true);
      });
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">MCP 服务器</h1>
          <p className="page-sub">
            管理连接外部工具的 MCP servers。这里的开关写入{" "}
            <code>config.yaml</code>（持久）；正在运行的会话里也可用{" "}
            <code>/mcp on|off &lt;id&gt;</code> 做临时启停。
          </p>
        </div>
        <div className="page-actions">
          <PlugsConnected size={22} />
        </div>
      </div>

      {loading ? (
        <div className="card skeleton">
          <div className="sk-line sk-lg" />
          <div className="sk-line sk-md" />
        </div>
      ) : !data || data.servers.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">
              <PlugsConnected size={20} />
            </div>
            <div className="empty-title">还没有配置任何 MCP server</div>
            <div className="empty-hint">
              编辑 <code>~/.modelbridge/config.yaml</code> 的 mcp.servers 段，
              重启 <code>mbridge</code> 后生效。
            </div>
          </div>
        </div>
      ) : (
        <>
          {msg ? (
            <div className={msgErr ? "card chat-error" : "card settings-msg ok"}>
              {msg}
            </div>
          ) : null}
          {!data.enabled ? (
            <div className="card dim" style={{ marginBottom: 12 }}>
              mcp 总开关当前为关闭（<code>mcp.enabled: false</code>），下面每台的
              enabled 只在重新打开总开关后起作用。
            </div>
          ) : null}
          <div className="card mcp-table">
            {data.servers.map((s) => (
              <div
                key={s.server_id}
                className={`mcp-row ${s.enabled ? "" : "is-disabled"}`}
              >
                <div className="mcp-main">
                  <span className="mono mcp-id">{s.server_id}</span>
                  <span className="badge badge-muted">{s.transport}</span>
                  <span className="badge badge-ok">{s.tool_policy}</span>
                </div>
                <div className="mcp-endpoint mono dim">
                  {s.command ? s.command : s.url}
                </div>
                <label
                  className="switch"
                  title={s.enabled ? "启用中 — 点击持久禁用" : "已禁用 — 点击启用"}
                >
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => toggle(s.server_id, e.target.checked)}
                  />
                  <span className="switch-slider" />
                </label>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
