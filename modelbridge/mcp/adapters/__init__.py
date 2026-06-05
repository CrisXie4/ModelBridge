"""MCP adapter layer: bridge remote capabilities onto ModelBridge abstractions."""

from __future__ import annotations

from .prompt_adapter import MCPPromptAdapter
from .resource_provider import MCPResourceProvider
from .tool_adapter import MCPToolAdapter

__all__ = ["MCPToolAdapter", "MCPResourceProvider", "MCPPromptAdapter"]
