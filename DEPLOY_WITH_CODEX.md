# 交給 Codex 的部署任務

將整個 `weibo-to-evernote` 專案資料夾交給新 Mac 上的 Codex，並把下面這段話直接發給它：

```text
請幫我在這台 Mac 部署「微博 Evernote 本機收件匣」。

先完整閱讀 DEPLOY_WITH_CODEX.md 與 README.md，再執行下列工作：
1. 檢查 macOS、Evernote 桌面版、Google Chrome 與 Python 3 是否可用。
2. 先執行 tests/test_inbox_bridge.py、Python 與 JavaScript 語法檢查，不要跳過失敗。
3. 確認 extension/config.json 尚未存在；config.example.json 的 bridgeToken 必須為空。不要從舊電腦複製 token。
4. 執行 install.command 等價的非互動安裝，資料目錄使用 ~/Documents/Weibo Evernote Inbox。
5. 檢查 LaunchAgent、本機 127.0.0.1:38419 狀態端點和 Evernote 偵測結果。不得輸出或回傳 token。
6. 協助我在 Chrome 載入 extension 資料夾。若 chrome://extensions 受自動化安全限制，明確告訴我需要親自完成的最少步驟，並在 Finder 顯示正確資料夾。
7. 擴充功能載入後，在微博頁面驗證「存入收件匣」按鈕、1.1.3 面板狀態、新版紅橙大象圖示與「選擇暫存資料夾…」按鈕；測試時不要保存、下載影片或同步真實微博，除非我另行確認。
8. 最後只報告安裝結果、驗證結果、本機資料位置，以及仍需我親自完成的事項。

不要改用 Email to Evernote，不要硬編碼我的帳戶資料，也不要刪除任何舊收件匣資料。
```

## Codex 執行參考

部署前先在解壓後的資料夾執行：

```bash
python3 -m unittest discover -s tests -v
python3 -c 'from pathlib import Path; compile(Path("bridge/inbox_bridge.py").read_text(encoding="utf-8"), "inbox_bridge.py", "exec")'
node --check extension/background.js
node --check extension/content.js
node --check extension/popup.js
zsh -n install.command
zsh -n uninstall.command
```

如果系統沒有 Node.js，JavaScript 語法檢查可以使用 Codex 內建 Node runtime；這不影響擴充功能本身運行。

非互動安裝命令：

```bash
python3 bridge/inbox_bridge.py install \
  --extension-dir "$PWD/extension" \
  --archive-dir "$HOME/Documents/Weibo Evernote Inbox"
```

安裝後，從 `~/Library/Application Support/WeiboEvernoteInbox/config.json` 讀取 token，只用於向本機狀態端點發送驗證請求。檢查輸出不得包含 token。

Chrome 的未封裝擴充功能必須選擇本包內的 `extension` 資料夾。不要選擇 ZIP、專案根目錄或 `manifest.json` 單一檔案。

## 安全邊界

- 安裝時必須為新 Mac 生成新的隨機 token。
- 橋接器只應監聽 `127.0.0.1:38419`。
- 不得將微博 cookie、Evernote 憑證或 Email to Evernote 地址寫入套件。
- 不得用 UI 座標自動化代替 Chrome 的安全確認。
- 測試同步應使用 `dryRun` 或單元測試，不得建立真實 Evernote 筆記。
- 影片下載只允許微博與新浪媒體網域，並保留單檔與總量上限。
- 圖形化資料夾選擇必須拒絕磁碟根目錄、個人主目錄和含無關檔案的資料夾；切換目錄不得刪除或自動搬移舊資料。
