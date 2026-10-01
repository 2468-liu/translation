const toggle = document.getElementById("toggle");
const status = document.getElementById("status");
const language = document.getElementById("language");
const textOnly = document.getElementById("textOnly");
const fontSize = document.getElementById("fontSize");
const fontSizeValue = document.getElementById("fontSizeValue");
const apiEnabled = document.getElementById("apiEnabled");
const apiFields = document.getElementById("apiFields");
const apiUrl = document.getElementById("apiUrl");
const apiKey = document.getElementById("apiKey");
const apiModel = document.getElementById("apiModel");

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

async function refresh() {
  const stored = await chrome.storage.session.get(["running", "language"]);
  const theme = await chrome.storage.local.get({
    captionTextOnly: false,
    captionFontPx: 20,
    apiEnabled: false,
    apiUrl: "",
    apiKey: "",
    apiModel: "",
  });
  textOnly.checked = Boolean(theme.captionTextOnly);
  const px = Math.max(16, Math.min(40, Number(theme.captionFontPx) || 20));
  fontSize.value = String(px);
  fontSizeValue.textContent = String(px);
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
  status.textContent = running ? "正在采集。关闭字幕窗不会停止后端。" : "未开始";
}

textOnly.addEventListener("change", () => {
  chrome.storage.local.set({ captionTextOnly: textOnly.checked });
  status.textContent = textOnly.checked ? "已切换为只有字，字幕框控制区已隐藏。" : "已显示字幕框控制区。";
});

fontSize.addEventListener("input", () => {
  const px = Math.max(16, Math.min(40, Number(fontSize.value) || 20));
  fontSizeValue.textContent = String(px);
  chrome.storage.local.set({ captionFontPx: px });
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

toggle.addEventListener("click", async () => {
  toggle.disabled = true;
  try {
    const running = toggle.classList.contains("stop");
    if (running) {
      status.textContent = "正在终止后端并释放模型…";
      await chrome.runtime.sendMessage({ type: "stop" });
    } else {
      status.textContent = "正在启动本地服务和模型，请稍候…";
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) throw new Error("没有可用标签页");
      const api = apiPayload();
      if (api.apiEnabled && (!api.apiUrl || !api.apiModel)) {
        throw new Error("外部接口要填地址和模型名");
      }
      chrome.storage.local.set(api);
      const response = await chrome.runtime.sendMessage({
        type: "start",
        tabId: tab.id,
        windowId: tab.windowId,
        language: language.value,
        ...api,
      });
      if (!response || !response.ok) {
        throw new Error((response && response.error) || "启动失败");
      }
    }
  } catch (error) {
    status.textContent = String(error.message || error);
  } finally {
    toggle.disabled = false;
    await refresh();
  }
});

refresh();
