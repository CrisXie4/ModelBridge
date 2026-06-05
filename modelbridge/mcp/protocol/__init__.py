"""MCP protocol layer: pure data models + JSON-RPC codec, no IO."""

from __future__ import annotations

from .capabilities import (
    KNOWN_VERSIONS,
    PROTOCOL_VERSION,
    HandshakeResult,
    ServerCapabilities,
    client_capabilities,
    client_info,
)
from .codec import decode_message, encode_line
from .messages import (
    IncomingMessage,
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
)
from .types import (
    CallToolResult,
    ContentBlock,
    GetPromptResult,
    MCPPrompt,
    MCPResource,
    MCPTool,
    PromptArgument,
    PromptMessage,
    ReadResourceResult,
)

__all__ = [
    "PROTOCOL_VERSION",
    "KNOWN_VERSIONS",
    "HandshakeResult",
    "ServerCapabilities",
    "client_capabilities",
    "client_info",
    "encode_line",
    "decode_message",
    "IncomingMessage",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcNotification",
    "JsonRpcError",
    "MCPTool",
    "MCPResource",
    "MCPPrompt",
    "PromptArgument",
    "ContentBlock",
    "CallToolResult",
    "ReadResourceResult",
    "PromptMessage",
    "GetPromptResult",
]
