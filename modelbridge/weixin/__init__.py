"""微信通道 — 通过 iLink Bot API 接入微信，让 ModelBridge agent 在微信里收发消息。

公开接口：

* :mod:`modelbridge.weixin.client`  — iLink Bot HTTP 客户端（扫码登录 / 收发消息）。
* :mod:`modelbridge.weixin.creds`   — 凭据持久化（``~/.modelbridge/weixin.json``）。
* :mod:`modelbridge.weixin.runner`  — 微信网关主循环：长轮询拿消息 → 跑 agent turn → 回复。
* :mod:`modelbridge.weixin.cli`     — ``mbridge weixin login|status|logout`` + 顶层 ``gateway``。

设计原则：
  * CLI 功能（mbridge ask / init 等）在这个通道里**不可用**——只有微信对话。
  * 日志既写终端也写文件，用户能看到。
  * 复用 :func:`modelbridge.agent.loop.run_agent_turn`；不重写 agent 引擎。
"""

from __future__ import annotations

ILINK_BASE = "https://ilinkai.weixin.qq.com"
ILINK_CDN = "https://novac2c.cdn.weixin.qq.com/c2c"

__all__ = ["ILINK_BASE", "ILINK_CDN"]
