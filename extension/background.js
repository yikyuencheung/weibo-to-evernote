const CONFIG_FILE = "config.json";

async function loadConfig() {
  const response = await fetch(chrome.runtime.getURL(CONFIG_FILE), { cache: "no-store" });
  if (!response.ok) throw new Error(`無法讀取擴充功能設定：HTTP ${response.status}`);
  return response.json();
}

async function callBridge(path, method = "GET", body) {
  const config = await loadConfig();
  if (!config.bridgeToken) throw new Error("尚未完成安裝，請先執行 install.command。");
  let response;
  try {
    response = await fetch(`${config.bridgeUrl}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Inbox-Token": config.bridgeToken
      },
      body: method === "POST" ? JSON.stringify(body || {}) : undefined
    });
  } catch (_error) {
    throw new Error("無法連接本機收件匣，請重新執行 install.command 或檢查橋接器是否正在運行。");
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
    CHOOSE_ARCHIVE: () => callBridge("/choose-archive", "POST")
  };
  const task = tasks[message.type];
  if (!task) return false;
  task().then(payload => sendResponse({ ok: true, payload }))
    .catch(error => sendResponse({ ok: false, error: error?.message || String(error) }));
  return true;
});
