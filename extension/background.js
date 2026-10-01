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
  if (message.type === "capture-error") {
    captureError = message.error || "";
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
function nativeRequest(type, timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    const requestId = ++nativeRequestId;
    const timer = setTimeout(() => {
      nativeRequests.delete(requestId);
      reject(new Error("本地后端操作超时"));
    }, timeoutMs);
    nativeRequests.set(requestId, { resolve, reject, timer });
    try {
      nativeConnection().postMessage({ type, requestId });
    } catch (error) {
      clearTimeout(timer);
      nativeRequests.delete(requestId);
      reject(error);
    }
  });
}
async function ensureBackend() {
  const response = await nativeRequest("ensure");
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

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (!changes.captionTextOnly && !changes.captionFontPx) return;
  applyCaptionStyle(Boolean(changes.captionTextOnly));
});

function clampFont(px) {
  const size = Number(px);
  if (!Number.isFinite(size)) return 20;
  return Math.max(16, Math.min(40, Math.round(size)));
}

async function start({ tabId, language, windowId, apiEnabled, apiUrl, apiKey, apiModel }) {
  captureError = "";
  await ensureBackend();
  // Chrome keeps a tab captured until its tracks end. Release the previous
  // consumer before asking for another id, or the same tab stays dead.
  await releaseCapture();
  await ensureOffscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  const saved = await chrome.storage.local.get({ captionTextOnly: false, captionFontPx: 20 });
  const fontPx = clampFont(saved.captionFontPx);
  const response = await sendWhenReady({
    type: "start-capture",
    streamId,
    tabId,
    language: language || "auto",
    apiEnabled: Boolean(apiEnabled),
    apiUrl: apiUrl || "",
    apiKey: apiKey || "",
    apiModel: apiModel || "",
    textOnly: saved.captionTextOnly !== false,
    fontPx,
  });
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "offscreen 启动失败");
  }
  await chrome.storage.session.set({
    running: true,
    tabId,
    language: language || "auto",
    videoWindowId: windowId || null,
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
  const saved = await chrome.storage.local.get({ captionTextOnly: false, captionFontPx: 20 });
  const fontPx = clampFont(saved.captionFontPx);
  chrome.runtime.sendMessage({
    type: "caption-style",
    textOnly: saved.captionTextOnly !== false,
    fontPx,
  }).catch(() => {});
}

