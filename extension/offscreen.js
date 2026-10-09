const WS_URL = "ws://127.0.0.1:8765/ws";

let socket = null;
let audioContext = null;
let stream = null;
let capturing = false;
let desiredStyle = null;

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
  desiredStyle = style;
}

function sendJson(payload) {
  if (!payload || !socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify(payload));
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
  await releaseMedia();
  closeSocket();
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
          chrome.storage.local.get(["captionTextOnly"]).then((stored) => {
            if (stored.captionTextOnly !== nextValue) {
              chrome.storage.local.set({ captionTextOnly: nextValue });
            }
          }).catch(() => {});
          // Echoed back so the server has the applied value. The caption window no
          // longer broadcasts an identical state frame (it compares before sending),
          // so this no longer bounces between the two forever.
          desiredStyle = { type: "style", textOnly: nextValue };
          if (payload.fontPx != null && payload.fontPx !== "") desiredStyle.fontPx = clampFont(payload.fontPx);
          if (typeof payload.fontColor === "string") desiredStyle.color = payload.fontColor;
          sendJson(desiredStyle);
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
      chrome.runtime
        .sendMessage({ type: "capture-error", error: "本地服务已断开" })
        .catch(() => {});
    };
  });
}

async function startCapture(message) {
  await stopCapture(false);
  rememberStyle(message);
  await openSocket(message);
  capturing = true;
}
