"""微信网关主循环 — 长轮询拉微信消息，喂给 ModelBridge agent，回复发回微信。

工具集与 CLI REPL 等价：
  * read_file / list_dir / write_file / str_replace / run_bash
  * 浏览器侧边栏工具 (read_page / click / fill / navigate…) — 需 ``mbridge bridge on``
  * 电脑控制 (mouse / keyboard / screenshot / inject_js)
  * subagent / skills / MCP
审批策略：微信通道没有交互 UI，所以走三种模式：
  * ``--auto`` (默认) — LLM 安全判断器先看；安全直接放行，不安全则拒绝并回告用户
  * ``--yes``      — 全部无条件放行 (等价 CLI ``--yes``)
  * ``--reject-unsafe`` — unsafe 操作直接拒绝、写进 abort 说明 (最保守)
日志既写文件也写到 stderr，用户能从 ``mbridge gateway`` 启动的终端看见实时进度。
"""

from __future__ import annotations

import base64
import queue
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ..agent.context import AgentContext, ApprovalDecision
from ..agent.loop import run_agent_turn
from ..agent.security import PathPolicy
from ..agent.session import Session
from ..agent.tools import build_default_registry
from ..client import resolve_model_name
from ..schemas import text_of
from ..utils import get_logger
from .creds import load_credentials
from .client import WeixinClient, WeixinError, WeixinTransientError


# ---------------------------------------------------------------------------
# 默认 system prompt —— 与 cli._default_system_prompt 保持同构，但微信通道里
# 我们不嵌入「在 cwd 执行」这类 CLI 语义；且提示 agent「你在微信里跟人对话」。
# ---------------------------------------------------------------------------
def _default_system_prompt(*, allow_bash: bool) -> str:
    bash_line = "" if not allow_bash else (
        "- run_bash(command): 在 cwd 里执行 shell 命令 (默认 30 秒超时)。\n"
    )
    return (
        "你是 ModelBridge 在微信里的助手。你能像 CLI 一样读写文件、执行命令、操作电脑。\n"
        "回答用简洁自然的中文，像真人同事一样直接。不要 Markdown，微信渲染不出来——用纯文本和缩进。\n\n"
        "可用工具:\n"
        "- read_file(path): 读取项目内文件。\n"
        "- list_dir(path): 列目录条目。\n"
        "- write_file(path, content): 创建/覆盖文件。\n"
        "- str_replace(path, old_str, new_str): 精确替换 (old_str 须唯一)。\n"
        f"{bash_line}"
        "- 浏览器侧边栏工具 (read_page / click / fill / navigate …)：需用户先 `mbridge bridge on`。\n"
        "- 电脑控制 (mouse / keyboard / screenshot / inject_js)。\n"
        "- spawn_subagent / skills / MCP。\n\n"
        "原则:\n"
        "1. 改文件前先 read_file 确认上下文再动手；不要凭空写代码。\n"
        "2. 优先 str_replace，避免覆盖未读过的内容。\n"
        "3. 工具失败就分析、调参重试，不要死循环。\n"
        "4. 危险命令 (删库/关机/远程写入) 用户没明确批准前不要执行；直接拒绝并说理由。\n"
    )


@dataclass
class _UserSession:
    session: Session
    last_active: float = field(default_factory=time.time)
    # 复用 REPL 的「批准一次 = 本会话继续自动批准」语义
    _auto_approved: set[str] = field(default_factory=set)
    # 每个微信用户一条独立收件队列 + 一个 worker 线程：同一用户的消息串行处理
    # （保证会话历史不串），不同用户并行，互不阻塞。None 是关停哨兵。
    inbox: "queue.Queue[dict[str, Any] | None]" = field(default_factory=queue.Queue)
    worker: threading.Thread | None = None


class WeixinGateway:
    """微信 → agent → 微信 网关进程体 (工具集 = CLI)。"""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        allow_bash: bool = True,        # 微信通道用户期望「能控制电脑」
        approval_mode: str = "auto",   # auto | yes | reject-unsafe
        cwd: Path | None = None,
        max_iters: int = 20,
        idle_gc_seconds: int = 1800,
    ) -> None:
        self.model_name = model_name
        self.allow_bash = allow_bash
        self.approval_mode = approval_mode
        self.cwd = (cwd or Path.cwd()).resolve()
        self.max_iters = max_iters
        self.idle_gc_seconds = idle_gc_seconds
        self._sessions: dict[str, _UserSession] = {}
        self._lock = threading.Lock()
        # 去重结构被多个 user worker 线程共享，单独一把锁保护。
        self._seen_lock = threading.Lock()
        self._stop = threading.Event()
        self._log = get_logger().getChild("weixin.gateway")
        self.client: WeixinClient | None = None
        # MCP manager — 连接已配置的 MCP 服务端；shutdown 时关掉
        self._mcp_manager: Any = None
        self._browser_bridge: Any = None
        self._registry: Any = None
        self._system_prompt = _default_system_prompt(allow_bash=self.allow_bash)
        self._model_is_vision = False
        self._seen_message_ids: set[str] = set()
        self._seen_message_order: deque[str] = deque(maxlen=1000)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        # 叫醒并停掉所有 user worker（塞哨兵；worker 也会看 _stop）。
        try:
            with self._lock:
                sessions = list(self._sessions.values())
            for us in sessions:
                us.inbox.put(None)
        except Exception:
            pass
        # 主动清理
        try:
            if self._mcp_manager is not None:
                self._mcp_manager.shutdown()
        except Exception:
            pass
        try:
            if self._browser_bridge is not None:
                self._browser_bridge.close()
        except Exception:
            pass
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass

    def _ensure_console_logging(self) -> None:
        """把 mbridge 日志同时输出到 stderr，前台跑 gateway 的用户才看得到实时活动。

        默认 ``get_logger()`` 只挂了写文件的 handler（~/.modelbridge/logs/mbridge.log），
        所以前台只看得到启动面板然后一片安静。这里给根 logger 补一个终端 handler。
        """
        import logging
        import sys

        base = get_logger()  # mbridge 命名 logger（文件 handler 挂在它上面）
        for handler in base.handlers:
            if getattr(handler, "_mb_gateway_console", False):
                return  # 已经加过，别重复
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.INFO)
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        stream._mb_gateway_console = True  # type: ignore[attr-defined]
        base.addHandler(stream)
        base.setLevel(logging.INFO)

    def run(self) -> int:
        self._ensure_console_logging()
        self._log.info("网关启动中…（日志同时写 ~/.modelbridge/logs/mbridge.log）")
        creds = load_credentials()
        if not creds:
            self._log.error("未登录：先运行 mbridge weixin login 扫码。")
            return 2
        bot_token = creds.get("bot_token", "")
        if not bot_token:
            self._log.error("weixin.json 缺少 bot_token。")
            return 2
        self.client = WeixinClient(
            bot_token=bot_token,
            bot_id=creds.get("ilink_bot_id"),
            user_id=creds.get("ilink_user_id"),
            baseurl=creds.get("baseurl"),
        )

        resolved_model = resolve_model_name(self.model_name)
        self.model_name = resolved_model
        try:
            from ..client import find_model

            entry = find_model(resolved_model)
            self._model_is_vision = bool(
                getattr(getattr(entry, "capabilities", None), "vision", False)
            )
        except Exception:
            self._model_is_vision = False

        self._registry = self._build_registry()
        try:
            from ..skills.wiring import wire_skills

            self._system_prompt = wire_skills(
                self._registry,
                self._system_prompt,
                project_path=self.cwd,
            )
        except Exception as exc:
            self._log.warning("skills 加载跳过: %s", exc)

        try:
            from ..bridge.control import RemoteBrowserBridge

            self._browser_bridge = RemoteBrowserBridge()
        except Exception as exc:
            self._log.warning("browser bridge 未连接: %s", exc)
            self._browser_bridge = None

        self._log.info(
            "微信网关启动: model=%s, bot_id=%s, baseurl=%s, allow_bash=%s, approval=%s",
            resolved_model,
            self.client.bot_id,
            self.client.baseurl,
            self.allow_bash,
            self.approval_mode,
        )

        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    updates = self.client.get_updates(timeout=45.0)
                    failures = 0
                except WeixinTransientError as exc:
                    # 长轮询被服务端正常关闭 / 读超时：立刻重连，不当故障、不刷 WARNING。
                    self._log.debug("长轮询重连: %s", exc)
                    self._stop.wait(0.5)
                    continue
                except WeixinError as exc:
                    failures += 1
                    delay = min(30.0, max(1.0, 2 ** min(failures - 1, 5)))
                    self._log.warning("getupdates 失败: %s (%.0fs 后重试)", exc, delay)
                    self._stop.wait(delay)
                    continue
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    delay = min(30.0, max(2.0, 2 ** min(failures - 1, 5)))
                    self._log.warning(
                        "getupdates 未知异常: %s: %s (%.0fs 后重试)",
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    self._stop.wait(delay)
                    continue

                if updates:
                    self._log.info("收到 %d 条消息", len(updates))
                for msg in updates:
                    try:
                        self._dispatch(msg)
                    except Exception:
                        self._log.error("分发消息出错:\n%s", traceback.format_exc())

                self._gc_idle_sessions()
        finally:
            self.stop()

        self._log.info("微信网关已停止")
        return 0

    def _build_registry(self):
        """与 CLI 等价的工具集：默认工具 + bash + browser + computer + subagent + skills + MCP。"""
        registry = build_default_registry(include_bash=self.allow_bash)

        # 浏览器侧边栏工具 (write 工具会触发审批 → 走我们的 approve 回调)
        try:
            from ..agent.tools.browser_tools import build_browser_registry
            for tool in build_browser_registry(include_write=True).tools.values():
                registry.register(tool)
        except Exception as e:
            self._log.warning("browser 工具加载失败: %s", e)

        # 电脑控制 (鼠标/键盘/截图/inject_js)
        try:
            from ..agent.tools.computer_control_tools import build_computer_registry
            for tool in build_computer_registry().tools.values():
                registry.register(tool)
        except Exception as e:
            self._log.warning("computer 控制工具加载失败: %s", e)

        # spawn_subagent
        try:
            from ..agent.tools.subagent_tool import SpawnSubagentTool
            registry.register(SpawnSubagentTool())
        except Exception as e:
            self._log.warning("spawn_subagent 工具加载失败: %s", e)

        # MCP — 一次性连接，挂工具进 registry
        try:
            from ..mcp.manager import MCPManager
            from ..config import load_app_config
            cfg = load_app_config()
            if getattr(cfg, "mcp_servers", None):
                mgr = MCPManager()
                mgr.connect_all(cfg.mcp_servers)
                mgr.register_tools(registry)
                self._mcp_manager = mgr
        except Exception as e:
            self._log.warning("MCP 初始化跳过: %s", e)

        return registry

    def _build_session(self, *, model_name: str) -> _UserSession:
        """为一个微信用户创建独立的 ModelBridge 对话历史。"""
        session = Session(model_name=model_name)
        session.add_system(self._system_prompt)
        return _UserSession(session=session)

    def _make_approval(self, sess: _UserSession) -> Callable:
        """审批回调：微信通道没有互动 UI，按 approval_mode 走。

        - ``yes``          : 无条件 YES (同 CLI ``--yes``)
        - ``auto`` (默认)  : LLM 安全判断器先看；安全 → YES，不安全 → NO 并附理由
        - ``reject-unsafe``: 任何高风险工具直接 NO（最保守，不让 LLM 判）
        ALWAYS 决策仍按工具组记忆到 sess._auto_approved，同 REPL。
        """
        log = self._log

        def approve(*, tool: str, summary: str, detail: str = "",
                    save_pattern: str | None = None, auto: bool = False) -> ApprovalDecision:
            key = tool
            if key in sess._auto_approved:
                return ApprovalDecision.YES
            if self.approval_mode == "yes":
                log.info("[approve] YES (--yes): %s | %s", tool, summary)
                return ApprovalDecision.YES
            # auto / reject-unsafe：判定风险
            risky = _is_risky(tool, summary)
            if self.approval_mode == "reject-unsafe" and risky:
                log.warning("[approve] REJECT (reject-unsafe): %s | %s", tool, summary)
                return ApprovalDecision.NO
            if self.approval_mode == "auto":
                # 先用规则快速放过明显安全的；risky 才花一次 LLM
                if not risky:
                    log.info("[approve] AUTO (rule safe): %s | %s", tool, summary)
                    return ApprovalDecision.YES
                ok, reason = _llm_safety_judge(tool=tool, summary=summary, detail=detail)
                if ok:
                    log.info("[approve] AUTO (llm safe): %s | %s | %s", tool, summary, reason)
                    sess._auto_approved.add(key)
                    return ApprovalDecision.YES
                log.warning("[approve] REJECT (llm unsafe): %s | %s | %s", tool, summary, reason)
                return ApprovalDecision.NO
            return ApprovalDecision.YES

        return approve

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    def _peek_user_id(self, msg: dict[str, Any]) -> str:
        """只取发件人 user_id，用来把消息路由到对应 worker（不做重活）。"""
        return str(
            msg.get("from_user_id")
            or msg.get("from")
            or msg.get("user_id")
            or msg.get("sender")
            or ""
        )

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """把一条消息投递到发件用户的收件队列；worker 线程会串行消费。

        并发模型：一个用户一个 worker → 同一用户消息按序处理（会话历史不串），
        不同用户并行。避免了「一个长任务(最长 600s)把整条主循环和所有其他用户
        全堵死」的问题。
        """
        if not isinstance(msg, dict) or self.client is None:
            return
        user_id = self._peek_user_id(msg)
        if not user_id or user_id == self.client.bot_id:
            return
        us = self._get_session(user_id)
        us.inbox.put(msg)

    def _user_worker(self, user_id: str, us: _UserSession) -> None:
        """某个微信用户的专属处理线程：从收件队列里逐条取消息处理。"""
        while not self._stop.is_set():
            try:
                msg = us.inbox.get(timeout=1.0)
            except queue.Empty:
                continue
            if msg is None:  # 关停哨兵
                break
            try:
                self._handle_message(msg)
            except Exception:
                self._log.error(
                    "[worker %s] 处理消息出错:\n%s", user_id, traceback.format_exc()
                )

    @contextmanager
    def _typing_keepalive(self, context_token: str) -> Iterator[None]:
        """开跑前发一次「正在输入」，并在整个 agent turn 期间每 4 秒续一次。

        微信的 typing 状态只维持几秒，长任务跑几分钟时「对方正在输入」早没了；
        用一个后台线程周期性续发，直到本轮结束。
        """
        stop = threading.Event()

        def _loop() -> None:
            # 先立刻发一次，之后每 4 秒续；stop 被 set 时立即退出。
            while True:
                try:
                    if self.client is not None:
                        self.client.send_typing(context_token=context_token)
                except Exception:  # noqa: BLE001 - typing 失败无关紧要
                    pass
                if stop.wait(4.0):
                    break

        thread = threading.Thread(
            target=_loop, name="weixin-typing", daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1.0)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict) or self.client is None:
            return
        message_id = str(
            msg.get("message_id") or msg.get("msg_id") or msg.get("client_id") or ""
        )
        if message_id and self._is_duplicate(message_id):
            self._log.debug("跳过重复消息: %s", message_id)
            return

        user_id = str(
            msg.get("from_user_id")
            or msg.get("from")
            or msg.get("user_id")
            or msg.get("sender")
            or ""
        )
        if not user_id or user_id == self.client.bot_id:
            return
        context_token = str(
            msg.get("context_token")
            or msg.get("ContextToken")
            or msg.get("context")
            or ""
        )
        if not context_token:
            self._log.warning("跳过缺少 context_token 的消息: %s", msg)
            return

        text = self._extract_text(msg)
        images = self._extract_images(msg)
        attachment_notes = self._extract_attachment_notes(msg)
        if attachment_notes:
            text = "\n".join(part for part in (text, *attachment_notes) if part)
        if images and not text:
            text = "请查看并处理我发送的图片。"
        if not text:
            self._send_reply(
                to=user_id,
                context_token=context_token,
                reply="暂时只支持文本、图片以及带文字转写的语音消息。",
            )
            return

        self._log.info("[%s] %s", user_id, text[:200])
        command_reply = self._handle_command(user_id, text)
        if command_reply is not None:
            self._send_reply(to=user_id, context_token=context_token, reply=command_reply)
            return

        sess = self._get_session(user_id)
        registry = self._registry or self._build_registry()

        try:
            policy = PathPolicy.from_config(extra_cwd=self.cwd)
        except Exception:
            policy = PathPolicy(allowed_dirs=[self.cwd])
        ctx = AgentContext(
            policy=policy,
            cwd=self.cwd,
            approve=self._make_approval(sess),
            allow_bash=self.allow_bash,
            model_is_vision=self._model_is_vision,
        )
        ctx.browser_bridge = self._browser_bridge

        try:
            sess.session.add_user(text, images=images or None)
            # 整个 turn 期间持续「正在输入」，任务再长用户也看得到在处理。
            with self._typing_keepalive(context_token):
                result = run_agent_turn(
                    session=sess.session,
                    ctx=ctx,
                    registry=registry,
                    # run() 启动时已把 self.model_name resolve 成真实模型名；
                    # `or` 分支只为满足类型检查（self.model_name 声明为 str | None）。
                    model_name=self.model_name or resolve_model_name(None),
                    timeout=600.0,
                    max_iters=self.max_iters,
                    stream=True,
                    on_content_delta=lambda _delta: None,
                )
        except Exception:
            self._log.error("agent turn 异常:\n%s", traceback.format_exc())
            reply = "内部处理出错，详情已写入 ModelBridge 日志。"
        else:
            final_response = result.final_response
            reply = text_of(final_response.content) if final_response else ""
            if not reply:
                reply = "模型没有返回可显示的内容。"

        self._send_reply(to=user_id, context_token=context_token, reply=reply)

    def _handle_command(self, user_id: str, text: str) -> str | None:
        command = text.strip().lower()
        if command in {"/clear", "/new", "清空对话", "新对话"}:
            # 只重置会话历史，保留该用户的 worker + 收件队列（别 pop 掉 session，
            # 否则 worker 线程会被孤立、泄漏）。
            with self._lock:
                us = self._sessions.get(user_id)
            if us is not None:
                fresh = Session(model_name=self.model_name or "default")
                fresh.add_system(self._system_prompt)
                us.session = fresh
                us._auto_approved.clear()
            return "已清空当前微信会话的 ModelBridge 上下文。"
        if command in {"/status", "状态"}:
            return (
                f"ModelBridge 微信网关运行中\n"
                f"模型：{self.model_name}\n"
                f"工作区：{self.cwd}\n"
                f"bash：{'允许' if self.allow_bash else '禁用'}\n"
                f"审批模式：{self.approval_mode}"
            )
        if command in {"/help", "帮助"}:
            return (
                "直接发送问题即可使用 ModelBridge。\n"
                "/new 或 /clear：开始新对话\n"
                "/status：查看当前模型和工作区\n"
                "/help：显示本帮助"
            )
        return None

    def _send_reply(self, *, to: str, context_token: str, reply: str) -> None:
        if self.client is None:
            return
        chunks = self._split_text(reply, limit=2000)
        total = len(chunks)
        # 协议要求：每条回复都原样带回同一个 context_token（不是单次消费），所以多
        # 段回复复用它是对的。多段时加 (i/n) 前缀，万一微信不保序用户也能拼回；段间
        # 停一小下以保序、避免限流。
        for idx, chunk in enumerate(chunks, 1):
            body = chunk if total == 1 else f"（{idx}/{total}）\n{chunk}"
            try:
                self.client.send_message(
                    to=to,
                    text=body,
                    context_token=context_token,
                    msg_type="text",
                )
                self._log.info("[%s] -> %s", to, body[:200])
            except WeixinError as exc:
                self._log.error("send_message 失败: %s | reply=%s", exc, chunk[:200])
                break
            if idx < total:
                time.sleep(0.4)

    @staticmethod
    def _split_text(text: str, *, limit: int) -> list[str]:
        text = (text or "").strip()
        if not text:
            return ["（空回复）"]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            cut = remaining.rfind("\n", 0, limit + 1)
            if cut < limit // 2:
                cut = remaining.rfind("。", 0, limit + 1)
                if cut >= limit // 2:
                    cut += 1
            if cut < limit // 2:
                cut = limit
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _is_duplicate(self, message_id: str) -> bool:
        # 多个 user worker 并发调用，加锁保护共享的 seen 集合/队列。
        with self._seen_lock:
            if message_id in self._seen_message_ids:
                return True
            if len(self._seen_message_order) == self._seen_message_order.maxlen:
                oldest = self._seen_message_order[0]
                self._seen_message_ids.discard(oldest)
            self._seen_message_order.append(message_id)
            self._seen_message_ids.add(message_id)
            return False

    def _extract_text(self, msg: dict[str, Any]) -> str:
        parts: list[str] = []
        items = msg.get("item_list")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                text_item = item.get("text_item")
                if isinstance(text_item, dict) and isinstance(text_item.get("text"), str):
                    parts.append(text_item["text"].strip())
                voice_item = item.get("voice_item")
                if isinstance(voice_item, dict):
                    transcript = voice_item.get("text") or voice_item.get("transcript")
                    if isinstance(transcript, str) and transcript.strip():
                        parts.append(transcript.strip())
        if parts:
            return "\n".join(part for part in parts if part)

        for key in ("text", "Text", "content", "Content", "message", "Message"):
            value = msg.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for child_key in ("text", "content"):
                    child = value.get(child_key)
                    if isinstance(child, str) and child.strip():
                        return child.strip()
        return ""

    def _extract_images(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        if self.client is None:
            return []
        images: list[dict[str, Any]] = []
        items = msg.get("item_list")
        if not isinstance(items, list):
            return images
        for item in items:
            if not isinstance(item, dict):
                continue
            image_item = item.get("image_item")
            if not isinstance(image_item, dict):
                continue
            media = image_item.get("media")
            if not isinstance(media, dict):
                continue
            try:
                data = self.client.download_media(media)
            except WeixinError as exc:
                self._log.warning("微信图片下载失败: %s", exc)
                continue
            mime = self.client.guess_mime(str(image_item.get("filename") or ""), data)
            if not mime.startswith("image/"):
                self._log.warning("收到的 image_item 不是可识别图片: %s", mime)
                continue
            encoded = base64.b64encode(data).decode("ascii")
            images.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
            )
        return images

    @staticmethod
    def _extract_attachment_notes(msg: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        items = msg.get("item_list")
        if not isinstance(items, list):
            return notes
        for item in items:
            if not isinstance(item, dict):
                continue
            file_item = item.get("file_item")
            if isinstance(file_item, dict):
                name = file_item.get("file_name") or file_item.get("filename") or "未命名文件"
                notes.append(f"[微信附件：{name}；当前未自动下载文件附件]")
            video_item = item.get("video_item")
            if isinstance(video_item, dict):
                notes.append("[微信视频；当前未自动提交给模型]")
        return notes

    # ------------------------------------------------------------------
    # 会话缓存 + GC
    # ------------------------------------------------------------------

    def _get_session(self, user_id: str) -> _UserSession:
        with self._lock:
            sess = self._sessions.get(user_id)
            if sess is None:
                sess = self._build_session(model_name=self.model_name or "default")
                self._sessions[user_id] = sess
                # 起这个用户的专属 worker 线程（daemon，进程退出即回收）。
                sess.worker = threading.Thread(
                    target=self._user_worker,
                    args=(user_id, sess),
                    name=f"weixin-user-{user_id[:8]}",
                    daemon=True,
                )
                sess.worker.start()
                self._log.info("新建会话+worker: user_id=%s", user_id)
            sess.last_active = time.time()
            return sess

    def _gc_idle_sessions(self) -> None:
        if self.idle_gc_seconds <= 0:
            return
        now = time.time()
        with self._lock:
            stale = [uid for uid, s in self._sessions.items()
                     if now - s.last_active > self.idle_gc_seconds]
            removed = [self._sessions.pop(uid) for uid in stale]
        for s in removed:
            s.inbox.put(None)  # 让空闲 worker 退出
        if stale:
            self._log.debug("回收 %d 个空闲会话", len(stale))


# ---------------------------------------------------------------------------
# 风险判定 + LLM 安全判断 (auto mode 用)
# ---------------------------------------------------------------------------

# 明显安全/不需要审批的工具 (只读、列目录等)
_SAFE_TOOLS = {"read_file", "list_dir", "read_page", "get_selection",
               "query_dom", "extract", "screenshot", "get_page_text"}

# 规则判定：不是 SAFE_TOOLS 且属于改文件/系统/远程类即视为需要 LLM 判断
def _is_risky(tool: str, summary: str) -> bool:
    if tool in _SAFE_TOOLS:
        return False
    write_like = any(k in tool for k in ("write", "str_replace", "bash", "exec",
                                          "remove", "delete", "navigate", "click",
                                          "fill", "inject", "subagent"))
    return write_like


def _llm_safety_judge(*, tool: str, summary: str, detail: str) -> tuple[bool, str]:
    """跟 CLI 的 _auto_judge 同款逻辑：调一个轻量模型判断操作安全性。"""
    try:
        from ..providers import get_provider
        from ..config import load_app_config, load_models_file
        from ..client import find_model
        from ..schemas import ChatMessage, ChatRequest

        prompt = (
            f"判断以下操作是否安全。分析后先给出理由，再给出结论「安全」或「不安全」。\n"
            f"工具: {tool}\n操作: {summary}\n详情: {detail[:300]}"
        )
        cfg = load_app_config()
        models_file = load_models_file()
        tiny = None
        for m in models_file.models:
            if getattr(m, "level", None) in ("tiny", "cheap") or "tiny" in m.name.lower():
                tiny = m
                break
        if tiny is None and cfg.default_model:
            tiny = find_model(cfg.default_model)
        if tiny is None:
            return False, "(未找到可用模型，保守判不安全)"
        entry = find_model(tiny.name)
        if entry is None:
            return False, "(模型解析失败)"
        provider = get_provider(entry)
        resp = provider.chat(
            ChatRequest(model=entry.model, messages=[ChatMessage(role="user", content=prompt)]),
            timeout=15.0,
        )
        content = resp.content or ""
        is_safe = "安全" in content and "不安全" not in content
        reason = content.strip() if len(content) <= 200 else content.strip()[:200] + "…"
        return is_safe, reason
    except Exception as e:
        return False, f"(AI 判断失败，保守判不安全: {e})"
