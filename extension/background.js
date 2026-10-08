let captureError = "";
const NATIVE_HOST = "translation_backend";
let nativePort = null;
let nativeRequestId = 0;
const nativeRequests = new Map();

const CAPTION_TYPES = new Set(["asr", "translation", "segment_start", "status", "capture-error"]);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) return false;
  if (CAPTION_TYPES.has(message.type)) {
    forwardCaption(message);
    return false;
  }
  if (message.type === "start") {
    start(message).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "stop") {
    stop().then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "switch-chain") {
    switchChain(message.chain).then(
      (reply) => sendResponse(reply),
      (error) => sendResponse({ ok: false, error: String((error && error.message) || error) })
    );
    return true;
  }
  if (message.type === "capture-error") {
    captureError = message.error || "";
  }
  // Reachability probe. status is answered by the host without starting
  // anything, so the popup can tell "the native host cannot be reached at all"
  // apart from "the backend would not start" before committing to the slow path.
  // Those are different faults with different fixes, and the popup used to learn
  // about them only from a generic failure that refresh() then erased.
  if (message.type === "ping-backend") {
    nativeRequest("status", 8000).then(
      (reply) => sendResponse({ ok: true, reply }),
      (error) => sendResponse({ ok: false, error: String((error && error.message) || error) })
    );
    return true;
  }
  return false;
});

function nativeConnection() {
  if (nativePort) return nativePort;
  nativePort = chrome.runtime.connectNative(NATIVE_HOST);
  nativePort.onMessage.addListener((message) => {
    const request = nativeRequests.get(message && message.requestId);
    if (!request) return;
    nativeRequests.delete(message.requestId);
    clearTimeout(request.timer);
    request.resolve(message);
  });
  nativePort.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError;
    nativePort = null;
    for (const request of nativeRequests.values()) {
      clearTimeout(request.timer);
      request.reject(new Error((error && error.message) || "本地启动桥接已断开"));
    }
    nativeRequests.clear();
  });
  return nativePort;
}
function nativeRequest(type, timeoutMs = 120000, extra = {}) {
  return new Promise((resolve, reject) => {
    const requestId = ++nativeRequestId;
    const timer = setTimeout(() => {
      nativeRequests.delete(requestId);
      reject(new Error("本地后端操作超时"));
    }, timeoutMs);
    nativeRequests.set(requestId, { resolve, reject, timer });
    try {
      nativeConnection().postMessage({ type, requestId, ...extra });
    } catch (error) {
      clearTimeout(timer);
      nativeRequests.delete(requestId);
      reject(error);
    }
  });
}
async function ensureBackend(audioOnly) {
  const response = await nativeRequest("ensure", 120000, { audioOnly: Boolean(audioOnly) });
  if (!response || !response.ok || !response.ready) {
    throw new Error((response && response.error) || "本地后端启动失败");
  }
}

async function shutdownBackend() {
  const response = await nativeRequest("shutdown", 15000);
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "本地后端停止失败");
  }
}

async function switchChain(chain) {
  // The host owns the kill-and-restart: this file never kills a process. A chain is a
  // set of models and ports, and deciding which of them to keep alive is exactly the
  // knowledge that belongs in one place -- the same reason the pid file and the port
  // list live in native_host.py rather than here.
  //
  // 180s because the host waits 20s for teardown, then start_backend waits up to 90s,
  // and a failed attempt is followed by a full second start for the fallback. Anything
  // less would time out on the slow-but-working path and report a failure that did not
  // happen.
  const reply = await nativeRequest("switch-chain", 180000, { chain: String(chain || "") });
  if (!reply) return { ok: false, error: "本地后端没有回应链路切换" };
  // rolled_back is not an error to hide: the user clicked something that did not work,
  // and the popup says so rather than quietly showing the old chain.
  return reply;
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (!changes.captionTextOnly && !changes.captionFontPx && !changes.captionColor) return;
  applyCaptionStyle(Boolean(changes.captionTextOnly));
});

function clampFont(px) {
  const size = Number(px);
  if (!Number.isFinite(size)) return 20;
  return Math.max(16, Math.min(40, Math.round(size)));
}

async function start({ tabId, language, audioOnly, apiEnabled, apiUrl, apiKey, apiModel, partialIntervalMs }) {
  captureError = "";
  await ensureBackend(audioOnly);
  // Chrome keeps a tab captured until its tracks end. Release the previous
  // consumer before asking for another id, or the same tab stays dead.
  await ensureOffscreen();
  const saved = await chrome.storage.local.get({
    captionTextOnly: false,
    captionFontPx: 20,
    captionColor: "#ffffff",
  });
  const fontPx = clampFont(saved.captionFontPx);
  const response = await sendWhenReady({
    type: "start-capture",
    language: language || "auto",
    audioOnly: Boolean(audioOnly),
    apiEnabled: Boolean(apiEnabled),
    apiUrl: apiUrl || "",
    apiKey: apiKey || "",
    apiModel: apiModel || "",
    textOnly: saved.captionTextOnly !== false,
    fontPx,
    color: saved.captionColor,
    partialIntervalMs,
  });
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "offscreen 启动失败");
  }
  await chrome.storage.session.set({
    running: true,
    tabId,
    language: language || "auto",
  });
  await applyCaptionStyle();
}

async function stop() {
  if (await chrome.offscreen.hasDocument()) {
    try {
      await sendWhenReady({ type: "stop-capture" });
    } catch (_error) {
      // offscreen may already be gone
    }
  }
  if (await chrome.offscreen.hasDocument()) {
    await chrome.offscreen.closeDocument();
  }
  await chrome.storage.session.set({ running: false });
  await shutdownBackend();
}

async function sendWhenReady(message) {
  let lastError = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const response = await chrome.runtime.sendMessage(message);
      if (response) return response;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError || new Error("offscreen 无响应");
}

async function releaseCapture() {
  if (!(await chrome.offscreen.hasDocument())) return;
  try {
    await sendWhenReady({ type: "release-capture" });
  } catch (_error) {
    // offscreen may already be gone
  }
  if (await chrome.offscreen.hasDocument()) {
    await chrome.offscreen.closeDocument();
  }
}

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "Capture tab audio and stream it to the local translator",
  });
}

function forwardCaption(_payload) {}

async function applyCaptionStyle() {
  const saved = await chrome.storage.local.get({
    captionTextOnly: false,
    captionFontPx: 20,
    captionColor: "#ffffff",
  });
  const fontPx = clampFont(saved.captionFontPx);
  chrome.runtime.sendMessage({
    type: "caption-style",
    textOnly: saved.captionTextOnly !== false,
    fontPx,
    color: saved.captionColor,
  }).catch(() => {});
}


