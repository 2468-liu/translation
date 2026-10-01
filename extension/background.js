let captureError = "";

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
  if (message.type === "show-overlay") {
    chrome.runtime.sendMessage({ type: "raise-caption" }).catch(() => {});
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "focus-video") {
    focusVideo().then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) })
    );
    return true;
  }
  if (message.type === "overlay-closed") {
    closeOverlay().then(
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

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (!changes.captionTextOnly && !changes.captionFontPx) return;
  applyCaptionStyle(Boolean(changes.captionTextOnly));
});

chrome.windows.onRemoved.addListener(async (windowId) => {
  const stored = await chrome.storage.session.get("overlayWindowId");
  if (stored.overlayWindowId === windowId) {
    await chrome.storage.session.remove("overlayWindowId");
  }
});

async function silencePage(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => {
        document.querySelectorAll("#live-subtitle-host").forEach((node) => node.remove());
        if (window.__liveSubtitle && window.__liveSubtitle.destroy) window.__liveSubtitle.destroy();
        if (window.__subtitleAudio && window.__subtitleAudio.stop) window.__subtitleAudio.stop();
        window.__subtitleAudio = null;
      },
    });
  } catch (_error) {
    // the tab may already be closed
  }
}

function clampFont(px) {
  const size = Number(px);
  if (!Number.isFinite(size)) return 20;
  return Math.max(16, Math.min(40, Math.round(size)));
}

async function start({ tabId, language, windowId, apiEnabled, apiUrl, apiKey, apiModel }) {
  captureError = "";
  await silencePage(tabId);
  // Chrome keeps a tab captured until its tracks end. Release the previous
  // consumer before asking for another id, or the same tab stays dead.
  await releaseCapture();
  await ensureOffscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  const saved = await chrome.storage.local.get({ captionTextOnly: true, captionFontPx: 20 });
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
  await applyCaptionStyle(false);
}

async function stop() {
  const stored = await chrome.storage.session.get("tabId");
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
  if (stored.tabId) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: stored.tabId, allFrames: true },
        func: () => {
          document.querySelectorAll("#live-subtitle-host").forEach((node) => node.remove());
          if (window.__liveSubtitle && window.__liveSubtitle.destroy) window.__liveSubtitle.destroy();
          if (window.__subtitleAudio && window.__subtitleAudio.stop) window.__subtitleAudio.stop();
          window.__subtitleAudio = null;
        },
      });
    } catch (_error) {
      // the tab may already be closed
    }
  }
  await closeOverlay();
  await chrome.storage.session.set({ running: false });
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

async function injectCaption(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"],
  });
}

async function showCaption(tabId) {
  const stored = await chrome.storage.session.get("tabId");
  const target = tabId || stored.tabId;
  if (!target) return;
  await injectCaption(target);
  try {
    await chrome.tabs.sendMessage(target, { type: "overlay-show" });
  } catch (_error) {
    // the script's own listener is registered by the injection above
  }
}

function forwardCaption(_payload) {}

async function openOverlay(windowId) {
  const stored = await chrome.storage.session.get("overlayWindowId");
  if (stored.overlayWindowId) {
    try {
      await chrome.windows.update(stored.overlayWindowId, {
        state: "normal",
        focused: true,
        drawAttention: true,
      });
      return;
    } catch (_error) {
      await chrome.storage.session.remove("overlayWindowId");
    }
  }
  let anchor = null;
  if (windowId) {
    try {
      anchor = await chrome.windows.get(windowId);
    } catch (_error) {
      anchor = null;
    }
  }
  if (!anchor) {
    anchor = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
  }
  const saved = await chrome.storage.local.get({ captionWidth: 640, captionHeight: 168 });
  const width = saved.captionWidth || 640;
  const height = saved.captionHeight || 168;
  const left = Math.max(0, Math.round((anchor.left || 0) + ((anchor.width || 1200) - width) / 2));
  const top = Math.max(0, Math.round((anchor.top || 0) + (anchor.height || 800) - height - 120));
  const spec = {
    url: chrome.runtime.getURL("overlay.html"),
    type: "popup",
    width,
    height,
    left,
    top,
    focused: true,
    state: "normal",
  };
  const created = await chrome.windows.create(spec);
  await chrome.storage.session.set({ overlayWindowId: created.id });
}

async function applyCaptionStyle(removePageCaption) {
  const saved = await chrome.storage.local.get({ captionTextOnly: true, captionFontPx: 20 });
  const fontPx = clampFont(saved.captionFontPx);
  chrome.runtime.sendMessage({
    type: "caption-style",
    textOnly: saved.captionTextOnly !== false,
    fontPx,
  }).catch(() => {});
  if (!removePageCaption) return;
  const stored = await chrome.storage.session.get("tabId");
  if (!stored.tabId) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: stored.tabId },
      func: () => {
        document.querySelectorAll("#live-subtitle-host").forEach((node) => node.remove());
        if (window.__liveSubtitle && window.__liveSubtitle.destroy) window.__liveSubtitle.destroy();
      },
    });
  } catch (_error) {
    // the tab may already be closed
  }
}

async function closeOverlay() {
  const stored = await chrome.storage.session.get("overlayWindowId");
  if (!stored.overlayWindowId) return;
  try {
    await chrome.windows.remove(stored.overlayWindowId);
  } catch (_error) {
    // already closed
  }
  await chrome.storage.session.remove("overlayWindowId");
}

async function closeStrayOverlays() {
  const wins = await chrome.windows.getAll({ populate: true });
  await Promise.all(
    wins
      .filter((win) => (win.tabs || []).some((tab) => (tab.url || "").includes("/overlay.html")))
      .map((win) => chrome.windows.remove(win.id).catch(() => {}))
  );
}

closeStrayOverlays();

async function focusVideo() {
  const stored = await chrome.storage.session.get("videoWindowId");
  if (!stored.videoWindowId) return;
  try {
    await chrome.windows.update(stored.videoWindowId, { focused: true });
  } catch (_error) {
    // video window may have been closed
  }
}
