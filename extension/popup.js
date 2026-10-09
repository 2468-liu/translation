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

// Must equal server/config.py PARTIAL_INTERVAL_MS. A lower default here would mean
// pressing start silently changes behaviour, and that mismatch has been the cause
// of a control that appeared to do nothing.
const DEFAULT_CADENCE_MS = 750;
const CADENCE_MIN_MS = 250;
const CADENCE_MAX_MS = 1000;
const CADENCE_STEP_MS = 50;

let cadenceCommit = null;

// A chain is the whole pipeline -- which models, which ports, and which output mode --
// not one recogniser. runtime.json holds the choice and native_host does the restart, so
// this widget only asks and reports.
//
// The output mode is named here because it belongs to the line: 只有字/流式是显示层的事，
// 而"块输出"只是 Qwen3 那条线路的属性（config.CHAINS[...]["output"]）。分开做成一个下拉
// 会给出第二个可以设置它的地方，而两个开关里总会有一个在说谎。
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
  // A line switch kills the backend, and the caption window is a child of it. So while
  // this runs there is nothing to ask and nothing to report: the hint says so, and the
  // question is repeated once the new backend is up (and again when the socket
  // reconnects by itself -- see scheduleReconnect in offscreen.js).
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
  status.textContent = captionTextOnlyNote(next);
});

// ---------------------------------------------------------------------------
// 字幕窗的三种情形。这是整套 UI 与字幕协调的核心，规则的写法只有一条：
//
//   屏幕上的东西谁说了算 —— 字幕窗进程。storage 只是"下次开始时用什么"。
//
//   ① 窗口在跑    复选框 = 问出来的真实值；下方hint说屏幕上现在是什么
//   ② 窗口没在跑  复选框 = storage（下次开始的设置）；hint 说明"开始后生效"，不是错误
//   ③ 服务没连上  hint 说"本地服务未连接，重连中"；连上后自动重问一次
//
// 所以"当前状态"永远来自**回答**或服务端明确的 caption-absent，不用定时器猜：
// 猜出来的那次误报（后端重启期间 socket 已开、窗口还没起）正是"一次有一次没有"的来源。
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

// The caption window answering about itself, or the server saying there is none.
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
    // One retry, not a warning: right after a line switch the socket can be open while
    // the window is still coming up, and the honest answer then is "ask again in a
    // moment", not an error the user cannot act on.
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

// One state, two controls: this checkbox and the caption window's own header control.
//
// 只有字 hides the caption window's header -- its own control included -- because that is
// the whole point of the mode: nothing on the video but the text. So this checkbox is
// the way back, and it has to be telling the truth or the first press is spent on a
// state the window is not in. Hence the ask in refresh() below, and the note that says
// where to press to get the frame back instead of pretending a control is still there.
function captionTextOnlyNote(on) {
  return on
    ? "字幕窗已切到「只有字」：黑框和「只有字」都隐藏了。在这个插件里再点一下即可恢复。"
    : "字幕窗已恢复黑底框和「只有字」控制。";
}

// The live caption window, told directly.
//
// Writing storage alone is not enough, and that is where the second press came from:
// storage is written by whichever side changed last, so when the caption window's own
// control has moved the state and storage has not caught up (or the value was already
// the one being asked for), storage.onChanged never fires, nothing is sent, and the
// first click does nothing visible. The style frame is what the window obeys; storage
// is only what the next start reads.
function tellCaptionWindow() {
  chrome.runtime.sendMessage({
    type: "caption-style",
    textOnly: textOnly.checked,
    fontPx: Number(fontSize.value) || 20,
    color: fontColor.value,
  }).catch(() => {});
}

// Ask the window what it is set to, and let its answer correct this checkbox.
//
// Nothing is inferred from a timeout. The server answers with the window's own state
// frame, or with caption-absent when there is no window at all -- and the second case is
// normal, so it paints the stored setting instead of an error.
function askCaptionWindowState() {
  chrome.runtime.sendMessage({ type: "caption-state" })
    .then((reply) => {
      if (!reply || !reply.sent) {
        // No socket to the local service: state ③, not a caption fault.
        captionPresent = false;
        captionLive = null;
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
    apiEnabled: false,
    apiUrl: "",
    apiKey: "",
    apiModel: "",
    partialIntervalMs: DEFAULT_CADENCE_MS,
  });
  paintCadence(theme.partialIntervalMs);
  textOnly.checked = Boolean(theme.captionTextOnly);
  audioOnly.checked = Boolean(theme.audioOnly);
  // Storage is what the next start reads; this checkbox has to show what is on screen
  // now. 只有字 hides the window's own control, so this is the only place a stale value
  // would be noticed -- and the ask is what settles which of the three states applies.
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
  // A failure is kept so it cannot be erased a few milliseconds after it appears, but
  // "kept" is not "kept forever". The backend start is the one step whose failure stops
  // being true on its own: the powershell it launched keeps running after the request
  // times out, so the backend comes up behind the popup's back and the button turns into
  // 终止后端 while the status line still reads 启动后端失败. The two together read as a
  // contradiction and send the user looking for a second fault.
  //
  // Only that step is dropped, and only while nothing is up. A caption-window failure or a
  // failed stop stays true after the backend is running, so those are left alone.
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
  tellCaptionWindow();
  // Optimistic, then corrected by the window's own report: if the window is not in the
  // state this press asked for, its next frame says so and the checkbox follows.
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
    await refresh(failure, step);
  }
});

refresh();
