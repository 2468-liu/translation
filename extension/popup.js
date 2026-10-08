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

// Must equal server/config.py PARTIAL_INTERVAL_MS. A lower default here would mean
// pressing start silently changes behaviour, and that mismatch has been the cause
// of a control that appeared to do nothing.
const DEFAULT_CADENCE_MS = 750;
const CADENCE_MIN_MS = 250;
const CADENCE_MAX_MS = 1000;
const CADENCE_STEP_MS = 50;

let cadenceCommit = null;

// A chain is the whole pipeline -- which models, which ports -- not one recogniser.
// runtime.json holds the choice and native_host does the restart, so this widget only
// asks and reports.
const CHAIN_LABELS = {
  "whisper-hy": "whisper + 混元",
  "qwen3-hy": "Qwen3-ASR + 混元",
};

function paintChain(chain, note) {
  if (chain && Object.prototype.hasOwnProperty.call(CHAIN_LABELS, chain)) {
    chainSelect.value = chain;
  }
  chainHint.textContent = note || "切换会重启后端，大约 10 秒；字幕会中断一下。切不过去会自动回退。";
}

// Three states, because "no feedback" reads as a dead control: the user clicks again,
// and the second click arrives while the first restart is still in flight.
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
  status.textContent = `正在切换到 ${CHAIN_LABELS[chain] || chain}，后端会重启…`;
  try {
    const reply = await chrome.runtime.sendMessage({ type: "switch-chain", chain });
    if (reply && reply.ok) {
      chainSelect.dataset.applied = chain;
      markChainOk(reply.chain || chain);
      status.textContent = `已切到 ${CHAIN_LABELS[reply.chain] || reply.chain}，可以开始。`;
    } else {
      // A rollback leaves the popup on the chain that is actually running, not the one
      // that was asked for. Showing the requested one would have the user clicking again
      // on a change that already failed.
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
  const text = `${v} 毫秒`;
  cadenceValue.textContent = String(v);
  cadenceReadout.textContent = String(v);
  // Named bands rather than a bare number, because the effect is not proportional and
  // the middle of the range has no character of its own.
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
  // Sent as its own config frame so the switch works while running, not only at
  // start. While stopped this is a no-op -- there is no socket to carry it, and
  // the stored value rides along on the next start.
  chrome.runtime.sendMessage({ type: "cadence", partialIntervalMs: v }).catch(() => {});
}

// The slider reports every pixel of movement, so sending on each event would put a
// config frame on the wire per step. Debounced to a commit on release plus a slow
// trailing send, which keeps the live switch working without a flood.
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
  const target = next ? "已切换为只有字，字幕框控制区已隐藏。" : "已显示字幕框控制区。";
  status.textContent = target;
});

async function refresh(keepMessage) {
  const stored = await chrome.storage.session.get(["running", "language"]);
  const theme = await chrome.storage.local.get({
    captionTextOnly: false,
    audioOnly: false,
    captionFontPx: 20,
    captionColor: "#ffffff",
    apiEnabled: false,
    apiUrl: "",
    apiKey: "",
    apiModel: "",
    partialIntervalMs: DEFAULT_CADENCE_MS,
  });
  paintCadence(theme.partialIntervalMs);
  textOnly.checked = Boolean(theme.captionTextOnly);
  audioOnly.checked = Boolean(theme.audioOnly);
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
  // The host is the authority on whether a backend exists. Browser-side state --
  // storage.session and the offscreen document -- only records whether *this*
  // extension started one, and it is empty after a browser restart, after the popup
  // is reopened, and whenever the offscreen document was closed by something else.
  //
  // Reading only those is what made "终止后端不杀进程" true in the worst way: the popup
  // showed 未开始 and a 开始 button while llama-server held 3.4 of 4.0 GB, so there
  // was no stop button to press. Pressing 开始 called start_backend, which saw the
  // ports open and returned success, and nothing appeared to happen.
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
  // One probe answers both questions: which chain is live, and is anything running.
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
    // Unreachable is different from stopped, and the two were being reported the same
    // way. If the host cannot be reached we do not claim nothing is running, because
    // that is the claim that hides a live backend.
    hostReachable = false;
  }
  if (hostUp !== null) running = running || hostUp;

  toggle.textContent = running ? "终止后端" : "开始";
  toggle.classList.toggle("stop", running);
  mediaState.textContent = running ? "●" : "○";
  mediaState.style.color = running ? "#806900" : "#aaa39b";
  if (keepMessage) {
    status.textContent = keepMessage;
  } else if (!hostReachable) {
    status.textContent = "本地后端宿主连不上，无法确认是否在运行。";
  } else if (running && !Boolean(stored.running)) {
    // The host says something is up that this extension did not start, or no longer
    // remembers starting. Saying so is the difference between a stop button that works
    // and a button that appears to do nothing.
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
  status.textContent = textOnly.checked ? "已切换为只有字，字幕框控制区已隐藏。" : "已显示字幕框控制区。";
});

audioOnly.addEventListener("change", () => {
  chrome.storage.local.set({ audioOnly: audioOnly.checked });
  status.textContent = audioOnly.checked
    ? "已启用仅音频诊断，后端不会创建字幕窗。"
    : "已恢复显示原生字幕窗。";
});

fontSize.addEventListener("input", () => {
  const px = Math.max(16, Math.min(40, Number(fontSize.value) || 20));
  fontSizeValue.textContent = String(px);
  chrome.storage.local.set({ captionFontPx: px });
});

fontColor.addEventListener("input", () => {
  paintColorBar(fontColor.value);
  chrome.storage.local.set({ captionColor: fontColor.value });
  status.textContent = `字幕颜色已设为 ${fontColor.value.toUpperCase()}。`;
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
chainSelect.addEventListener("change", () => {
  // Reset the hint colour before switching: a previous failure left it red, and a red
  // hint next to a busy control reads as a second error.
  chainHint.style.color = "";
  switchChain(chainSelect.value);
});

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
        ...api,
      });
      if (!response || !response.ok) {
        throw new Error((response && response.error) || "启动失败");
      }
    }
  } catch (error) {
    // Kept, not overwritten. refresh() used to run unconditionally in the finally
    // block and reset the status line to 未开始, so every failure was erased a few
    // milliseconds after it appeared -- which is indistinguishable from the button
    // doing nothing. The step label matters for the same reason: "启动后端" and
    // "启动字幕窗" are different faults with different fixes.
    failure = `${step}失败：${String((error && error.message) || error)}`;
    console.error("[字幕]", failure, error);
  } finally {
    toggle.disabled = false;
    await refresh(failure);
  }
});

refresh();
