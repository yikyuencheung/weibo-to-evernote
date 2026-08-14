const elements = {
  queued: document.querySelector("#queued"),
  exported: document.querySelector("#exported"),
  total: document.querySelector("#total"),
  sync: document.querySelector("#sync"),
  reopen: document.querySelector("#reopen"),
  folder: document.querySelector("#folder"),
  choose: document.querySelector("#choose"),
  message: document.querySelector("#message"),
  path: document.querySelector("#path")
};

function send(type) {
  return new Promise(resolve => chrome.runtime.sendMessage({ type }, resolve));
}

function show(message, error = false) {
  elements.message.textContent = message;
  elements.message.classList.toggle("error", error);
}

function busy(value) {
  [elements.sync, elements.reopen, elements.folder, elements.choose].forEach(button => { button.disabled = value; });
}

async function refresh(message) {
  const response = await send("GET_STATUS");
  if (!response?.ok) {
    show(response?.error || "無法取得狀態", true);
    elements.path.textContent = "";
    return;
  }
  const data = response.payload;
  elements.queued.textContent = data.queued;
  elements.exported.textContent = data.exported;
  elements.total.textContent = data.total;
  elements.path.textContent = data.archiveDir;
  elements.sync.disabled = data.queued === 0;
  elements.choose.disabled = Boolean(data.pickerBusy);
  show(message || (data.pickerBusy ? "資料夾選擇視窗已開啟。" : (data.queued ? `有 ${data.queued} 條微博待同步。` : "目前沒有待同步微博。")));
}

async function perform(type, working) {
  busy(true);
  show(working);
  const response = await send(type);
  if (!response?.ok) {
    show(response?.error || "操作失敗", true);
    busy(false);
    return;
  }
  busy(false);
  await refresh(response.payload.message);
}

elements.sync.addEventListener("click", () => perform("SYNC_EVERNOTE", "正在生成增量 ENEX 並交給 Evernote…"));
elements.reopen.addEventListener("click", () => perform("REOPEN_LATEST", "正在打開最近一批 ENEX…"));
elements.folder.addEventListener("click", () => perform("OPEN_INBOX", "正在打開本機收件匣…"));
elements.choose.addEventListener("click", () => perform("CHOOSE_ARCHIVE", "請在系統視窗中選擇空資料夾或既有收件匣…"));
refresh();
