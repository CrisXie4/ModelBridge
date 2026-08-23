// ModelBridge side panel — chat UI over the LocalBridge native host.
//
// Native Messaging note: chrome.runtime.connectNative gives a Port that speaks
// JSON objects directly — Chrome does the length-prefix framing; the Python
// host does the rest. We never touch bytes here.

const HOST_NAME = "com.modelbridge.localbridge";

const el = {
  messages: document.getElementById("messages"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  model: document.getElementById("model"),
  status: document.getElementById("status"),
};

let port = null;
let busy = false;
let turnSeq = 0;
let activeTurn = null; // { id, bubble, contentEl, reasoningEl, buf }

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

function setStatus(state, title) {
  el.status.className = "status status--" + state;
  el.status.title = title || state;
}

function connect() {
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (e) {
    setStatus("off", "无法连接宿主");
    addError("无法连接 LocalBridge 宿主：" + e.message);
    return;
  }
  port.onMessage.addListener(handleFrame);
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    port = null;
    setStatus("off", "已断开");
    if (err) {
      addError(
        "与宿主的连接断开：" +
          err.message +
          "\n\n请确认已运行 `mbridge bridge install --extension-id <本扩展ID>`，" +
          "并在 chrome://extensions 重新加载扩展。"
      );
    }
    setBusy(false);
  });
  setStatus("on", "已连接");
}

function ensurePort() {
  if (!port) connect();
  return port;
}

// ---------------------------------------------------------------------------
// Incoming frames
// ---------------------------------------------------------------------------

function handleFrame(msg) {
  switch (msg.type) {
    case "ready":
      onReady(msg);
      break;
    case "delta":
      onDelta(msg);
      break;
    case "assistant":
      onAssistant(msg);
      break;
    case "done":
      onDone(msg);
      break;
    case "error":
      addError(msg.message || "未知错误");
      setBusy(false);
      break;
    // Stage 2 (tool_call) and Stage 3 (approval) cases are added below.
    case "tool_call":
      onToolCall(msg);
      break;
    case "approval":
      onApproval(msg);
      break;
    case "notice":
      addNotice(msg.text || "");
      break;
    case "state":
      applyState(msg);
      break;
    default:
      console.warn("unknown frame", msg);
  }
}

// 宿主的系统提示 (压缩/溢出恢复等) — 一行灰色小字，不进对话气泡。
function addNotice(text) {
  if (!text) return;
  const div = document.createElement("div");
  div.className = "notice";
  div.textContent = text;
  el.messages.appendChild(div);
  scrollToBottom();
}

// 状态快照 → 顶栏 tooltip (缓存命中率 / 任务进度，不占面板空间)。
function applyState(msg) {
  const bits = [];
  if (msg.cacheSession) bits.push("缓存命中 " + msg.cacheSession);
  const todos = msg.todos || [];
  if (todos.length) {
    const done = todos.filter((t) => t && t.status === "completed").length;
    bits.push("任务 " + done + "/" + todos.length);
  }
  if (bits.length) el.status.title = (el.status.title.split("·")[0] || "").trim() + " · " + bits.join(" · ");
}

function onReady(msg) {
  setStatus("on", "已连接 · v" + (msg.version || "?"));
  el.model.innerHTML = "";
  (msg.models || []).forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === msg.defaultModel) opt.selected = true;
    el.model.appendChild(opt);
  });
  if (!(msg.models || []).length) {
    const opt = document.createElement("option");
    opt.textContent = "(未配置模型)";
    el.model.appendChild(opt);
  }
}

function onDelta(msg) {
  if (!activeTurn || activeTurn.id !== msg.id) return;
  if (msg.kind === "reasoning") {
    activeTurn.reasoningBody.textContent += msg.text;
    activeTurn.reasoning.style.display = "block";
  } else {
    hideTyping(activeTurn);
    activeTurn.buf += msg.text;
    // 流式阶段先按纯文本展示 (快)；最终 assistant 帧到达后再渲染 Markdown。
    activeTurn.contentEl.textContent = activeTurn.buf;
  }
  scrollToBottom();
}

function onAssistant(msg) {
  if (!activeTurn || activeTurn.id !== msg.id) return;
  hideTyping(activeTurn);
  // Authoritative final content (replaces the streamed buffer).
  renderMarkdown(activeTurn.contentEl, msg.content || activeTurn.buf);
  activeTurn.contentEl.classList.add("md");
  scrollToBottom();
}

function onDone(msg) {
  if (activeTurn && activeTurn.id === msg.id) {
    hideTyping(activeTurn);
    // 一轮下来气泡完全是空的 (比如出错被 error 气泡接管) 就移除，不留空壳。
    if (!activeTurn.bubble.querySelector(".content")?.hasChildNodes() &&
        !activeTurn.buf &&
        !activeTurn.bubble.querySelector(".tool") &&
        activeTurn.reasoning.style.display === "none") {
      activeTurn.bubble.remove();
    }
    activeTurn = null;
  }
  setBusy(false);
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

function sendChat() {
  const text = el.input.value.trim();
  if (!text || busy) return;
  if (!ensurePort()) return;

  addMsg("user", text);
  el.input.value = "";
  autoGrow();

  const id = "t" + ++turnSeq;
  activeTurn = newAssistantBubble(id);
  setBusy(true);

  port.postMessage({ type: "chat", id, text, model: el.model.value || null });
}

function setBusy(b) {
  busy = b;
  el.send.disabled = b;
  if (b) setStatus("busy", "思考中…");
  else if (port) setStatus("on", "已连接");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function hideEmpty() {
  const e = document.getElementById("empty");
  if (e) e.remove();
}

function addMsg(role, text) {
  hideEmpty();
  const div = document.createElement("div");
  div.className = "msg " + role;
  const r = document.createElement("span");
  r.className = "role";
  r.textContent = role === "user" ? "你" : role === "error" ? "错误" : "助手";
  const body = document.createElement("span");
  body.textContent = text;
  div.append(r, body);
  el.messages.appendChild(div);
  scrollToBottom(true);
  return div;
}

function newAssistantBubble(id) {
  hideEmpty();
  const div = document.createElement("div");
  div.className = "msg assistant";
  const r = document.createElement("span");
  r.className = "role";
  r.textContent = "助手";

  // 思考过程 — 折叠在 <details> 里，不再整段灰字占屏。
  const reasoning = document.createElement("details");
  reasoning.className = "reasoning";
  reasoning.style.display = "none";
  const rSummary = document.createElement("summary");
  rSummary.textContent = "思考过程";
  const rBody = document.createElement("div");
  rBody.className = "reasoning-body";
  reasoning.append(rSummary, rBody);

  const content = document.createElement("div");
  content.className = "content";

  const typing = document.createElement("span");
  typing.className = "typing";
  for (let i = 0; i < 3; i++) typing.appendChild(document.createElement("i"));

  div.append(r, reasoning, content, typing);
  el.messages.appendChild(div);
  scrollToBottom(true);
  return {
    id,
    bubble: div,
    contentEl: content,
    reasoning,
    reasoningBody: rBody,
    typing,
    buf: "",
  };
}

function hideTyping(turn) {
  if (turn && turn.typing) {
    turn.typing.remove();
    turn.typing = null;
  }
}

function addError(text) {
  addMsg("error", "⚠ " + text);
}

function addToolLine(text) {
  const span = document.createElement("span");
  span.className = "tool";
  span.textContent = "🔧 " + text;
  if (activeTurn) {
    // 工具调用发生在最终回答之前 — 芯片插在内容区上方，保持时间顺序。
    activeTurn.bubble.insertBefore(span, activeTurn.contentEl);
  } else {
    hideEmpty();
    el.messages.appendChild(span);
  }
  scrollToBottom();
}

// 用户往上翻历史时不要强行拽到底；只有本来就贴着底部 (或 force) 才跟随。
function scrollToBottom(force) {
  const m = el.messages;
  const nearBottom = m.scrollHeight - m.scrollTop - m.clientHeight < 80;
  if (force || nearBottom) m.scrollTop = m.scrollHeight;
}

// ---------------------------------------------------------------------------
// 极简 Markdown 渲染 — 纯 DOM 构建，绝不注入 HTML 字符串。
// 支持: 段落 / # 标题 / - * 列表 / 1. 列表 / > 引用 / ``` 代码块 /
//       `行内代码` / **粗体** / *斜体* / [链接](url)
// ---------------------------------------------------------------------------

function mdInline(text) {
  const frag = document.createDocumentFragment();
  const re =
    /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1]) {
      const c = document.createElement("code");
      c.textContent = m[1].slice(1, -1);
      frag.appendChild(c);
    } else if (m[2]) {
      const b = document.createElement("strong");
      b.textContent = m[2].slice(2, -2);
      frag.appendChild(b);
    } else if (m[3]) {
      const i = document.createElement("em");
      i.textContent = m[3].slice(1, -1);
      frag.appendChild(i);
    } else if (m[4]) {
      const a = document.createElement("a");
      a.textContent = m[5];
      a.href = m[6];
      a.target = "_blank";
      a.rel = "noreferrer noopener";
      frag.appendChild(a);
    }
    last = re.lastIndex;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function renderMarkdown(container, text) {
  container.textContent = "";
  const lines = String(text == null ? "" : text).split("\n");
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    const p = document.createElement("p");
    para.forEach((ln, idx) => {
      if (idx) p.appendChild(document.createElement("br"));
      p.appendChild(mdInline(ln));
    });
    container.appendChild(p);
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      flushPara();
      const code = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++; // 跳过结尾 ```
      const pre = document.createElement("pre");
      const c = document.createElement("code");
      c.textContent = code.join("\n");
      pre.appendChild(c);
      container.appendChild(pre);
      continue;
    }

    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      flushPara();
      // 面板空间小，# 从 h3 起步，逐级降到 h6。
      const hEl = document.createElement("h" + Math.min(h[1].length + 2, 6));
      hEl.appendChild(mdInline(h[2]));
      container.appendChild(hEl);
      i++;
      continue;
    }

    const isUl = /^\s*[-*]\s+/.test(line);
    const isOl = /^\s*\d+[.)]\s+/.test(line);
    if (isUl || isOl) {
      flushPara();
      const listEl = document.createElement(isUl ? "ul" : "ol");
      const itemRe = isUl ? /^\s*[-*]\s+(.*)/ : /^\s*\d+[.)]\s+(.*)/;
      while (i < lines.length) {
        const im = lines[i].match(itemRe);
        if (!im) break;
        const li = document.createElement("li");
        li.appendChild(mdInline(im[1]));
        listEl.appendChild(li);
        i++;
      }
      container.appendChild(listEl);
      continue;
    }

    const q = line.match(/^>\s?(.*)/);
    if (q) {
      flushPara();
      const bq = document.createElement("blockquote");
      const qLines = [];
      while (i < lines.length) {
        const qm = lines[i].match(/^>\s?(.*)/);
        if (!qm) break;
        qLines.push(qm[1]);
        i++;
      }
      qLines.forEach((ln, idx) => {
        if (idx) bq.appendChild(document.createElement("br"));
        bq.appendChild(mdInline(ln));
      });
      container.appendChild(bq);
      continue;
    }

    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }
    para.push(line);
    i++;
  }
  flushPara();
}

function autoGrow() {
  el.input.style.height = "auto";
  el.input.style.height = Math.min(el.input.scrollHeight, 140) + "px";
}

// ---------------------------------------------------------------------------
// Stage 2/3 handlers — defined in page-tools.js (loaded after this file would
// be cleaner, but we keep one file): provided as no-ops until those stages.
// ---------------------------------------------------------------------------

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function isRestricted(url) {
  return (
    !url ||
    /^(chrome|edge|about|devtools|view-source):/i.test(url) ||
    url.startsWith("https://chrome.google.com/webstore") ||
    url.startsWith("https://chromewebstore.google.com")
  );
}

// 等待加载的上限须小于 host 侧的工具超时 (browser_bridge.DEFAULT_TOOL_TIMEOUT)，
// 否则模型收到的是"超时"而不是这里的降级结果。
const LOAD_WAIT_MS = 60000;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Resolve true 当 tab 加载到 complete；超时 resolve false (页面卡住时降级处理)。
function waitForTabLoad(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const finish = (loaded) => {
      if (settled) return;
      settled = true;
      chrome.tabs.onUpdated.removeListener(onUpdated);
      if (timer) clearTimeout(timer);
      resolve(loaded);
    };
    const onUpdated = (id, changeInfo) => {
      if (id === tabId && changeInfo.status === "complete") finish(true);
    };
    chrome.tabs.onUpdated.addListener(onUpdated);
    timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab && tab.status === "complete") finish(true);
      })
      .catch(() => finish(false)); // 标签页已关闭
  });
}

async function onToolCall(msg) {
  addToolLine(toolSummary(msg.name, msg.args));
  let result = { ok: false, content: "未知错误" };
  try {
    const tab = await getActiveTab();
    if (!tab) {
      result = { ok: false, content: "找不到活动标签页。" };
    } else if (msg.name === "navigate") {
      // tabs.update 不需要脚本注入 (chrome:// 新标签页也能跳走)，并且
      // 等新页面加载完成后才回复 — 模型下一步可以直接 read_page。
      result = await navigateAndWait(tab, msg.args || {});
    } else if (msg.name === "get_cookies") {
      // chrome.cookies 不需要页面注入，浏览器内部页也能查 (只要 URL 是 http(s))。
      result = await getCookies(tab, msg.args || {});
    } else if (msg.name === "net_start" || msg.name === "net_log" || msg.name === "net_stop") {
      result = await netTool(tab, msg.name, msg.args || {});
    } else if (isRestricted(tab.url)) {
      result = { ok: false, content: "当前页面不允许脚本注入 (浏览器内部页/商店页)。请切换到普通网页。" };
    } else if ((msg.name === "click" || msg.name === "press_key") && (msg.args || {}).trusted) {
      result = await trustedInputTool(tab, msg.name, msg.args || {});
    } else {
      result = await runPageTool(tab, msg.name, msg.args || {});
    }
  } catch (e) {
    result = { ok: false, content: "注入失败: " + (e && e.message ? e.message : String(e)) };
  }
  if (port) {
    port.postMessage({
      type: "tool_result",
      requestId: msg.requestId,
      ok: !!result.ok,
      content: String(result.content == null ? "" : result.content),
    });
  }
}

async function navigateAndWait(tab, args) {
  const url = String(args.url || "");
  if (!/^https?:\/\//i.test(url)) {
    return { ok: false, content: "url 必须是 http(s):// 开头的完整地址。" };
  }
  try {
    await chrome.tabs.update(tab.id, { url });
  } catch (e) {
    return { ok: false, content: "跳转失败: " + (e && e.message ? e.message : String(e)) };
  }
  addToolLine("⏳ 等待页面加载完成…");
  const loaded = await waitForTabLoad(tab.id, LOAD_WAIT_MS);
  let title = "";
  try {
    const t = await chrome.tabs.get(tab.id);
    title = (t && t.title) || "";
  } catch (e) {
    /* 标签页可能已关闭 */
  }
  if (loaded) {
    return {
      ok: true,
      content:
        `已打开并加载完成: ${url}` +
        (title ? `\n标题: ${title}` : "") +
        "\n可直接用 read_page 读取页面内容。",
    };
  }
  return {
    ok: true,
    content:
      `已打开 ${url}，但等待 ${LOAD_WAIT_MS / 1000} 秒后页面仍在加载 (可能卡住)。` +
      "可用 read_page 读取当前已渲染的内容。",
  };
}

// ---------------------------------------------------------------------------
// Cookies — chrome.cookies (面板上下文，无需页面注入)
// ---------------------------------------------------------------------------

async function getCookies(tab, args) {
  const url = tab.url || "";
  if (!/^https?:/i.test(url)) {
    return { ok: false, content: "当前页面不是 http(s) 地址，无法查询 Cookie。" };
  }
  let items = [];
  if (args.include_domain) {
    try {
      const host = new URL(url).hostname;
      items = await chrome.cookies.getAll({ domain: host });
    } catch (e) {
      items = await chrome.cookies.getAll({ url });
    }
  } else {
    items = await chrome.cookies.getAll({ url });
  }
  const word = String(args.name || "").toLowerCase();
  if (word) {
    items = items.filter((c) => c.name.toLowerCase().includes(word));
  }
  if (!items.length) {
    return {
      ok: true,
      content: word ? `(没有名字里含 ${args.name} 的 Cookie)` : "(当前站点没有 Cookie)",
    };
  }
  const lines = items.map((c) => {
    const flags = [c.httpOnly ? "httpOnly" : "", c.secure ? "secure" : "", c.sameSite || ""]
      .filter(Boolean)
      .join(" ");
    const exp = c.expirationDate
      ? new Date(c.expirationDate * 1000).toLocaleString()
      : "会话结束";
    return (
      `${c.name} = ${c.value}\n` +
      `    domain=${c.domain} path=${c.path} 过期=${exp}${flags ? " " + flags : ""}`
    );
  });
  return { ok: true, content: `当前站点共 ${items.length} 条 Cookie:\n` + lines.join("\n") };
}

// ---------------------------------------------------------------------------
// Network capture — chrome.webRequest observers keyed by tab id
// ---------------------------------------------------------------------------

const NET_BUFFER_CAP = 500;
const netMonitors = new Map(); // tabId -> {entries, byReq, listeners}

function fmtSize(n) {
  if (!n) return "-";
  return n >= 1024 ? (n / 1024).toFixed(1) + "KB" : n + "B";
}

function fmtClock(ts) {
  const t = new Date(ts);
  return [t.getHours(), t.getMinutes(), t.getSeconds()]
    .map((x) => String(x).padStart(2, "0"))
    .join(":");
}

async function netTool(tab, name, args) {
  if (name === "net_start") {
    if (netMonitors.has(tab.id)) {
      return { ok: true, content: "本标签页已在记录中。操作页面后用 net_log 查看。" };
    }
    const filter = { urls: ["<all_urls>"], tabId: tab.id };
    const mon = { entries: [], byReq: new Map(), listeners: [] };
    const record = (e) => {
      mon.byReq.set(e.rid, e);
      mon.entries.push(e);
      if (mon.entries.length > NET_BUFFER_CAP) {
        const dropped = mon.entries.splice(0, mon.entries.length - NET_BUFFER_CAP);
        for (const d of dropped) mon.byReq.delete(d.rid);
      }
    };
    const onBefore = (d) => {
      const e = {
        rid: d.requestId,
        ts: Date.now(),
        method: d.method,
        url: d.url,
        type: d.type,
        status: 0,
        size: 0,
        dur: 0,
        err: "",
      };
      record(e);
    };
    const onDone = (d) => {
      const e = mon.byReq.get(d.requestId);
      if (!e) return;
      e.status = d.statusCode;
      e.dur = Date.now() - e.ts;
      const cl = (d.responseHeaders || []).find(
        (h) => h.name.toLowerCase() === "content-length"
      );
      if (cl) e.size = parseInt(cl.value, 10) || 0;
    };
    const onErr = (d) => {
      const e = mon.byReq.get(d.requestId);
      if (!e) return;
      e.err = d.error || "failed";
      e.dur = Date.now() - e.ts;
    };
    try {
      chrome.webRequest.onBeforeRequest.addListener(onBefore, filter);
      chrome.webRequest.onCompleted.addListener(onDone, filter, ["responseHeaders"]);
      chrome.webRequest.onErrorOccurred.addListener(onErr, filter);
    } catch (e) {
      return { ok: false, content: "webRequest 监听注册失败: " + (e && e.message ? e.message : String(e)) };
    }
    mon.listeners = [
      [chrome.webRequest.onBeforeRequest, onBefore],
      [chrome.webRequest.onCompleted, onDone],
      [chrome.webRequest.onErrorOccurred, onErr],
    ];
    netMonitors.set(tab.id, mon);
    return {
      ok: true,
      content: `开始记录本标签页的网络请求 (上限 ${NET_BUFFER_CAP} 条，跨页面跳转继续记录)。` +
        "接下来点击/提交/跳转后用 net_log 查看。",
    };
  }
  if (name === "net_log") {
    const mon = netMonitors.get(tab.id);
    if (!mon) {
      return { ok: false, content: "本标签页还没有开始记录 — 先调用 net_start。" };
    }
    let entries = mon.entries;
    const word = String(args.filter || "").toLowerCase();
    if (word) {
      entries = entries.filter(
        (e) => e.url.toLowerCase().includes(word) || e.method.toLowerCase().includes(word)
      );
    }
    const total = entries.length;
    let limit = parseInt(args.limit, 10) || 30;
    limit = Math.min(Math.max(limit, 1), 200);
    const shown = entries.slice(-limit);
    if (!shown.length) {
      return {
        ok: true,
        content: word ? `(没有匹配 ${args.filter} 的请求，共 ${total} 条)` : "(还没有记录到请求)",
      };
    }
    const lines = shown.map((e) => {
      const st = e.err ? "ERR " + e.err : e.status || "…";
      const dur = e.dur ? e.dur + "ms" : "-";
      return `[${fmtClock(e.ts)}] ${e.method} ${st} ${fmtSize(e.size)} ${dur} ${e.url}`;
    });
    return {
      ok: true,
      content: `最近 ${shown.length}/${total} 条请求 (net_start 之后):\n` + lines.join("\n"),
    };
  }
  if (name === "net_stop") {
    const mon = netMonitors.get(tab.id);
    if (!mon) return { ok: true, content: "本标签页本就没有在记录。" };
    for (const [ev, cb] of mon.listeners) {
      try {
        ev.removeListener(cb);
      } catch (e) {
        /* listener 可能已被浏览器清理 */
      }
    }
    netMonitors.delete(tab.id);
    return { ok: true, content: `已停止记录并清空 (共记录过 ${mon.entries.length} 条)。` };
  }
  return { ok: false, content: "未知的网络工具: " + name };
}

// ---------------------------------------------------------------------------
// Trusted input — CDP real input events (isTrusted=true) via chrome.debugger
// ---------------------------------------------------------------------------

// 回调式 chrome API → Promise
function cdpCall(obj, method, ...args) {
  return new Promise((resolve, reject) => {
    obj[method](...args, (r) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(r);
    });
  });
}

const CDP_KEY_MAP = {
  enter: { key: "Enter", code: "Enter", vk: 13, text: "\r" },
  tab: { key: "Tab", code: "Tab", vk: 9 },
  escape: { key: "Escape", code: "Escape", vk: 27 },
  backspace: { key: "Backspace", code: "Backspace", vk: 8 },
  delete: { key: "Delete", code: "Delete", vk: 46 },
  arrowleft: { key: "ArrowLeft", code: "ArrowLeft", vk: 37 },
  arrowright: { key: "ArrowRight", code: "ArrowRight", vk: 39 },
  arrowup: { key: "ArrowUp", code: "ArrowUp", vk: 38 },
  arrowdown: { key: "ArrowDown", code: "ArrowDown", vk: 40 },
  home: { key: "Home", code: "Home", vk: 36 },
  end: { key: "End", code: "End", vk: 35 },
  pageup: { key: "PageUp", code: "PageUp", vk: 33 },
  pagedown: { key: "PageDown", code: "PageDown", vk: 34 },
  " ": { key: " ", code: "Space", vk: 32, text: " " },
};

function cdpKeyInfo(key) {
  const k = String(key || "");
  if (k.length === 1) {
    const upper = k.toUpperCase();
    const code = /^[A-Z]$/i.test(k)
      ? "Key" + upper
      : /^[0-9]$/.test(k)
        ? "Digit" + k
        : k;
    return { key: k, code, vk: upper.charCodeAt(0), text: k };
  }
  return CDP_KEY_MAP[k.toLowerCase()] || null;
}

// 序列化进页面：定位元素 → 滚到视野中央 → 返回视口坐标 (中心点)。
function cdpLocateElement(selector) {
  const el = document.querySelector(selector);
  if (!el) return { error: "找不到元素: " + selector };
  el.scrollIntoView({ block: "center", inline: "center" });
  return new Promise((resolve) =>
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const r = el.getBoundingClientRect();
        resolve({ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) });
      })
    )
  );
}

async function trustedInputTool(tab, name, args) {
  // 定位目标 (click 必需；press_key 只在有 selector 时定位)
  let point = null;
  if (name === "click" || args.selector) {
    let injected;
    try {
      injected = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: cdpLocateElement,
        args: [args.selector || ""],
      });
    } catch (e) {
      return { ok: false, content: "定位元素失败: " + (e && e.message ? e.message : String(e)) };
    }
    const r = injected && injected[0] && injected[0].result;
    if (!r) return { ok: false, content: "页面无返回。" };
    if (r.error) return { ok: false, content: r.error };
    point = r;
    await sleep(150); // 等滚动落位
  }

  let attached = true;
  try {
    await cdpCall(chrome.debugger, "attach", { tabId: tab.id }, "1.3");
  } catch (e) {
    // DevTools 正在调试该标签页 / 浏览器策略禁止 → 降级到软模拟。
    addToolLine("⚠ CDP 附加失败，已回退模拟输入 (" + (e && e.message ? e.message : e) + ")");
    attached = false;
  }
  if (!attached) return runPageTool(tab, name, args);

  try {
    if (name === "click") {
      const p = { x: point.x, y: point.y, button: "left", buttons: 1, clickCount: 1 };
      await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: point.x,
        y: point.y,
      });
      await sleep(80 + Math.random() * 120);
      await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchMouseEvent", {
        type: "mousePressed",
        ...p,
      });
      await sleep(60 + Math.random() * 80);
      await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchMouseEvent", {
        type: "mouseReleased",
        ...p,
      });
      await sleep(500);
      // 点击可能触发跳转：与软点击一致，等新页面加载完成再回复。
      let after = null;
      try {
        after = await chrome.tabs.get(tab.id);
      } catch (e) {
        /* 标签页可能已关闭 */
      }
      if (after && after.status === "loading") {
        addToolLine("⏳ 点击触发页面跳转，等待加载…");
        await waitForTabLoad(tab.id, LOAD_WAIT_MS);
      }
      return { ok: true, content: "已用 CDP 真实输入点击: " + (args.selector || `(${point.x},${point.y})`) };
    }

    // press_key
    const ki = cdpKeyInfo(args.key);
    if (!ki) return { ok: false, content: "无法识别的 key: " + args.key };
    const base = {
      key: ki.key,
      code: ki.code,
      windowsVirtualKeyCode: ki.vk,
      nativeVirtualKeyCode: ki.vk,
    };
    const down = { type: "keyDown", ...base };
    if (ki.text) down.text = ki.text;
    await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchKeyEvent", down);
    if (ki.text) {
      await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchKeyEvent", {
        type: "char",
        text: ki.text,
      });
    }
    await sleep(40 + Math.random() * 60);
    await cdpCall(chrome.debugger, "sendCommand", "Input.dispatchKeyEvent", {
      type: "keyUp",
      ...base,
    });
    return { ok: true, content: "已用 CDP 真实输入按下 " + ki.key };
  } finally {
    try {
      await cdpCall(chrome.debugger, "detach", { tabId: tab.id });
    } catch (e) {
      /* 可能已随页面跳转自动分离 */
    }
  }
}

async function runPageTool(tab, name, args) {
  // 页面还在加载时先等它加载完，而不是注入后读到半张页面 / 直接报错。
  let note = "";
  if (tab.status === "loading") {
    addToolLine("⏳ 页面加载中，等待完成…");
    const loaded = await waitForTabLoad(tab.id, LOAD_WAIT_MS);
    if (!loaded) {
      note = `[页面等待 ${LOAD_WAIT_MS / 1000} 秒仍未加载完成，以下为当前已渲染的内容]\n\n`;
    }
  }

  const inject = () =>
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: pageToolDispatcher,
      args: [name, args],
    });

  let injected;
  try {
    injected = await inject();
  } catch (e) {
    // 页面正在跳转/刷新时注入会失败 — 等加载完成后重试一次。
    addToolLine("⏳ 页面跳转中，等待后重试…");
    await waitForTabLoad(tab.id, LOAD_WAIT_MS);
    injected = await inject();
  }
  const result = (injected && injected[0] && injected[0].result) || {
    ok: false,
    content: "页面无返回。",
  };
  if (note && result.ok) result.content = note + result.content;

  // 点击可能触发跳转：等新页面加载完成再回复，模型可以无缝继续读取。
  if (name === "click" && result.ok) {
    await sleep(500);
    try {
      let after = await chrome.tabs.get(tab.id);
      if (after && after.status === "loading") {
        addToolLine("⏳ 点击触发页面跳转，等待加载…");
        const loaded = await waitForTabLoad(tab.id, LOAD_WAIT_MS);
        after = await chrome.tabs.get(tab.id);
        result.content += loaded
          ? `\n点击触发了页面跳转，新页面已加载完成 (${(after && after.url) || "?"})，可直接 read_page。`
          : "\n点击触发了页面跳转，但页面长时间未加载完成；read_page 可读取当前已渲染的内容。";
      }
    } catch (e) {
      /* 标签页可能已关闭 */
    }
  }
  return result;
}

function toolSummary(name, args) {
  args = args || {};
  if (name === "fill") return `fill ${args.selector} = ${truncate(args.value, 40)}`;
  if (name === "press_key") return `press_key ${args.key}${args.trusted ? " (CDP)" : ""}`;
  if (name === "get_cookies")
    return `get_cookies${args.name ? " " + args.name : ""}${args.include_domain ? " (整站)" : ""}`;
  if (name === "net_start") return "net_start 开始记录网络请求";
  if (name === "net_log") return `net_log${args.filter ? " " + args.filter : ""}`;
  if (name === "net_stop") return "net_stop 停止记录";
  if (args.selector) return `${name} ${args.selector}`;
  if (args.url) return `${name} ${args.url}`;
  return name;
}

function truncate(s, n) {
  s = String(s == null ? "" : s);
  return s.length <= n ? s : s.slice(0, n) + "…";
}

// This function is serialized and run *in the page* (isolated world). It must
// be fully self-contained — no references to side-panel scope.
function pageToolDispatcher(name, args) {
  args = args || {};
  const ok = (content) => ({ ok: true, content: content });
  const fail = (content) => ({ ok: false, content: content });

  function describe(el, i) {
    const tag = el.tagName.toLowerCase();
    const id = el.id ? "#" + el.id : "";
    const cls = el.className && typeof el.className === "string"
      ? "." + el.className.trim().split(/\s+/).join(".")
      : "";
    const attrs = [];
    ["type", "name", "href", "value", "placeholder", "aria-label", "role"].forEach((a) => {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) attrs.push(`${a}=${JSON.stringify(v.slice(0, 60))}`);
    });
    let text = (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
    if (text.length > 80) text = text.slice(0, 80) + "…";
    return `[${i}] <${tag}${id}${cls}> ${attrs.join(" ")}${text ? " — " + text : ""}`;
  }

  try {
    if (name === "read_page") {
      let max = parseInt(args.max_chars, 10);
      if (!Number.isFinite(max) || max <= 0) max = 8000;
      max = Math.min(max, 40000);
      let body = (document.body && document.body.innerText) || "";
      body = body.replace(/\n{3,}/g, "\n\n").trim();
      const truncated = body.length > max;
      if (truncated) body = body.slice(0, max);
      return ok(
        `标题: ${document.title}\nURL: ${location.href}\n\n正文:\n${body}` +
          (truncated ? "\n\n[已截断]" : "")
      );
    }
    if (name === "get_selection") {
      const sel = String(window.getSelection ? window.getSelection() : "").trim();
      return ok(sel || "(用户当前没有选中任何文本)");
    }
    if (name === "query_dom") {
      if (!args.selector) return fail("缺少 selector");
      const limit = Math.min(parseInt(args.limit, 10) || 20, 100);
      let nodes;
      try {
        nodes = document.querySelectorAll(args.selector);
      } catch (e) {
        return fail("无效的 selector: " + e.message);
      }
      if (!nodes.length) return ok("(没有匹配的元素)");
      const lines = [];
      for (let i = 0; i < nodes.length && i < limit; i++) lines.push(describe(nodes[i], i));
      const more = nodes.length > limit ? `\n[共 ${nodes.length} 个，仅显示前 ${limit}]` : "";
      return ok(lines.join("\n") + more);
    }
    if (name === "extract") {
      if (!args.selector) return fail("缺少 selector");
      let nodes;
      try {
        nodes = document.querySelectorAll(args.selector);
      } catch (e) {
        return fail("无效的 selector: " + e.message);
      }
      if (!nodes.length) return ok("(没有匹配的元素)");
      const out = [];
      nodes.forEach((el) => {
        if (args.attr) out.push(el.getAttribute(args.attr) || "");
        else out.push((el.innerText || el.textContent || "").trim());
      });
      return ok(out.join("\n"));
    }
    if (name === "click") {
      if (!args.selector) return fail("缺少 selector");
      const el = document.querySelector(args.selector);
      if (!el) return fail("找不到元素: " + args.selector);
      el.scrollIntoView({ block: "center" });
      el.click();
      return ok("已点击: " + args.selector);
    }
    if (name === "page_scroll") {
      const doc = document.scrollingElement || document.documentElement;
      if (args.selector) {
        const el = document.querySelector(args.selector);
        if (!el) return fail("找不到元素: " + args.selector);
        const block = args.block === "start" || args.block === "end" ? args.block : "center";
        el.scrollIntoView({ block });
      } else if (args.to === "top") {
        window.scrollTo(0, 0);
      } else if (args.to === "bottom") {
        window.scrollTo(0, doc.scrollHeight);
      } else {
        const dy = parseInt(args.dy, 10);
        window.scrollBy(0, Number.isFinite(dy) ? dy : 600);
      }
      return ok(
        "已滚动。当前滚动位置 " +
          Math.round(window.scrollY) +
          " / 总高 " +
          doc.scrollHeight +
          " (视口高 " +
          window.innerHeight +
          ")"
      );
    }
    if (name === "press_key") {
      const key = String(args.key || "Enter");
      let el = null;
      if (args.selector) {
        el = document.querySelector(args.selector);
        if (!el) return fail("找不到元素: " + args.selector);
      } else {
        el =
          document.activeElement && document.activeElement !== document.body
            ? document.activeElement
            : document.body;
      }
      let code, vk, text;
      if (key.length === 1) {
        vk = key.toUpperCase().charCodeAt(0);
        code = /^[a-z]$/i.test(key)
          ? "Key" + key.toUpperCase()
          : /^[0-9]$/.test(key)
            ? "Digit" + key
            : key;
        text = key;
      } else {
        const M = {
          Enter: [13, "\r", "Enter"],
          Tab: [9, "", "Tab"],
          Escape: [27, "", "Escape"],
          Backspace: [8, "", "Backspace"],
          Delete: [46, "", "Delete"],
          ArrowLeft: [37, "", "ArrowLeft"],
          ArrowRight: [39, "", "ArrowRight"],
          ArrowUp: [38, "", "ArrowUp"],
          ArrowDown: [40, "", "ArrowDown"],
          Home: [36, "", "Home"],
          End: [35, "", "End"],
          PageUp: [33, "", "PageUp"],
          PageDown: [34, "", "PageDown"],
          Space: [32, " ", "Space"],
        };
        const m = M[key] || M[key === " " ? "Space" : key];
        if (!m) return fail("无法识别的 key: " + key);
        vk = m[0];
        text = m[1];
        code = m[2];
      }
      const dispKey = key === "Space" ? " " : key;
      const fire = (type, charCode) => {
        const ev = new KeyboardEvent(type, {
          key: dispKey,
          code,
          bubbles: true,
          cancelable: true,
        });
        Object.defineProperty(ev, "keyCode", { get: () => vk });
        Object.defineProperty(ev, "which", { get: () => vk });
        if (charCode) Object.defineProperty(ev, "charCode", { get: () => charCode });
        el.dispatchEvent(ev);
      };
      fire("keydown");
      fire("keypress", text ? vk : 0);
      fire("keyup");
      // 模拟事件不触发浏览器默认行为：Enter 在表单里手动补一次提交。
      if (key === "Enter" && el.form) {
        if (typeof el.form.requestSubmit === "function") {
          el.form.requestSubmit();
          return ok("已按键 Enter 并提交所在表单 (requestSubmit)。");
        }
        el.form.submit();
        return ok("已按键 Enter 并提交所在表单。");
      }
      return ok("已按键: " + key);
    }
    if (name === "fill") {
      if (!args.selector) return fail("缺少 selector");
      const el = document.querySelector(args.selector);
      if (!el) return fail("找不到元素: " + args.selector);
      el.focus();
      const setter = Object.getOwnPropertyDescriptor(
        el.constructor.prototype,
        "value"
      );
      if (setter && setter.set) setter.set.call(el, args.value == null ? "" : String(args.value));
      else el.value = args.value == null ? "" : String(args.value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return ok("已填写: " + args.selector);
    }
    if (name === "inject_js") {
      if (!args.code) return fail("缺少 code");
      // Wrap in a self-removing pattern: create <script>, run, remove.
      // This ensures the script tag itself is gone from the DOM after execution.
      const wrappedCode = `(function(){
        const s = document.createElement('script');
        s.textContent = ${JSON.stringify(args.code)};
        (document.head || document.documentElement).appendChild(s);
        try {
          const result = window.eval(s.textContent);
          s.remove();
          return { ok: true, content: result !== undefined ? String(result) : '(无返回值)' };
        } catch(e) {
          s.remove();
          return { ok: false, content: String(e) };
        }
      })()`;
      try {
        const result = window.eval(wrappedCode);
        return result;
      } catch (e) {
        return fail("JS 执行出错: " + String(e));
      }
    }
    // navigate 不在页面里执行 — 侧边栏用 chrome.tabs.update 处理并等待加载完成。
    return fail("未知的浏览器工具: " + name);
  } catch (e) {
    return fail("页面执行出错: " + (e && e.message ? e.message : String(e)));
  }
}

function onApproval(msg) {
  hideEmpty();
  const card = document.createElement("div");
  card.className = "approval";

  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = "需要确认：" + (msg.summary || msg.tool);

  const detail = document.createElement("div");
  detail.className = "detail";
  detail.textContent = msg.detail || "";

  const actions = document.createElement("div");
  actions.className = "actions";

  let answered = false;
  const respond = (decision, label) => {
    if (answered) return;
    answered = true;
    if (port) {
      port.postMessage({ type: "approval_result", requestId: msg.requestId, decision });
    }
    actions.remove();
    const chosen = document.createElement("div");
    chosen.className = "detail";
    chosen.textContent = "→ " + label;
    card.appendChild(chosen);
    scrollToBottom();
  };

  const mk = (cls, label, decision) => {
    const b = document.createElement("button");
    b.className = cls;
    b.textContent = label;
    b.addEventListener("click", () => respond(decision, label));
    return b;
  };

  actions.append(
    mk("yes", "同意", "yes"),
    mk("always", "本会话总是", "always"),
    mk("no", "拒绝", "no")
  );

  card.append(summary, detail, actions);
  el.messages.appendChild(card);
  scrollToBottom();
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------

el.send.addEventListener("click", sendChat);
el.input.addEventListener("input", autoGrow);
el.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

connect();
