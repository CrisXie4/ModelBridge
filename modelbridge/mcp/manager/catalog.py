"""Aggregated capability catalog across all connected servers.

Holds the union of every ready server's tools / resources / prompts, each
tagged with its origin server and a qualified name. Resolution maps a
qualified name back to ``(server_id, item)`` for dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..logging import mcp_logger
from ..protocol.types import MCPPrompt, MCPResource, MCPTool
from .naming import qualify, sanitize


@dataclass
class QualifiedTool:
    server_id: str
    qualified_name: str
    tool: MCPTool


@dataclass
class QualifiedResource:
    server_id: str
    resource: MCPResource


@dataclass
class QualifiedPrompt:
    server_id: str
    qualified_name: str
    prompt: MCPPrompt


@dataclass
class Catalog:
    tools: list[QualifiedTool] = field(default_factory=list)
    resources: list[QualifiedResource] = field(default_factory=list)
    prompts: list[QualifiedPrompt] = field(default_factory=list)

    # qualified_name -> (server_id, raw_name)
    _tool_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    _prompt_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    # sanitised server_id -> real server_id (to invert namespacing)
    _server_alias: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def add_server(
        self,
        server_id: str,
        *,
        tools: list[MCPTool],
        resources: list[MCPResource],
        prompts: list[MCPPrompt],
    ) -> None:
        self._server_alias[sanitize(server_id)] = server_id
        for t in tools:
            qn = qualify(server_id, t.name)
            if qn in self._tool_index:
                mcp_logger().warning("mcp.catalog tool name collision: %s", qn)
                continue
            self._tool_index[qn] = (server_id, t.name)
            self.tools.append(QualifiedTool(server_id=server_id, qualified_name=qn, tool=t))
        for r in resources:
            if any(existing.resource.uri == r.uri for existing in self.resources):
                mcp_logger().warning("mcp.catalog resource uri collision: %s", r.uri)
                continue
            self.resources.append(QualifiedResource(server_id=server_id, resource=r))
        for p in prompts:
            qn = qualify(server_id, p.name)
            if qn in self._prompt_index:
                mcp_logger().warning("mcp.catalog prompt name collision: %s", qn)
                continue
            self._prompt_index[qn] = (server_id, p.name)
            self.prompts.append(QualifiedPrompt(server_id=server_id, qualified_name=qn, prompt=p))

    # ------------------------------------------------------------------
    def remove_server(self, server_id: str) -> None:
        """Drop every capability that came from ``server_id`` (M5 hot refresh)."""
        self.tools = [t for t in self.tools if t.server_id != server_id]
        self.resources = [r for r in self.resources if r.server_id != server_id]
        self.prompts = [p for p in self.prompts if p.server_id != server_id]
        self._tool_index = {
            k: v for k, v in self._tool_index.items() if v[0] != server_id
        }
        self._prompt_index = {
            k: v for k, v in self._prompt_index.items() if v[0] != server_id
        }
        self._server_alias.pop(sanitize(server_id), None)

    # ------------------------------------------------------------------
    def resolve_tool(self, qualified_name: str) -> tuple[str, str] | None:
        return self._tool_index.get(qualified_name)

    def resolve_prompt(self, qualified_name: str) -> tuple[str, str] | None:
        return self._prompt_index.get(qualified_name)

    def find_resource(self, uri: str) -> QualifiedResource | None:
        for r in self.resources:
            if r.resource.uri == uri:
                return r
        return None

    def counts(self) -> dict[str, int]:
        return {
            "tools": len(self.tools),
            "resources": len(self.resources),
            "prompts": len(self.prompts),
        }


__all__ = [
    "Catalog",
    "QualifiedTool",
    "QualifiedResource",
    "QualifiedPrompt",
]
