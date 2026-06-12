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
    default:
      console.warn("unknown frame", msg);
  }
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
    } else if (isRestricted(tab.url)) {
      result = { ok: false, content: "当前页面不允许脚本注入 (浏览器内部页/商店页)。请切换到普通网页。" };
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
