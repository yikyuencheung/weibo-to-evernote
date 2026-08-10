# 換 Mac 時遷移本機收件匣

安裝包不包含你的微博內容、圖片或歷史 ENEX。需要保留本機存檔時，另行複製舊 Mac 的整個資料夾：

```text
~/Documents/Weibo Evernote Inbox
```

其中包含：

```text
inbox.sqlite
raw/
posts/
assets/
exports/
```

建議流程：

1. 在舊 Mac 確認沒有正在執行同步。
2. 完整複製上述資料夾到外接磁碟或加密雲端儲存。
3. 在新 Mac 部署工具，但先不要在微博保存新內容。
4. 停止新 Mac 的橋接器。
5. 將備份資料夾放回同一路徑，再重新啟動橋接器。
6. 檢查面板中的總計、待同步與已匯出數量。

不要複製這個檔案：

```text
~/Library/Application Support/WeiboEvernoteInbox/config.json
```

`assets/` 也可能包含 1.1.0 之後下載的微博 MP4 影片。設定檔包含只應在單一 Mac 使用的本機 token；新 Mac 安裝程式會生成新的 token。

如果不需要歷史本機資料，只攜帶本安裝包即可；已經同步到 Evernote 的筆記會由 Evernote 帳戶自行同步。
