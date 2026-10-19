const toggle = document.getElementById("toggle");
const status = document.getElementById("status");
const language = document.getElementById("language");
const targetLanguage = document.getElementById("targetLanguage");
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
const cadenceSelect = document.getElementById("cadenceSelect");
const cadenceSlider = document.getElementById("cadenceSlider");
const cadenceValue = document.getElementById("cadenceValue");
const cadenceReadout = document.getElementById("cadenceReadout");
const cadenceHint = document.getElementById("cadenceHint");
const captionStateHint = document.getElementById("captionStateHint");
const dismissSlider = document.getElementById("dismissSlider");
const dismissValue = document.getElementById("dismissValue");
const dismissReadout = document.getElementById("dismissReadout");
const dismissHint = document.getElementById("dismissHint");

const settingsTrigger = document.getElementById("settingsTrigger");
const settingsDrawer = document.getElementById("settingsDrawer");
const mediaDomain = document.getElementById("mediaDomain");
const mediaTitle = document.getElementById("mediaTitle");
const liveDot = document.getElementById("liveDot");
const sessionStatusText = document.getElementById("sessionStatusText");

// Must equal server/config.py PARTIAL_INTERVAL_MS.
// Nemotron 3.5 ASR uses 80ms chunk multiples. The official recommended default
// for streaming subtitles is 320ms ([56, 4], 4 right-context frames).
const DEFAULT_CADENCE_MS = 320;
const NV_CADENCE_PRESETS = [320, 560, 1120];

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
  "qwen3-in": "Qwen3-ASR + Index 影视翻译（块输出）",
  "nv-in": "Nemotron + Index 翻译（流式）",
  "qwen3-hy": "Qwen3-ASR + Index 影视翻译（兼容）",
  "nv-hy": "Nemotron + Index 翻译（兼容）",
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
  let closest = NV_CADENCE_PRESETS[0];
  let minDiff = Math.abs(v - closest);
  for (const preset of NV_CADENCE_PRESETS) {
    const diff = Math.abs(v - preset);
    if (diff < minDiff) {
      minDiff = diff;
      closest = preset;
    }
  }
  return closest;
}

function paintCadence(ms) {
  const v = clampCadence(ms);
  if (cadenceSelect) cadenceSelect.value = String(v);
  if (cadenceSlider) cadenceSlider.value = String(v);
  if (cadenceValue) cadenceValue.textContent = String(v);
  if (cadenceReadout) cadenceReadout.textContent = String(v);
  if (cadenceHint) {
    if (v === 320) {
      cadenceHint.textContent = "320ms [56, 4]：官方推荐流式首选档，字幕稳健流出，兼顾低延迟与稳定性。";
    } else if (v === 560) {
      cadenceHint.textContent = "560ms [56, 7]：官方高精长句档，长句与视频识别率更佳，抗噪声能力强。";
    } else if (v === 1120) {
      cadenceHint.textContent = "1120ms [56, 14]：超高精翻档，字词识别率最佳，适合离线或长句听写。";
    } else {
      cadenceHint.textContent = `当前步长 ${v}ms。`;
    }
  }
  return v;
}

async function sendCadence(ms) {
  const v = paintCadence(ms);
  await chrome.storage.local.set({ partialIntervalMs: v });
  chrome.runtime.sendMessage({ type: "cadence", partialIntervalMs: v }).catch(() => {});
  if (status) status.textContent = `步长已设为 ${v}ms，下一语音切片自动生效（无需重启）。`;
}

function onCadenceSelectChange() {
  if (cadenceSelect) {
    sendCadence(cadenceSelect.value);
  }
}

function onCadenceInput() {
  if (cadenceSlider) paintCadence(cadenceSlider.value);
  if (cadenceCommit !== null) clearTimeout(cadenceCommit);
  cadenceCommit = setTimeout(() => { cadenceCommit = null; if (cadenceSlider) sendCadence(cadenceSlider.value); }, 400);
}

function onCadenceChange() {
  if (cadenceCommit !== null) { clearTimeout(cadenceCommit); cadenceCommit = null; }
  if (cadenceSlider) sendCadence(cadenceSlider.value);
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
  const stored = await chrome.storage.session.get(["running", "language", "targetLanguage"]);
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
  if (stored.targetLanguage && targetLanguage) targetLanguage.value = stored.targetLanguage;

  // Read active tab title and domain for the session card header
  chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
    if (tab) {
      const title = tab.title || "网页实时音视频字幕";
      let domain = "当前网页";
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

  if (liveDot) liveDot.classList.toggle("active", running);
  if (sessionStatusText) sessionStatusText.textContent = running ? "正在实时采集与翻译" : "服务已就绪";

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
  chrome.storage.local.set({ audioOnly: audioOnly.checked });
  captionPresent = null;
  paintCaptionState();
  if (!audioOnly.checked) askCaptionWindowState();
  status.textContent = audioOnly.checked
    ? "已启用仅音频诊断，后端不会创建字幕窗。"
    : "已恢复显示原生字幕窗。";
});

if (settingsTrigger && settingsDrawer) {
  settingsTrigger.addEventListener("click", () => {
    settingsTrigger.classList.toggle("open");
    settingsDrawer.classList.toggle("open");
  });
}

language.addEventListener("change", () => {
  const val = language.value;
  chrome.storage.session.set({ language: val });
  chrome.runtime.sendMessage({
    type: "language",
    language: val,
  }).catch(() => {});
  const langName = language.options[language.selectedIndex]?.text?.split(" ")[0] || val;
  status.textContent = `语种已设为「${langName}」，下一语音切片自动生效（无需重启）。`;
});

if (targetLanguage) {
  targetLanguage.addEventListener("change", () => {
    const val = targetLanguage.value;
    chrome.storage.session.set({ targetLanguage: val });
    chrome.runtime.sendMessage({
      type: "target-language",
      targetLanguage: val,
    }).catch(() => {});
    const langName = targetLanguage.options[targetLanguage.selectedIndex]?.text?.split(" ")[0] || val;
    status.textContent = `目标语种已设为「${langName}」，下一语音切片自动生效（无需重启）。`;
  });
}

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
if (cadenceSelect) {
  cadenceSelect.addEventListener("change", onCadenceSelectChange);
}
if (cadenceSlider) {
  cadenceSlider.addEventListener("input", onCadenceInput);
  cadenceSlider.addEventListener("change", onCadenceChange);
}
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
      toggle.textContent = "终止中…";
      status.textContent = "正在终止后端并释放模型…";
      const reply = await chrome.runtime.sendMessage({ type: "stop" });
      if (!reply || !reply.ok) {
        throw new Error((reply && reply.error) || "终止失败");
      }
    } else {
      step = "启动后端";
      toggle.textContent = "启动中…";
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
        targetLanguage: targetLanguage ? targetLanguage.value : "zh",
        audioOnly: audioOnly.checked,
        partialIntervalMs: clampCadence(cadenceSelect ? cadenceSelect.value : (cadenceSlider ? cadenceSlider.value : DEFAULT_CADENCE_MS)),
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
