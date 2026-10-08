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
const cadenceFast = document.getElementById("cadenceFast");
const cadenceSlow = document.getElementById("cadenceSlow");
const cadenceHint = document.getElementById("cadenceHint");
const CADENCE_MS = { fast: 250, slow: 750 };

function paintCadence(ms) {
  const isSlow = Number(ms) >= CADENCE_MS.slow;
  cadenceFast.setAttribute("aria-pressed", String(!isSlow));
  cadenceSlow.setAttribute("aria-pressed", String(isSlow));
  cadenceHint.textContent = isSlow
    ? "正在用 750 毫秒：改口更少，但首句也更晚出现。"
    : "正在用 250 毫秒：跟得紧，改口也更频繁。";
}

async function sendCadence(ms) {
  paintCadence(ms);
  await chrome.storage.local.set({ partialIntervalMs: Number(ms) });
  // Sent as its own config frame so the switch works while running, not only at
  // start. While stopped this is a no-op -- there is no socket to carry it, and
  // the stored value rides along on the next start.
  chrome.runtime.sendMessage({ type: "cadence", partialIntervalMs: Number(ms) }).catch(() => {});
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
    partialIntervalMs: CADENCE_MS.fast,
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
  let running = Boolean(stored.running);
  try {
    const offscreen = await chrome.runtime.sendMessage({ type: "get-status" });
    if (offscreen && typeof offscreen.running === "boolean") running = offscreen.running;
  } catch (_error) {
    running = false;
  }
  if (stored.language) language.value = stored.language;
  toggle.textContent = running ? "终止后端" : "开始";
  toggle.classList.toggle("stop", running);
  mediaState.textContent = running ? "●" : "○";
  mediaState.style.color = running ? "#806900" : "#aaa39b";
  status.textContent = keepMessage || (running
    ? "正在采集。字幕由 Windows 原生置顶窗口显示，不改网页内容或全屏状态。"
    : "未开始");
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
cadenceFast.addEventListener("click", () => sendCadence(CADENCE_MS.fast));
cadenceSlow.addEventListener("click", () => sendCadence(CADENCE_MS.slow));

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
        partialIntervalMs: Number(cadenceFast.getAttribute("aria-pressed") === "true"
          ? CADENCE_MS.fast
          : CADENCE_MS.slow),
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
