"""微信 iLink Bot 通道的纯逻辑单测（不打网络）。

覆盖：
* client._json_response —— iLink 原生响应 / 旧式 {"success","data"} 包裹 / ret 错误码。
* client.poll_qrcode_status 的状态归一化 + credentials 提升（用打桩 transport）。
* creds 存取往返 + 权限。
* runner._split_text / _extract_text / 去重 / 风险判定。
* cli._render_qrcode 在 gbk 终端也不崩（渲染成 ▀ 半块）。
"""

from __future__ import annotations

import httpx
import pytest

from modelbridge.weixin.client import (
    WeixinClient,
    WeixinError,
    _json_response,
)


# ---------------------------------------------------------------------------
# _json_response
# ---------------------------------------------------------------------------

def _resp(payload, status=200):
    return httpx.Response(status_code=status, json=payload)


def test_json_response_ilink_native():
    # iLink 原生：ret=0，直接返回整个 payload。
    out = _json_response(_resp({"ret": 0, "qrcode": "abc"}), "op")
    assert out["qrcode"] == "abc"


def test_json_response_legacy_wrapper():
    # 旧式 {"success": true, "data": {...}} → 解包 data。
    out = _json_response(_resp({"success": True, "data": {"x": 1}}), "op")
    assert out == {"x": 1}


def test_json_response_ret_error_raises():
    with pytest.raises(WeixinError):
        _json_response(_resp({"ret": 1001, "errmsg": "boom"}), "op")


def test_json_response_success_false_raises():
    with pytest.raises(WeixinError):
        _json_response(_resp({"success": False}), "op")


def test_json_response_http_error_raises():
    with pytest.raises(WeixinError):
        _json_response(_resp({}, status=500), "op")


# ---------------------------------------------------------------------------
# 长轮询瞬断分类：服务端关连接/读超时 = transient(安静重连)，连不上 = 真故障(退避)
# ---------------------------------------------------------------------------

def _client_that_raises(exc):
    from modelbridge.weixin.client import WeixinClient

    def handler(request):
        raise exc

    return WeixinClient(
        bot_token="T",
        baseurl="https://ilink.example",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_getupdates_disconnect_is_transient():
    from modelbridge.weixin.client import WeixinTransientError

    client = _client_that_raises(
        httpx.RemoteProtocolError("Server disconnected without sending a response.")
    )
    with pytest.raises(WeixinTransientError):
        client.get_updates(timeout=1.0)


def test_getupdates_read_timeout_is_transient():
    from modelbridge.weixin.client import WeixinTransientError

    client = _client_that_raises(httpx.ReadTimeout("timed out"))
    with pytest.raises(WeixinTransientError):
        client.get_updates(timeout=1.0)


def test_getupdates_connect_error_is_real():
    from modelbridge.weixin.client import WeixinError, WeixinTransientError

    client = _client_that_raises(httpx.ConnectError("cannot connect"))
    with pytest.raises(WeixinError) as ei:
        client.get_updates(timeout=1.0)
    assert not isinstance(ei.value, WeixinTransientError)  # 真故障，走退避


# ---------------------------------------------------------------------------
# poll_qrcode_status —— 用 httpx MockTransport 打桩，验证状态归一化与凭据提升。
# ---------------------------------------------------------------------------

def test_poll_qrcode_status_confirmed_lifts_credentials(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "status": "confirmed",
                "bot_token": "tok",
                "ilink_bot_id": "bot",
                "credentials": {"ilink_user_id": "user"},
            },
        )

    def fake_get(url, **kw):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            return client.get(url, **{k: v for k, v in kw.items() if k != "timeout"})
        finally:
            client.close()

    monkeypatch.setattr(httpx, "get", fake_get)
    out = WeixinClient.poll_qrcode_status("qr123")
    assert out["status"] == "confirmed"
    creds = out["credentials"]
    # 顶层的 bot_token / ilink_bot_id 被提升进 credentials，且不覆盖已有 user_id。
    assert creds["bot_token"] == "tok"
    assert creds["ilink_bot_id"] == "bot"
    assert creds["ilink_user_id"] == "user"


def test_poll_qrcode_status_scanned_alias(monkeypatch):
    def fake_get(url, **kw):
        return httpx.Response(200, json={"ret": 0, "status": "scanned"})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = WeixinClient.poll_qrcode_status("qr123")
    assert out["status"] == "scaned"  # scanned → scaned 归一化


# ---------------------------------------------------------------------------
# creds 往返
# ---------------------------------------------------------------------------

def test_credentials_roundtrip(tmp_path, monkeypatch):
    from modelbridge.weixin import creds as creds_mod

    monkeypatch.setattr(creds_mod, "get_app_dir", lambda: tmp_path)
    assert creds_mod.load_credentials() is None
    creds_mod.save_credentials({"bot_token": "T", "baseurl": "https://x.example"})
    loaded = creds_mod.load_credentials()
    assert loaded["bot_token"] == "T"
    assert "logged_at" in loaded
    assert creds_mod.get_bot_token() == "T"
    assert creds_mod.get_baseurl() == "https://x.example"
    creds_mod.clear_credentials()
    assert creds_mod.load_credentials() is None


def test_get_baseurl_default(tmp_path, monkeypatch):
    from modelbridge.weixin import creds as creds_mod
    from modelbridge.weixin import ILINK_BASE

    monkeypatch.setattr(creds_mod, "get_app_dir", lambda: tmp_path)
    assert creds_mod.get_baseurl() == ILINK_BASE  # 无凭据 → 默认地址


# ---------------------------------------------------------------------------
# runner 文本处理
# ---------------------------------------------------------------------------

def test_split_text_respects_limit():
    from modelbridge.weixin.runner import WeixinGateway

    text = "。".join(["句子" * 20 for _ in range(50)])
    chunks = WeixinGateway._split_text(text, limit=200)
    assert len(chunks) > 1  # 够长，必然被切成多块
    assert all(len(c) <= 200 for c in chunks)
    # 不丢字：把分块和原文都去掉分隔/空白后，字符应当一致。
    def _core(s: str) -> str:
        return s.replace("。", "").replace("\n", "").replace(" ", "")
    assert _core("".join(chunks)) == _core(text)


def test_split_text_empty():
    from modelbridge.weixin.runner import WeixinGateway

    assert WeixinGateway._split_text("", limit=100) == ["（空回复）"]


def test_extract_text_from_item_list():
    from modelbridge.weixin.runner import WeixinGateway

    gw = WeixinGateway.__new__(WeixinGateway)  # 不跑 __init__，只测纯函数
    msg = {"item_list": [{"type": 1, "text_item": {"text": "  你好  "}}]}
    assert gw._extract_text(msg) == "你好"


def test_extract_text_voice_transcript():
    from modelbridge.weixin.runner import WeixinGateway

    gw = WeixinGateway.__new__(WeixinGateway)
    msg = {"item_list": [{"voice_item": {"transcript": "语音转写"}}]}
    assert gw._extract_text(msg) == "语音转写"


def test_is_risky_classification():
    from modelbridge.weixin.runner import _is_risky

    assert _is_risky("read_file", "read") is False
    assert _is_risky("list_dir", "ls") is False
    assert _is_risky("write_file", "write") is True
    assert _is_risky("run_bash", "rm -rf") is True


def test_duplicate_detection():
    from modelbridge.weixin.runner import WeixinGateway
    import threading

    gw = WeixinGateway.__new__(WeixinGateway)
    from collections import deque

    gw._seen_message_ids = set()
    gw._seen_message_order = deque(maxlen=1000)
    gw._seen_lock = threading.Lock()
    assert gw._is_duplicate("m1") is False
    assert gw._is_duplicate("m1") is True
    assert gw._is_duplicate("m2") is False


# ---------------------------------------------------------------------------
# cli._render_qrcode —— gbk 终端不崩，能画出来。
# ---------------------------------------------------------------------------

def test_render_qrcode_ok():
    from modelbridge.weixin.cli import _render_qrcode

    assert _render_qrcode("https://ilinkai.weixin.qq.com/login?qrcode=T") is True


def test_render_qrcode_empty_is_false():
    from modelbridge.weixin.cli import _render_qrcode

    assert _render_qrcode("") is False


# ---------------------------------------------------------------------------
# 并发/回复相关的修复：多段回复 + typing 续发
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.bot_id = "bot"
        self.sent = []
        self.typing = 0

    def send_message(self, *, to, text, context_token, msg_type="text"):
        self.sent.append((to, text, context_token))
        return {}

    def send_typing(self, *, context_token, status=1):
        self.typing += 1
        return True


def _bare_gateway():
    import logging
    from modelbridge.weixin.runner import WeixinGateway

    gw = WeixinGateway.__new__(WeixinGateway)
    gw.client = _FakeClient()
    gw._log = logging.getLogger("weixin-test")
    return gw


def test_send_reply_multichunk_same_token_and_prefix(monkeypatch):
    import modelbridge.weixin.runner as runner

    monkeypatch.setattr(runner.time, "sleep", lambda *_a, **_k: None)  # 别真等 0.4s
    gw = _bare_gateway()
    long = "。".join(["字" * 300 for _ in range(20)])  # 远超 2000
    gw._send_reply(to="u1", context_token="CTX", reply=long)

    sent = gw.client.sent
    assert len(sent) >= 2                      # 被切成多段
    assert all(ct == "CTX" for _, _, ct in sent)  # 协议：每段都带回同一 context_token
    assert sent[0][1].startswith("（1/")        # 多段带 (i/n) 前缀
    assert all(to == "u1" for to, _, _ in sent)


def test_send_reply_single_chunk_no_prefix():
    gw = _bare_gateway()
    gw._send_reply(to="u1", context_token="CTX", reply="短消息")
    assert gw.client.sent == [("u1", "短消息", "CTX")]  # 单段不加前缀


def test_typing_keepalive_pings_at_least_once():
    import time as _t

    gw = _bare_gateway()
    with gw._typing_keepalive("CTX"):
        _t.sleep(0.05)
    assert gw.client.typing >= 1  # 进入即先发一次


def test_per_user_workers_run_concurrently():
    """不同用户的长任务并行处理，不再互相阻塞（bug 1 修复回归）。"""
    import logging
    import threading
    import time as _t

    from modelbridge.weixin.runner import WeixinGateway

    gw = WeixinGateway.__new__(WeixinGateway)
    gw.client = _FakeClient()
    gw._log = logging.getLogger("weixin-test")
    gw._lock = threading.Lock()
    gw._stop = threading.Event()
    gw._sessions = {}
    gw.model_name = "m"
    gw._system_prompt = "sys"
    gw.idle_gc_seconds = 0

    started: dict[str, bool] = {}
    release = threading.Event()
    processed: list[str] = []
    plock = threading.Lock()

    def fake_handle(msg):
        u = msg["from_user_id"]
        started[u] = True
        release.wait(2.0)  # 卡住模拟长任务
        with plock:
            processed.append(u)

    gw._handle_message = fake_handle  # 覆盖，避免真跑 agent

    try:
        gw._dispatch({"from_user_id": "A"})
        gw._dispatch({"from_user_id": "B"})
        # 若是串行，B 会被 A 堵住永远进不来；并行则两个都能进入 handle。
        deadline = _t.time() + 3.0
        while _t.time() < deadline and not (started.get("A") and started.get("B")):
            _t.sleep(0.01)
        assert started.get("A") and started.get("B"), "两个用户没有并行处理"
        release.set()
        _t.sleep(0.2)
        with plock:
            assert set(processed) == {"A", "B"}
        assert len(gw._sessions) == 2  # 每个用户各一套会话+worker
    finally:
        release.set()
        gw.stop()
