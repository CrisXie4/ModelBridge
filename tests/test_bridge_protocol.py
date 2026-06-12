"""Stage 0 — Native Messaging framing round-trips and rejects bad frames."""

from __future__ import annotations

import io
import json
import struct

import pytest

from modelbridge.bridge import protocol as P

_LEN = struct.Struct("=I")


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return _LEN.pack(len(body)) + body


def test_encode_decode_round_trip():
    msg = {"type": "chat", "id": "1", "text": "你好 world"}
    wire = P.encode_message(msg)
    assert P.read_message(io.BytesIO(wire)) == msg


def test_read_multiple_messages_in_sequence():
    a = {"type": "chat", "id": "1", "text": "a"}
    b = {"type": "cancel", "id": "1"}
    stream = io.BytesIO(P.encode_message(a) + P.encode_message(b))
    assert P.read_message(stream) == a
    assert P.read_message(stream) == b
    assert P.read_message(stream) is None  # clean EOF


def test_clean_eof_returns_none():
    assert P.read_message(io.BytesIO(b"")) is None


def test_truncated_header_raises():
    with pytest.raises(P.ProtocolError):
        P.read_message(io.BytesIO(b"\x01\x02"))  # 2 of 4 length bytes


def test_truncated_body_raises():
    wire = _frame({"type": "chat"})
    with pytest.raises(P.ProtocolError):
        P.read_message(io.BytesIO(wire[:-3]))  # body cut short


def test_oversized_length_rejected():
    huge = _LEN.pack(P.MAX_MESSAGE_BYTES + 1)
    with pytest.raises(P.ProtocolError):
        P.read_message(io.BytesIO(huge))


def test_non_json_body_raises():
    body = b"not json"
    wire = _LEN.pack(len(body)) + body
    with pytest.raises(P.ProtocolError):
        P.read_message(io.BytesIO(wire))


def test_non_object_frame_raises():
    body = b"[1,2,3]"
    wire = _LEN.pack(len(body)) + body
    with pytest.raises(P.ProtocolError):
        P.read_message(io.BytesIO(wire))


def test_unicode_preserved_without_ascii_escaping():
    wire = P.encode_message({"text": "中文测试 🚀"})
    # ensure_ascii=False keeps multibyte UTF-8 compact on the wire
    assert "中文测试".encode("utf-8") in wire
    assert P.read_message(io.BytesIO(wire))["text"] == "中文测试 🚀"


def test_write_message_frames_and_is_readable_back():
    out = io.BytesIO()
    P.write_message(out, {"type": "done", "id": "9", "stopped": "stop"})
    out.seek(0)
    assert P.read_message(out) == {"type": "done", "id": "9", "stopped": "stop"}


def test_builders_have_expected_shape():
    assert P.ready(version="1.0", models=["m"], default_model="m")["type"] == P.T_READY
    assert P.delta(id="1", kind="content", text="x")["kind"] == "content"
    tc = P.tool_call(id="1", request_id="r1", name="click", args={"selector": "a"})
    assert tc["type"] == P.T_TOOL_CALL and tc["requestId"] == "r1"
    assert P.error(id=None, message="boom")["message"] == "boom"
