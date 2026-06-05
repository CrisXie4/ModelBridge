"""MCP domain data models: Tool / Resource / Prompt / Content.

Pure data classes decoded from a server's ``*/list`` and ``*/get`` results.
Kept deliberately small and tolerant — servers may add fields we ignore, and
omit optional ones we default. No IO here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "MCPTool":
        schema = raw.get("inputSchema") or raw.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        return cls(
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            input_schema=schema,
        )


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@dataclass
class MCPResource:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "MCPResource":
        return cls(
            uri=str(raw.get("uri") or ""),
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            mime_type=raw.get("mimeType") or raw.get("mime_type"),
        )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@dataclass
class PromptArgument:
    name: str
    description: str = ""
    required: bool = False

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "PromptArgument":
        return cls(
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            required=bool(raw.get("required", False)),
        )


@dataclass
class MCPPrompt:
    name: str
    description: str = ""
    arguments: list[PromptArgument] = field(default_factory=list)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "MCPPrompt":
        args_raw = raw.get("arguments") or []
        args = [PromptArgument.from_wire(a) for a in args_raw if isinstance(a, dict)]
        return cls(
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            arguments=args,
        )


# ---------------------------------------------------------------------------
# Content blocks (returned by tools/call, resources/read, prompts/get)
# ---------------------------------------------------------------------------

@dataclass
class ContentBlock:
    """A single content item. ``type`` is one of text|image|audio|resource.

    We keep ``text`` for the common case and stash everything else in ``data``
    so a renderer can decide how to surface non-text blocks (image/audio are
    described, not inlined, for the synchronous CLI).
    """

    type: str
    text: str | None = None
    mime_type: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "ContentBlock":
        ctype = str(raw.get("type") or "text")
        text: str | None = None
        if ctype == "text":
            text = str(raw.get("text") or "")
        elif ctype == "resource":
            # Embedded resource: {resource: {uri, text?, blob?, mimeType?}}
            res = raw.get("resource") or {}
            if isinstance(res, dict) and isinstance(res.get("text"), str):
                text = res["text"]
        return cls(
            type=ctype,
            text=text,
            mime_type=raw.get("mimeType") or raw.get("mime_type"),
            data=raw,
        )

    def as_text(self) -> str:
        """Flatten to a string for the synchronous text-only agent loop."""
        if self.text is not None:
            return self.text
        if self.type in ("image", "audio"):
            mime = self.mime_type or "?"
            return f"[{self.type} content · {mime} · 未内联]"
        if self.type == "resource":
            res = self.data.get("resource") or {}
            uri = res.get("uri") if isinstance(res, dict) else None
            return f"[embedded resource · {uri or '?'} · 二进制未内联]"
        return f"[{self.type} content]"


@dataclass
class CallToolResult:
    content: list[ContentBlock] = field(default_factory=list)
    is_error: bool = False
    structured: dict[str, Any] | None = None

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "CallToolResult":
        blocks_raw = raw.get("content") or []
        blocks = [ContentBlock.from_wire(b) for b in blocks_raw if isinstance(b, dict)]
        structured = raw.get("structuredContent")
        return cls(
            content=blocks,
            is_error=bool(raw.get("isError", False)),
            structured=structured if isinstance(structured, dict) else None,
        )

    def joined_text(self, *, sep: str = "\n") -> str:
        return sep.join(b.as_text() for b in self.content)


@dataclass
class ReadResourceResult:
    contents: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "ReadResourceResult":
        items_raw = raw.get("contents") or []
        items: list[ContentBlock] = []
        for it in items_raw:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("text"), str):
                items.append(
                    ContentBlock(type="text", text=it["text"],
                                 mime_type=it.get("mimeType"), data=it)
                )
            else:
                items.append(
                    ContentBlock(type="resource", mime_type=it.get("mimeType"), data=it)
                )
        return cls(contents=items)

    def joined_text(self, *, sep: str = "\n") -> str:
        return sep.join(b.as_text() for b in self.contents)


@dataclass
class PromptMessage:
    role: str
    content: str

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "PromptMessage":
        c = raw.get("content")
        if isinstance(c, dict):
            text = ContentBlock.from_wire(c).as_text()
        elif isinstance(c, list):
            text = "\n".join(
                ContentBlock.from_wire(b).as_text() for b in c if isinstance(b, dict)
            )
        else:
            text = str(c or "")
        return cls(role=str(raw.get("role") or "user"), content=text)


@dataclass
class GetPromptResult:
    description: str = ""
    messages: list[PromptMessage] = field(default_factory=list)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "GetPromptResult":
        msgs_raw = raw.get("messages") or []
        msgs = [PromptMessage.from_wire(m) for m in msgs_raw if isinstance(m, dict)]
        return cls(description=str(raw.get("description") or ""), messages=msgs)


__all__ = [
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
