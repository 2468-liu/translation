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
  return false;
});

function configPayload(message) {
  const payload = {
    type: "config",
    apiEnabled: Boolean(message.apiEnabled),
    apiUrl: message.apiUrl || "",
    apiKey: message.apiKey || "",
    apiModel: message.apiModel || "",
  };
  if (message.language) payload.language = message.language;
  if (typeof message.textOnly === "boolean") payload.textOnly = message.textOnly;
  if (message.fontPx != null && message.fontPx !== "") payload.fontPx = clampFont(message.fontPx);
  return payload;
}

function rememberStyle(message) {
  const style = { type: "style" };
  if (typeof message.textOnly === "boolean") style.textOnly = message.textOnly;
  if (message.fontPx != null && message.fontPx !== "") style.fontPx = clampFont(message.fontPx);
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
      sendJson({ type: "arm" });
      if (desiredStyle) sendJson(desiredStyle);
      resolve();
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        chrome.runtime.sendMessage(JSON.parse(event.data)).catch(() => {});
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
  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: message.streamId,
      },
    },
    video: false,
  });
  audioContext = new AudioContext();
  if (audioContext.state === "suspended") await audioContext.resume();
  const source = audioContext.createMediaStreamSource(stream);
  source.connect(audioContext.destination);
  await audioContext.audioWorklet.addModule(chrome.runtime.getURL("pcm-worklet.js"));
  const worklet = new AudioWorkletNode(audioContext, "pcm-processor");
  source.connect(worklet);
  await openSocket(message);
  worklet.port.onmessage = (event) => {
    if (!capturing || !socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(event.data);
  };
  capturing = true;
}
