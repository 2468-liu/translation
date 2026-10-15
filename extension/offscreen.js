const WS_URL = "ws://127.0.0.1:8765/ws";

let socket = null;
let audioContext = null;
let stream = null;
let capturing = false;
let desiredStyle = null;
// The last start-capture request, kept so a reconnect can resend the same opening
// frames (config carries language / api / cadence / style) without the popup.
let lastStart = null;

function clampFont(px) {
  const size = Number(px);
  if (!Number.isFinite(size)) return 20;
  return Math.max(16, Math.min(40, Math.round(size)));
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) return false;
  if (message.type === "start-capture") {
    startCapture(message).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "stop-capture") {
    stopCapture(true).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "release-capture") {
    stopCapture(false).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "get-status") {
    sendResponse({ running: capturing });
    return false;
  }
  if (message.type === "raise-caption") {
    sendJson({ type: "raise" });
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "caption-style") {
    rememberStyle(message);
    sendJson(desiredStyle);
    sendResponse({ ok: true });
    return false;
  }
  // "What is the caption window set to right now?", asked by the popup when it opens.
  //
  // The popup cannot read storage for this: storage is only what the last start will
  // read, and the window's own control (which is hidden in 只有字 mode, by design) can
  // move the state without storage hearing about it. The server does not keep a copy
  // either -- it asks the window -- so the answer comes back as a toggle_transparent
  // frame, is written to storage here, and the popup's checkbox ends up showing the
  // window's real state. That is what makes the first press work.
  //
  // `sent` says whether the question actually went out. False means there is no live
  // caption window to ask (nothing running, or audio-only mode), which is different
  // from a window that was asked and did not answer -- and only the second one is a
  // fault worth reporting to the user.
  if (message.type === "caption-state") {
    sendResponse({ ok: true, sent: sendJson({ type: "get_state" }) });
    return false;
  }
  if (message.type === "hide-caption") {
    sendJson({ type: "hide" });
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "translate-config") {
    sendJson(configPayload(message));
    sendResponse({ ok: true });
    return false;
  }
  // The cadence switch, pressed while running. configPayload would also work but
  // would drag the api/style fields along with defaults, so this sends just the
  // one field the user changed.
  if (message.type === "cadence") {
    const ms = Number(message.partialIntervalMs);
    if (ms > 0) sendJson({ type: "config", partial_interval_ms: ms });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

function configPayload(message) {
  const payload = {
    type: "config",
    apiEnabled: Boolean(message.apiEnabled),
    apiUrl: message.apiUrl || "",
    apiKey: message.apiKey || "",
    apiModel: message.apiModel || "",
    audioOnly: Boolean(message.audioOnly),
  };
  if (message.language) payload.language = message.language;
  if (typeof message.textOnly === "boolean") payload.textOnly = message.textOnly;
  if (message.fontPx != null && message.fontPx !== "") payload.fontPx = clampFont(message.fontPx);
  if (typeof message.color === "string") payload.color = message.color;
  if (message.dismissMs != null && message.dismissMs !== "") payload.dismissMs = Number(message.dismissMs);
  // The cadence switch. Carried on the same config frame as the rest so a start
  // already opens on the chosen value; ws_server applies it like any other.
  if (Number(message.partialIntervalMs) > 0) {
    payload.partial_interval_ms = Number(message.partialIntervalMs);
  }
  return payload;
}

function rememberStyle(message) {
  const style = { type: "style" };
  if (typeof message.textOnly === "boolean") style.textOnly = message.textOnly;
  if (message.fontPx != null && message.fontPx !== "") style.fontPx = clampFont(message.fontPx);
  if (typeof message.color === "string") style.color = message.color;
  if (message.dismissMs != null && message.dismissMs !== "") style.dismissMs = Number(message.dismissMs);
  desiredStyle = style;
}

function sendJson(payload) {
  if (!payload || !socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(payload));
  return true;
}

function tracksEnded(media) {
  const tracks = media ? media.getTracks() : [];
  if (!tracks.length) return Promise.resolve();
  const pending = tracks.map((track) => {
    if (track.readyState === "ended") return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        track.removeEventListener("ended", done);
        resolve();
      };
      track.addEventListener("ended", done);
      track.stop();
    });
  });
  return Promise.race([
    Promise.all(pending),
    new Promise((resolve) => setTimeout(resolve, 400)),
  ]);
}

async function releaseMedia() {
  const media = stream;
  stream = null;
  await tracksEnded(media);
  if (audioContext) {
    const current = audioContext;
    audioContext = null;
    await current.close().catch(() => {});
  }
}

function closeSocket() {
  if (!socket) return;
  const current = socket;
  socket = null;
  current.onclose = null;
  current.onerror = null;
  current.close();
}

async function stopCapture(hide) {
  if (hide) sendJson({ type: "hide" });
  capturing = false;
  cancelReconnect();
  await releaseMedia();
  closeSocket();
}

// Coming back on its own, because the thing that disconnects it is usually us.
//
// Switching the line in the popup asks the native host to kill and restart the backend,
// which drops this socket AND takes the caption window down with it (the window is a
// child of that process). Until now nothing reconnected: the user pressed 开始 again,
// which is why a line switch worked "sometimes" -- and while they were deciding, the
// popup was asking a caption window that did not exist yet. Capture itself lives in the
// server (WASAPI loopback), so there is no audio graph to rebuild here: reconnect, resend
// the opening frames, and the pipeline picks the same tab up again.
const RECONNECT_MS = 1200;
const RECONNECT_GIVE_UP_MS = 200000;
let reconnectTimer = null;
let reconnectDeadline = 0;
let reconnectTries = 0;

function cancelReconnect() {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  reconnectDeadline = 0;
  reconnectTries = 0;
}

function scheduleReconnect() {
  if (!capturing || reconnectTimer !== null) return;
  if (reconnectDeadline === 0) reconnectDeadline = Date.now() + RECONNECT_GIVE_UP_MS;
  if (Date.now() > reconnectDeadline) {
    chrome.runtime
      .sendMessage({ type: "capture-error", error: "本地服务断开，重连超时" })
      .catch(() => {});
    return;
  }
  reconnectTries += 1;
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    try {
      await openSocket(lastStart || {});
      reconnectTries = 0;
      reconnectDeadline = 0;
      chrome.runtime.sendMessage({ type: "capture-restored" }).catch(() => {});
    } catch (_error) {
      scheduleReconnect();
    }
  }, RECONNECT_MS);
}

function openSocket(config) {
  return new Promise((resolve, reject) => {
    socket = new WebSocket(WS_URL);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      socket.send(JSON.stringify(configPayload(config)));
      socket.send(JSON.stringify({ type: "get_state" }));
      sendJson({ type: "media-start" });
      sendJson({ type: "arm" });
      if (desiredStyle) sendJson(desiredStyle);
      resolve();
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const payload = JSON.parse(event.data);
        if (payload && payload.type === "toggle_transparent") {
          const nextValue = Boolean(payload.value);
          chrome.storage.local.set({ captionTextOnly: nextValue }).catch(() => {});
          if (!desiredStyle) desiredStyle = { type: "style" };
          desiredStyle.textOnly = nextValue;
          if (payload.fontPx != null && payload.fontPx !== "") desiredStyle.fontPx = clampFont(payload.fontPx);
          if (typeof payload.fontColor === "string") desiredStyle.color = payload.fontColor;
        }
        chrome.runtime.sendMessage(payload).catch(() => {});
      } catch (_error) {
        // ignore malformed payloads
      }
    };
    socket.onerror = () => {
      reject(new Error("连不上本地服务 ws://127.0.0.1:8765/ws"));
    };
    socket.onclose = () => {
      if (!capturing) return;
      // Not an error yet: the backend may be restarting (a line switch does exactly
      // that). Report only when the retries run out, so the status line does not cry
      // wolf every time the user changes line.
      scheduleReconnect();
    };
  });
}

async function startCapture(message) {
  await stopCapture(false);
  rememberStyle(message);
  lastStart = message;
  await openSocket(message);
  capturing = true;
}
