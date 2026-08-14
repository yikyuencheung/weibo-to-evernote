const CONFIG_FILE = "config.json";

async function loadConfig() {
  const response = await fetch(chrome.runtime.getURL(CONFIG_FILE), { cache: "no-store" });
  if (!response.ok) throw new Error(`無法讀取擴充功能設定：HTTP ${response.status}`);
  return response.json();
}

async function callBridge(path, method = "GET", body, timeoutMs = 0) {
  const config = await loadConfig();
  if (!config.bridgeToken) throw new Error("尚未完成安裝，請先執行對應平台的安裝程式。");
  const controller = timeoutMs ? new AbortController() : null;
  let timedOut = false;
  const timer = timeoutMs ? setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs) : null;
  let response;
  try {
    response = await fetch(`${config.bridgeUrl}${path}`, {
      method,
      signal: controller?.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Inbox-Token": config.bridgeToken
      },
      body: method === "POST" ? JSON.stringify(body || {}) : undefined
    });
  } catch (_error) {
    if (timedOut) throw new Error("資料夾選擇逾時；請重新打開面板後再試一次。");
    throw new Error("無法連接本機收件匣，請重新執行對應平台的安裝程式或檢查橋接器是否正在運行。");
  } finally {
    if (timer) clearTimeout(timer);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || `本機橋接器回傳 HTTP ${response.status}`);
  return payload;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message.type !== "string") return false;
  const tasks = {
    CAPTURE_WEIBO: () => callBridge("/capture", "POST", { clip: message.clip }),
    GET_STATUS: () => callBridge("/status"),
    SYNC_EVERNOTE: () => callBridge("/sync", "POST"),
    REOPEN_LATEST: () => callBridge("/reopen-latest", "POST"),
    OPEN_INBOX: () => callBridge("/open-folder", "POST"),
    CHOOSE_ARCHIVE: () => callBridge("/choose-archive", "POST", {}, 65000)
  };
  const task = tasks[message.type];
  if (!task) return false;
  task().then(payload => sendResponse({ ok: true, payload }))
    .catch(error => sendResponse({ ok: false, error: error?.message || String(error) }));
  return true;
});
