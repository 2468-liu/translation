const toggle = document.getElementById("toggle");
const status = document.getElementById("status");
const language = document.getElementById("language");
const textOnly = document.getElementById("textOnly");
const audioOnly = document.getElementById("audioOnly");
const fontSize = document.getElementById("fontSize");
const fontSizeValue = document.getElementById("fontSizeValue");
const fontColor = document.getElementById("fontColor");
const apiEnabled = document.getElementById("apiEnabled");
const apiFields = document.getElementById("apiFields");
const apiUrl = document.getElementById("apiUrl");
const apiKey = document.getElementById("apiKey");
const apiModel = document.getElementById("apiModel");
const mediaState = document.getElementById("mediaState");
const chainSelect = document.getElementById("chainSelect");
const chainHint = document.getElementById("chainHint");
const cadenceSlider = document.getElementById("cadenceSlider");
const cadenceValue = document.getElementById("cadenceValue");
const cadenceReadout = document.getElementById("cadenceReadout");
const cadenceHint = document.getElementById("cadenceHint");
const captionStateHint = document.getElementById("captionStateHint");
const dismissSlider = document.getElementById("dismissSlider");
const dismissValue = document.getElementById("dismissValue");
const dismissReadout = document.getElementById("dismissReadout");
const dismissHint = document.getElementById("dismissHint");

const captionLabelText = document.getElementById("captionLabelText");
const captionActiveSwitch = document.getElementById("captionActiveSwitch");
const translateActiveSwitch = document.getElementById("translateActiveSwitch");
const settingsTrigger = document.getElementById("settingsTrigger");
const settingsDrawer = document.getElementById("settingsDrawer");
const mediaDomain = document.getElementById("mediaDomain");
const mediaTitle = document.getElementById("mediaTitle");

const LANG_NAME_MAP = {
  auto: "自动检测",
  zh: "中文（简体）",
  ja: "日语（日本語）",
  en: "英语（English）",
  ko: "韩语（한국어）",
  fr: "法语（Français）",
  de: "德语（Deutsch）",
  es: "西班牙语（Español）",
  ru: "俄语（Русский）",
};

function updateCaptionLabel() {
  if (captionLabelText && language) {
    const name = LANG_NAME_MAP[language.value] || language.value;
    captionLabelText.textContent = `实时字幕 - ${name}`;
  }
}

// Must equal server/config.py PARTIAL_INTERVAL_MS. A lower default here would mean
// pressing start silently changes behaviour, and that mismatch has been the cause
// of a control that appeared to do nothing.
const DEFAULT_CADENCE_MS = 750;
const CADENCE_MIN_MS = 250;
const CADENCE_MAX_MS = 1000;
const CADENCE_STEP_MS = 50;

let cadenceCommit = null;
let dismissCommit = null;

function clampDismiss(sec) {
  const v = Number(sec);
  if (!Number.isFinite(v)) return 5;
  return Math.min(15, Math.max(0, Math.round(v)));
}

function paintDismiss(sec) {
  const v = clampDismiss(sec);
  if (dismissSlider) dismissSlider.value = String(v);
  if (dismissValue) dismissValue.textContent = v === 0 ? "常驻不隐去" : `${v} 秒`;
  if (dismissReadout) dismissReadout.textContent = v === 0 ? "常驻" : `${v}s`;
  if (dismissHint) {
    dismissHint.textContent = v === 0
      ? "字幕将持续保留在屏幕上，直到有新台词覆盖。"
      : `静音 ${v} 秒后自动隐去字幕，防止无声场景或切换视频时残留。`;
  }
  return v;
}

async function sendDismiss(sec) {
  const v = paintDismiss(sec);
  await chrome.storage.local.set({ captionDismissSec: v });
  tellCaptionWindow();
}

function onDismissInput() {
  if (dismissSlider) paintDismiss(dismissSlider.value);
  if (dismissCommit !== null) clearTimeout(dismissCommit);
  dismissCommit = setTimeout(() => { dismissCommit = null; if (dismissSlider) sendDismiss(dismissSlider.value); }, 400);
}

function onDismissChange() {
  if (dismissCommit !== null) { clearTimeout(dismissCommit); dismissCommit = null; }
  if (dismissSlider) sendDismiss(dismissSlider.value);
}

// A chain is the whole pipeline -- which models, which ports, and which output mode --
// not one recogniser. runtime.json holds the choice and native_host does the restart, so
// this widget only asks and reports.
const CHAIN_LABELS = {
  "whisper-hy": "whisper + 混元（流式）",
  "qwen3-hy": "Qwen3-ASR + 混元（块输出）",
  "gemma-e2e": "音频直出 Gemma4（流式）",
};

function paintChain(chain, note) {
  if (chain && Object.prototype.hasOwnProperty.call(CHAIN_LABELS, chain)) {
    chainSelect.value = chain;
  }
  chainHint.textContent = note || "切换会重启后端，大约 10 秒；字幕会中断一下。切不过去会自动回退。";
}

function setChainBusy(busy) {
  chainSelect.disabled = busy;
  chainHint.textContent = busy ? "正在重启后端，请稍候…" : chainHint.textContent;
}

function markChainFailed(reason) {
  chainHint.textContent = reason;
  chainHint.style.color = "#9a2f22";
}

function markChainOk(chain) {
  chainHint.textContent = `已切到 ${CHAIN_LABELS[chain] || chain}。`;
  chainHint.style.color = "#3f6b3f";
}

async function switchChain(chain) {
  if (!chain || chain === chainSelect.dataset.applied) return;
  setChainBusy(true);
  captionBusy = true;
  captionPresent = null;
  paintCaptionState();
  status.textContent = `正在切换到 ${CHAIN_LABELS[chain] || chain}，后端会重启…`;
  try {
    const reply = await chrome.runtime.sendMessage({ type: "switch-chain", chain });
    if (reply && reply.ok) {
      chainSelect.dataset.applied = chain;
      markChainOk(reply.chain || chain);
      status.textContent = `已切到 ${CHAIN_LABELS[reply.chain] || reply.chain}，字幕会自动回来。`;
    } else {
      if (reply && reply.rolled_back && reply.chain) {
        chainSelect.value = reply.chain;
        chainSelect.dataset.applied = reply.chain;
      }
      markChainFailed((reply && reply.error) || "切换失败");
      status.textContent = (reply && reply.error) || "线路切换失败";
    }
  } catch (error) {
    markChainFailed(`切换失败：${String((error && error.message) || error)}`);
    status.textContent = `线路切换失败：${String((error && error.message) || error)}`;
  } finally {
    setChainBusy(false);
    captionBusy = false;
    paintCaptionState();
    if (!audioOnly.checked) askCaptionWindowState();
  }
}

function clampCadence(ms) {
  const v = Number(ms);
  if (!Number.isFinite(v)) return DEFAULT_CADENCE_MS;
  return Math.min(CADENCE_MAX_MS, Math.max(CADENCE_MIN_MS,
    Math.round(v / CADENCE_STEP_MS) * CADENCE_STEP_MS));
}

function paintCadence(ms) {
  const v = clampCadence(ms);
  cadenceSlider.value = String(v);
  cadenceValue.textContent = String(v);
  cadenceReadout.textContent = String(v);
  cadenceHint.textContent = v <= CADENCE_MIN_MS + 50
    ? "跟得最紧，改口也最频繁；字幕改来改去的时间最多。"
    : v >= 900
      ? "改口最少，但第一句和换句都会更晚出现。"
      : "改口与首句延迟之间的折中。";
  return v;
}

async function sendCadence(ms) {
  const v = paintCadence(ms);
  await chrome.storage.local.set({ partialIntervalMs: v });
  chrome.runtime.sendMessage({ type: "cadence", partialIntervalMs: v }).catch(() => {});
}

function onCadenceInput() {
  paintCadence(cadenceSlider.value);
  if (cadenceCommit !== null) clearTimeout(cadenceCommit);
  cadenceCommit = setTimeout(() => { cadenceCommit = null; sendCadence(cadenceSlider.value); }, 400);
}

function onCadenceChange() {
  if (cadenceCommit !== null) { clearTimeout(cadenceCommit); cadenceCommit = null; }
  sendCadence(cadenceSlider.value);
}

function apiPayload() {
  return {
    apiEnabled: apiEnabled.checked,
    apiUrl: apiUrl.value.trim(),
    apiKey: apiKey.value.trim(),
    apiModel: apiModel.value.trim(),
  };
}

function showApiFields() {
  apiFields.hidden = !apiEnabled.checked;
}

function paintColorBar(color) {
  if (/^#[0-9a-f]{6}$/i.test(color)) {
    fontColor.style.background = color;
  }
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !Object.prototype.hasOwnProperty.call(changes, "captionTextOnly")) return;
  const next = Boolean(changes.captionTextOnly.newValue);
  textOnly.checked = next;
  captionLive = next;
  if (captionPresent === null) captionPresent = true;
  paintCaptionState();
  status.textContent = captionTextOnlyNote(next);
});

let captionPresent = null; // null = 还没问过；true/false = 服务端说有没有窗口
let captionLive = null;    // 有窗口时，问出来的 只有字 真值
let captionBusy = false;   // 正在重启后端（线路切换）：期间不问、不报

function paintCaptionState() {
  if (captionBusy) {
    captionStateHint.textContent = "正在重启后端，字幕窗会自动回来。";
    return;
  }
  if (captionPresent === null) {
    captionStateHint.textContent = "正在确认字幕窗状态…";
    return;
  }
  if (!captionPresent) {
    captionStateHint.textContent = audioOnly.checked
      ? "仅音频模式：不会创建字幕窗。下面的设置下次开始时生效。"
      : "字幕窗未运行 —— 下面这些是下次开始时用的设置。";
    return;
  }
  captionStateHint.textContent = captionLive
    ? "字幕窗当前：只有字（黑框和「只有字」都隐藏了）。"
    : "字幕窗当前：黑底框，「只有字」在最左边。";
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || !message.type) return false;
  if (message.type === "toggle_transparent") {
    captionPresent = true;
    captionLive = Boolean(message.value);
    textOnly.checked = captionLive;
    paintCaptionState();
    return false;
  }
  if (message.type === "caption-absent") {
    captionPresent = false;
    captionLive = null;
    paintCaptionState();
    setTimeout(() => {
      if (!captionBusy && !audioOnly.checked) askCaptionWindowState();
    }, 1500);
    return false;
  }
  if (message.type === "capture-restored") {
    status.textContent = "本地服务已重连，字幕继续。";
    if (!audioOnly.checked) askCaptionWindowState();
    return false;
  }
  return false;
});

function captionTextOnlyNote(on) {
  return on
    ? "字幕窗已切到「只有字」：黑框和「只有字」都隐藏了。在这个插件里再点一下即可恢复。"
    : "字幕窗已恢复黑底框和「只有字」控制。";
}

function tellCaptionWindow() {
  chrome.runtime.sendMessage({
    type: "caption-style",
    textOnly: textOnly.checked,
    fontPx: Number(fontSize.value) || 20,
    color: fontColor.value,
    dismissMs: clampDismiss(dismissSlider ? dismissSlider.value : 5) * 1000,
  }).catch(() => {});
}

function askCaptionWindowState() {
  chrome.runtime.sendMessage({ type: "caption-state" })
    .then((reply) => {
      if (!reply || !reply.sent) {
        captionPresent = false;
        captionLive = null;
        paintCaptionState();
      } else if (reply && typeof reply.currentTextOnly === "boolean") {
        captionPresent = true;
        captionLive = reply.currentTextOnly;
        textOnly.checked = reply.currentTextOnly;
        paintCaptionState();
      }
    })
    .catch(() => {});
}

async function refresh(keepMessage, failedStep) {
  const stored = await chrome.storage.session.get(["running", "language"]);
  const theme = await chrome.storage.local.get({
    captionTextOnly: false,
    audioOnly: false,
    captionFontPx: 20,
    captionColor: "#ffffff",
    captionDismissSec: 5,
    apiEnabled: false,
    apiUrl: "",
    apiKey: "",
    apiModel: "",
    partialIntervalMs: DEFAULT_CADENCE_MS,
  });
  paintCadence(theme.partialIntervalMs);
  paintDismiss(theme.captionDismissSec !== undefined ? theme.captionDismissSec : 5);
  textOnly.checked = Boolean(theme.captionTextOnly);
  audioOnly.checked = Boolean(theme.audioOnly);
  if (captionActiveSwitch) captionActiveSwitch.checked = !Boolean(theme.audioOnly);

  captionPresent = null;
  paintCaptionState();
  if (!audioOnly.checked) askCaptionWindowState();
  const px = Math.max(16, Math.min(40, Number(theme.captionFontPx) || 20));
  fontSize.value = String(px);
  fontSizeValue.textContent = String(px);
  fontColor.value = theme.captionColor || "#ffffff";
  paintColorBar(fontColor.value);
  apiEnabled.checked = Boolean(theme.apiEnabled);
  apiUrl.value = theme.apiUrl || "";
  apiKey.value = theme.apiKey || "";
  apiModel.value = theme.apiModel || "";
  showApiFields();

  let running = Boolean(stored.running);
  let hostUp = null;
  let hostReachable = true;
  try {
    const offscreen = await chrome.runtime.sendMessage({ type: "get-status" });
    if (offscreen && typeof offscreen.running === "boolean") running = offscreen.running;
  } catch (_error) {
    running = false;
  }
  if (stored.language) language.value = stored.language;
  updateCaptionLabel();

  // Query active tab title and domain for the media card
  chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
    if (tab) {
      const title = tab.title || "网页实时音视频字幕";
      let domain = "bilibili.com";
      try {
        if (tab.url && tab.url.startsWith("http")) domain = new URL(tab.url).hostname.replace(/^www\./, "");
      } catch (_) {}
      if (mediaDomain && domain) mediaDomain.textContent = domain;
      if (mediaTitle && title) mediaTitle.textContent = title;
    }
  }).catch(() => {});

  try {
    const probe = await chrome.runtime.sendMessage({ type: "ping-backend" });
    const reply = probe && probe.reply;
    const live = reply && reply.chain;
    if (live) {
      chainSelect.value = live;
      chainSelect.dataset.applied = live;
    }
    if (reply && typeof reply.up === "boolean") hostUp = reply.up;
  } catch (_error) {
    hostReachable = false;
  }
  if (hostUp !== null) running = running || hostUp;

  toggle.textContent = running ? "终止后端" : "开始";
  toggle.classList.toggle("stop", running);
  toggle.setAttribute("aria-label", running ? "终止后端" : "开始");
  toggle.innerHTML = running
    ? '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg><span class="sr-only">终止后端</span>'
    : '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><polygon points="7,5 19,12 7,19"/></svg><span class="sr-only">开始</span>';

  mediaState.textContent = running ? "●" : "○";
  mediaState.style.color = running ? "#806900" : "#aaa39b";

  const stale = keepMessage && failedStep === "启动后端" && running;
  if (stale) {
    console.info("[字幕] 上次「启动后端失败」已不成立：后端已经在跑，清掉那行字");
    keepMessage = "";
  }
  if (keepMessage) {
    status.textContent = keepMessage;
  } else if (!hostReachable) {
    status.textContent = "本地后端宿主连不上，无法确认是否在运行。";
  } else if (running && !Boolean(stored.running)) {
    status.textContent = "本地后端仍在运行（不是本次会话启动的）。点「终止后端」可停止。";
  } else {
    status.textContent = running
      ? "正在采集。字幕由 Windows 原生置顶窗口显示，不改网页内容或全屏状态。"
      : "未开始";
  }
  if (keepMessage) status.classList.add("error");
  else status.classList.remove("error");
}

textOnly.addEventListener("change", () => {
  chrome.storage.local.set({ captionTextOnly: textOnly.checked });
  tellCaptionWindow();
  if (captionPresent) {
    captionLive = textOnly.checked;
    paintCaptionState();
  }
  status.textContent = captionTextOnlyNote(textOnly.checked);
});

audioOnly.addEventListener("change", () => {
  if (captionActiveSwitch) captionActiveSwitch.checked = !audioOnly.checked;
  chrome.storage.local.set({ audioOnly: audioOnly.checked });
  captionPresent = null;
  paintCaptionState();
  if (!audioOnly.checked) askCaptionWindowState();
  status.textContent = audioOnly.checked
    ? "已启用仅音频诊断，后端不会创建字幕窗。"
    : "已恢复显示原生字幕窗。";
});

if (captionActiveSwitch) {
  captionActiveSwitch.addEventListener("change", () => {
    audioOnly.checked = !captionActiveSwitch.checked;
    audioOnly.dispatchEvent(new Event("change"));
  });
}

if (settingsTrigger && settingsDrawer) {
  settingsTrigger.addEventListener("click", () => {
    settingsTrigger.classList.toggle("open");
    settingsDrawer.classList.toggle("open");
  });
}

language.addEventListener("change", () => {
  updateCaptionLabel();
  chrome.storage.session.set({ language: language.value });
});

fontSize.addEventListener("input", () => {
  const px = Math.max(16, Math.min(40, Number(fontSize.value) || 20));
  fontSizeValue.textContent = String(px);
  chrome.storage.local.set({ captionFontPx: px });
  tellCaptionWindow();
});

fontColor.addEventListener("input", () => {
  paintColorBar(fontColor.value);
  chrome.storage.local.set({ captionColor: fontColor.value });
  tellCaptionWindow();
  status.textContent = `字幕颜色已设为 ${fontColor.value.toUpperCase()}。`;
});

document.querySelectorAll(".color-dot").forEach((dot) => {
  dot.addEventListener("click", () => {
    const color = dot.dataset.color;
    if (color) {
      fontColor.value = color;
      paintColorBar(color);
      chrome.storage.local.set({ captionColor: color });
      tellCaptionWindow();
      status.textContent = `字幕颜色已设为 ${color.toUpperCase()}。`;
    }
  });
});

function saveApi() {
  showApiFields();
  const payload = apiPayload();
  chrome.storage.local.set(payload);
  chrome.runtime.sendMessage({ type: "translate-config", ...payload }).catch(() => {});
}

apiEnabled.addEventListener("change", saveApi);
for (const field of [apiUrl, apiKey, apiModel]) {
  field.addEventListener("change", saveApi);
}
cadenceSlider.addEventListener("input", onCadenceInput);
cadenceSlider.addEventListener("change", onCadenceChange);
if (dismissSlider) {
  dismissSlider.addEventListener("input", onDismissInput);
  dismissSlider.addEventListener("change", onDismissChange);
}
if (chainSelect) {
  chainSelect.addEventListener("change", () => {
    if (chainHint) chainHint.style.color = "";
    switchChain(chainSelect.value);
  });
}

toggle.addEventListener("click", async () => {
  toggle.disabled = true;
  let step = "开始";
  let failure = "";
  try {
    const running = toggle.classList.contains("stop");
    if (running) {
      step = "终止";
      status.textContent = "正在终止后端并释放模型…";
      const reply = await chrome.runtime.sendMessage({ type: "stop" });
      if (!reply || !reply.ok) {
        throw new Error((reply && reply.error) || "终止失败");
      }
    } else {
      status.textContent = "正在启动本地服务和模型，并显示原生置顶字幕…";
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) throw new Error("没有可用标签页");
      const api = apiPayload();
      if (api.apiEnabled && (!api.apiUrl || !api.apiModel)) {
        throw new Error("外部接口要填地址和模型名");
      }
      chrome.storage.local.set(api);
      step = "启动后端";
      const ready = await chrome.runtime.sendMessage({ type: "ping-backend" });
      if (ready && ready.ok === false) throw new Error(ready.error);
      step = "启动字幕窗";
      const response = await chrome.runtime.sendMessage({
        type: "start",
        tabId: tab.id,
        language: language.value,
        audioOnly: audioOnly.checked,
        partialIntervalMs: clampCadence(cadenceSlider.value),
        dismissMs: clampDismiss(dismissSlider ? dismissSlider.value : 5) * 1000,
        ...api,
      });
      if (!response || !response.ok) {
        throw new Error((response && response.error) || "启动失败");
      }
    }
  } catch (error) {
    failure = `${step}失败：${String((error && error.message) || error)}`;
    console.error("[字幕]", failure, error);
  } finally {
    toggle.disabled = false;
    await refresh(failure, step);
  }
});

refresh();
