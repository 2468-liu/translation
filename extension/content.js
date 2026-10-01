(() => {
  document.querySelectorAll("#live-subtitle-host").forEach((node) => node.remove());
  if (window.__liveSubtitle && window.__liveSubtitle.version === 2) return;

  let alive = true;
  let port = null;

  function watchPort() {
    port = chrome.runtime.connect({ name: "caption" });
    port.onDisconnect.addListener(() => {
      try {
        watchPort();
      } catch (_error) {
        destroy();
      }
    });
  }

  try {
    watchPort();
  } catch (_error) {
    return;
  }

  const host = document.createElement("div");
  host.id = "live-subtitle-host";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; }
      #root {
        pointer-events: auto;
        width: var(--w, 640px);
        max-width: 92vw;
        color: #fff;
        font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
        -webkit-font-smoothing: antialiased;
        background: transparent;
        border: 0;
        box-shadow: none;
      }
      #cue {
        position: relative;
        text-align: center;
        background: transparent;
        border: 0;
        cursor: grab;
        user-select: none;
      }
      #cue:active { cursor: grabbing; }
      #source, #zh {
        background: transparent;
        border: 0;
        box-shadow: none;
        text-shadow:
          0 0 1px #000,
          0 1px 1px #000,
          0 -1px 1px #000,
          1px 0 1px #000,
          -1px 0 1px #000;
      }
      #source {
        margin: 0 0 4px;
        font-size: calc(var(--font, 26px) * 0.62);
        line-height: 1.35;
        opacity: 0.82;
      }
      #zh {
        margin: 0;
        font-size: var(--font, 26px);
        font-weight: 560;
        line-height: 1.35;
        letter-spacing: 0.01em;
      }
      .partial { opacity: 0.72; }
      .hidden { display: none; }
      .edge {
        position: absolute;
        background: transparent;
        border: 0;
        padding: 0;
      }
      .edge-r { top: 0; right: -8px; width: 16px; height: 100%; cursor: ew-resize; }
      .edge-b { left: 0; bottom: -8px; width: 100%; height: 16px; cursor: ns-resize; }
      #more, #panel { display: none; }
      #root:hover #more, #root.open #more { display: block; }
      #root.open #panel { display: flex; }
      #more {
        margin: 6px auto 0;
        padding: 0 8px;
        border: 0;
        background: transparent;
        color: #fff;
        font: 12px/1 "Segoe UI", sans-serif;
        letter-spacing: 0.2em;
        text-shadow: 0 1px 1px #000;
        cursor: pointer;
      }
      #panel {
        margin-top: 6px;
        justify-content: center;
        align-items: center;
        gap: 12px;
        background: transparent;
        border: 0;
        color: #fff;
        font-size: 12px;
        text-shadow: 0 1px 1px #000;
      }
      #panel label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
      #panel input[type="range"] { width: 72px; accent-color: #fff; cursor: pointer; }
      #panel button {
        border: 0;
        background: transparent;
        color: inherit;
        font: inherit;
        cursor: pointer;
        text-shadow: inherit;
      }
    </style>
    <div id="root">
      <div id="cue">
        <div id="source" class="hidden"></div>
        <div id="zh" class="hidden"></div>
        <div class="edge edge-r" data-resize="x"></div>
        <div class="edge edge-b" data-resize="y"></div>
      </div>
      <button id="more" type="button" aria-label="字幕设置">···</button>
      <div id="panel">
        <label>双语 <input id="bilingual" type="checkbox" /></label>
        <label>字号 <input id="font" type="range" min="16" max="48" step="1" /></label>
        <button id="close" type="button">关闭</button>
      </div>
    </div>
  `;

  const root = shadow.getElementById("root");
  const cue = shadow.getElementById("cue");
  const sourceEl = shadow.getElementById("source");
  const zhEl = shadow.getElementById("zh");
  const moreBtn = shadow.getElementById("more");
  const bilingualInput = shadow.getElementById("bilingual");
  const fontInput = shadow.getElementById("font");

  const state = {
    width: Math.round(Math.min(760, window.innerWidth * 0.62)),
    font: 26,
    dx: 0,
    dy: 0,
    bilingual: false,
    hidden: false,
    widthLocked: false,
    source: "",
    zh: "",
    sourceFinal: true,
    zhFinal: true,
  };
  let timer = 0;
  let open = false;

  function applyBox() {
    root.style.setProperty("--w", `${state.width}px`);
    root.style.setProperty("--font", `${state.font}px`);
    host.style.setProperty("width", `${state.width}px`, "important");
    host.hidden = state.hidden || (!state.zh && !state.source);
  }

  function largestVideo() {
    const videos = [...document.querySelectorAll("video")];
    videos.sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
    return videos.find((video) => video.clientWidth > 160) || null;
  }

  function place() {
    if (!alive) return;
    const parent = document.fullscreenElement || document.body || document.documentElement;
    if (host.parentElement !== parent) parent.appendChild(host);
    const video = largestVideo();
    if (video && !state.widthLocked) {
      state.width = Math.round(Math.min(980, Math.max(220, video.clientWidth * 0.78)));
    }
    host.style.setProperty("position", "fixed", "important");
    host.style.setProperty("z-index", "2147483647", "important");
    host.style.setProperty("margin", "0", "important");
    host.style.setProperty("padding", "0", "important");
    host.style.setProperty("background", "transparent", "important");
    host.style.setProperty("border", "0", "important");
    host.style.setProperty("box-shadow", "none", "important");
    host.style.setProperty("pointer-events", "none", "important");
    if (video) {
      const rect = video.getBoundingClientRect();
      const left = rect.left + (rect.width - state.width) / 2 + state.dx;
      const top = rect.bottom - Math.min(rect.height * 0.2, 110) - state.dy;
      host.style.setProperty("left", `${Math.round(left)}px`, "important");
      host.style.setProperty("top", `${Math.round(top)}px`, "important");
      host.style.setProperty("bottom", "auto", "important");
      host.style.setProperty("transform", "none", "important");
    } else {
      host.style.setProperty("left", `calc(50% + ${state.dx}px)`, "important");
      host.style.setProperty("top", "auto", "important");
      host.style.setProperty("bottom", `calc(9% + ${state.dy}px)`, "important");
      host.style.setProperty("transform", "translateX(-50%)", "important");
    }
    applyBox();
  }

  function paint() {
    const showSource = state.bilingual && state.source;
    sourceEl.textContent = state.source;
    sourceEl.classList.toggle("hidden", !showSource);
    sourceEl.classList.toggle("partial", !state.sourceFinal);
    zhEl.textContent = state.zh;
    zhEl.classList.toggle("hidden", !state.zh);
    zhEl.classList.toggle("partial", !state.zhFinal);
    place();
  }

  function render(immediate) {
    if (immediate) {
      clearTimeout(timer);
      timer = 0;
      paint();
      return;
    }
    if (timer) return;
    timer = setTimeout(() => {
      timer = 0;
      paint();
    }, 200);
  }

  function persist() {
    chrome.storage.local.set({
      cueWidth: state.widthLocked ? state.width : 0,
      cueFont: state.font,
      cueDx: state.dx,
      cueDy: state.dy,
      cueBilingual: state.bilingual,
    });
  }

  chrome.storage.local.get(["cueWidth", "cueFont", "cueDx", "cueDy", "cueBilingual"]).then((saved) => {
    if (saved.cueWidth) {
      state.width = saved.cueWidth;
      state.widthLocked = true;
    }
    if (saved.cueFont) state.font = saved.cueFont;
    if (Number.isFinite(saved.cueDx)) state.dx = saved.cueDx;
    if (Number.isFinite(saved.cueDy)) state.dy = saved.cueDy;
    state.bilingual = Boolean(saved.cueBilingual);
    bilingualInput.checked = state.bilingual;
    fontInput.value = String(state.font);
    place();
  });

  moreBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    open = !open;
    root.classList.toggle("open", open);
  });
  bilingualInput.addEventListener("change", () => {
    state.bilingual = bilingualInput.checked;
    persist();
    render(true);
  });
  fontInput.addEventListener("input", () => {
    state.font = Number(fontInput.value);
    persist();
    applyBox();
  });
  shadow.getElementById("close").addEventListener("click", (event) => {
    event.stopPropagation();
    state.hidden = true;
    place();
  });
  root.addEventListener("mouseleave", () => {
    open = false;
    root.classList.remove("open");
  });

  cue.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    const resize = event.target.dataset && event.target.dataset.resize;
    const origin = {
      x: event.screenX,
      y: event.screenY,
      width: state.width,
      font: state.font,
      dx: state.dx,
      dy: state.dy,
    };
    const move = (ev) => {
      const mx = ev.screenX - origin.x;
      const my = ev.screenY - origin.y;
      if (resize === "x") {
        state.widthLocked = true;
        state.width = Math.max(180, Math.min(1400, origin.width + mx));
      } else if (resize === "y") {
        state.font = Math.max(16, Math.min(56, Math.round(origin.font + my / 4)));
        fontInput.value = String(state.font);
      } else if (!event.target.closest("#panel, #more")) {
        state.dx = origin.dx + mx;
        state.dy = origin.dy - my;
      }
      place();
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      persist();
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });

  document.addEventListener("fullscreenchange", place);
  document.addEventListener("yt-navigate-finish", place);
  const tick = setInterval(place, 1000);
  place();

  function destroy() {
    if (!alive) return;
    alive = false;
    clearInterval(tick);
    document.removeEventListener("fullscreenchange", place);
    document.removeEventListener("yt-navigate-finish", place);
    host.remove();
    if (window.__liveSubtitle && window.__liveSubtitle.version === 2) window.__liveSubtitle = null;
  }

  window.__liveSubtitle = { version: 2, destroy };

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || !message.type) return;
    if (message.type === "overlay-show") {
      state.hidden = false;
      place();
      return;
    }
    if (message.type === "segment_start") {
      state.source = "";
      state.zh = "";
      state.sourceFinal = true;
      state.zhFinal = true;
      render(true);
      return;
    }
    if (message.type === "asr") {
      state.source = message.text || "";
      state.sourceFinal = Boolean(message.final);
      render(Boolean(message.final));
      return;
    }
    if (message.type === "translation") {
      state.zh = message.text || "";
      state.zhFinal = Boolean(message.final);
      render(Boolean(message.final));
      return;
    }
    if (message.type === "capture-error") {
      state.zh = message.error || "采集失败";
      state.zhFinal = true;
      render(true);
    }
  });
})();
