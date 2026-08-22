"""openai_tools() must be order-stable across MCP hot refresh.

The tools schema array is part of the provider prompt prefix (DeepSeek
implicit context caching). Unregister + re-register must not move an entry
to the tail, or every subsequent request re-pays the full prompt.
"""

from modelbridge.agent.tools.registry import ToolRegistry
from modelbridge.agent.tools.file_tools import ReadFileTool, ListDirTool, WriteFileTool, StrReplaceTool


def _make_registry():
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(WriteFileTool())
    reg.register(StrReplaceTool())
    return reg


def test_openai_tools_sorted_by_name():
    reg = _make_registry()
    names = [t["function"]["name"] for t in reg.openai_tools()]
    assert names == sorted(names)


def test_reregister_does_not_reorder_schema():
    reg = _make_registry()
    before = [t["function"]["name"] for t in reg.openai_tools()]

    # Simulate MCP hot refresh: drop a mid-list tool then add it back.
    tool = reg.unregister("read_file")
    assert tool is not None
    reg.register(tool)

    after = [t["function"]["name"] for t in reg.openai_tools()]
    assert after == before


def test_removed_and_readded_tool_keeps_prefix_position():
    reg = _make_registry()
    baseline = reg.openai_tools()

    tool = reg.unregister("list_dir")
    without = reg.openai_tools()
    # Removing one tool only removes its own slot; the rest keep relative order.
    kept = [t["function"]["name"] for t in without]
    assert kept == [n for n in sorted(b["function"]["name"] for b in baseline) if n != "list_dir"]

    reg.register(tool)
    assert reg.openai_tools() == baseline
