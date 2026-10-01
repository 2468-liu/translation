const DEFAULTS = {
  captionTextOnly: true,
  captionBilingual: false,
};

function mountCaption() {
  const style = document.createElement("style");
  style.textContent = `
    html, body {
      margin: 0;
      height: 100%;
      background: #141414;
      color: #fff;
    }
    body {
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    }
    #app {
      box-sizing: border-box;
      height: 100%;
      padding: 28px 16px 12px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
    }
    #app.text-only, #app.text-only body {
      background: transparent;
    }
    header {
      position: absolute;
      top: 6px;
      right: auto;
      left: 8px;
      display: flex;
      justify-content: flex-start;
    }
    #textOnlyControl { margin-right: 8px; }
    #panel { display: none; gap: 10px; align-items: center; }
    #app.open #panel, #app:hover #panel { display: flex; }
    #more {
      border: 0;
      background: transparent;
      color: #fff;
      letter-spacing: 0.18em;
      cursor: pointer;
    }
    label, button {
      font: 12px/1 "Microsoft YaHei", "Segoe UI", sans-serif;
      color: inherit;
    }
    label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
    #close {
      border: 0;
      background: transparent;
      cursor: pointer;
    }
    #source, #zh {
      margin: 0;
      text-align: center;
      word-break: break-word;
    }
    #source { font-size: 13px; opacity: 0.8; }
    #zh { font-size: 22px; line-height: 1.35; font-weight: 560; }
    #app.text-only #source, #app.text-only #zh {
      text-shadow: 0 0 1px #000, 0 1px 1px #000, 0 -1px 1px #000, 1px 0 1px #000, -1px 0 1px #000;
    }
    .partial { opacity: 0.72; }
    .hidden { display: none; }
  `;
  document.head.appendChild(style);
  document.body.innerHTML = `
    <div id="app">
      <header>
        <label id="textOnlyControl">只有字 <input id="textOnly" type="checkbox" /></label>
        <button id="more" type="button" aria-label="字幕设置">···</button>
        <div id="panel">
          <label>双语 <input id="bilingual" type="checkbox" /></label>
          <button id="close" type="button">关闭</button>
        </div>
      </header>
      <div id="source" class="hidden"></div>
      <div id="zh">正在听…</div>
    </div>
  `;

  const app = document.getElementById("app");
  const sourceEl = document.getElementById("source");
  const zhEl = document.getElementById("zh");
  const textOnlyInput = document.getElementById("textOnly");
  const bilingualInput = document.getElementById("bilingual");
  const state = {
    source: "",
    zh: "",
    sourceFinal: true,
    zhFinal: true,
    textOnly: false,
    bilingual: false,
    open: false,
  };
  document.title = "字幕";

  function paint() {
    app.classList.toggle("text-only", state.textOnly);
    app.classList.toggle("open", state.open);
    document.documentElement.style.background = state.textOnly ? "transparent" : "#141414";
    document.body.style.background = state.textOnly ? "transparent" : "#141414";
    const showSource = state.bilingual && state.source;
    sourceEl.textContent = state.source;
    sourceEl.classList.toggle("hidden", !showSource);
    sourceEl.classList.toggle("partial", !state.sourceFinal);
    zhEl.textContent = state.zh || "正在听…";
    zhEl.classList.toggle("partial", Boolean(state.zh) && !state.zhFinal);
  }

  chrome.storage.local.get(DEFAULTS).then((saved) => {
    state.textOnly = Boolean(saved.captionTextOnly);
    state.bilingual = Boolean(saved.captionBilingual);
    textOnlyInput.checked = state.textOnly;
    bilingualInput.checked = state.bilingual;
    paint();
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.captionTextOnly) {
      state.textOnly = Boolean(changes.captionTextOnly.newValue);
      textOnlyInput.checked = state.textOnly;
    }
    if (changes.captionBilingual) {
      state.bilingual = Boolean(changes.captionBilingual.newValue);
      bilingualInput.checked = state.bilingual;
    }
    paint();
  });

  document.getElementById("more").addEventListener("click", () => {
    state.open = !state.open;
    paint();
  });
  textOnlyInput.addEventListener("change", () => {
    chrome.storage.local.set({ captionTextOnly: textOnlyInput.checked });
  });
  bilingualInput.addEventListener("change", () => {
    chrome.storage.local.set({ captionBilingual: bilingualInput.checked });
  });
  document.getElementById("close").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "overlay-closed" }).catch(() => {});
    window.close();
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || !message.type) return;
    if (message.type === "segment_start") {
      state.source = "";
      state.zh = "";
      state.sourceFinal = true;
      state.zhFinal = true;
      paint();
      return;
    }
    if (message.type === "asr") {
      state.source = message.text || "";
      state.sourceFinal = Boolean(message.final);
      paint();
      return;
    }
    if (message.type === "translation") {
      state.zh = message.text || "";
      state.zhFinal = Boolean(message.final);
      paint();
      return;
    }
    if (message.type === "capture-error") {
      state.zh = message.error || "采集失败";
      state.zhFinal = true;
      paint();
    }
  });

  paint();
}

mountCaption();
