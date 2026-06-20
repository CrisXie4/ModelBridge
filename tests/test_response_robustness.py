# tests/test_response_robustness.py
"""Provider + JSON-RPC decode robustness against malformed-but-valid-JSON input.

A non-OpenAI-compatible endpoint or a misbehaving MCP server can return a 2xx
body / frame whose shape is wrong. Those must surface as actionable errors,
never as a raw KeyError/AttributeError/ValueError crash that escapes the layer
and (for MCP) takes down every sibling server's connect.
"""

from __future__ import annotations

import pytest

from modelbridge.mcp.protocol.messages import JsonRpcError
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers import ProviderError, get_provider


def _provider():
    entry = ModelEntry(
        name="f", provider=ProviderType.OPENAI_COMPATIBLE, model="f",
        base_url="http://x/v1",
    )
    return get_provider(entry)


@pytest.mark.parametrize("data", [
    {"choices": "err"},          # string, not a list
    {"choices": {"k": "v"}},     # dict, not a list
    {"choices": [None]},         # first element is null
    {"choices": [123]},          # first element is not an object
    {"choices": ["oops"]},       # first element is a string
    {},                          # no choices at all
])
def test_parse_chat_response_malformed_shape_raises_decode_error(data):
    p = _provider()
    with pytest.raises(ProviderError) as ei:
        p.parse_chat_response(data)
    assert getattr(ei.value, "error_type", None) == "decode"


def test_parse_chat_response_non_dict_message_is_tolerated():
    # choices[0] is a dict but message is the wrong type → empty content, no crash.
    p = _provider()
    resp = p.parse_chat_response({"choices": [{"message": "nope"}]})
    assert resp.content == ""


@pytest.mark.parametrize("code", ["oops", ["x"], {"a": 1}, None, 3.5])
def test_jsonrpc_error_tolerates_non_numeric_code(code):
    # A non-numeric error code from an untrusted server must not raise here —
    # that would escape the decode path and crash the whole connect loop.
    err = JsonRpcError.from_wire({"code": code, "message": "boom"})
    assert isinstance(err.code, int)
    assert err.message == "boom"


def test_jsonrpc_error_keeps_valid_code():
    err = JsonRpcError.from_wire({"code": -32601, "message": "method not found"})
    assert err.code == -32601
