# Windows 版

Windows 使用共用的 `bridge/inbox_bridge.py` 與 `extension`，本目錄另外提供 PowerShell 安裝程式與原生資料夾選擇器。

- 一般安裝：雙擊專案根目錄的 `install-windows.cmd`
- 直接安裝：雙擊本目錄的 `install.cmd`
- 卸載：雙擊專案根目錄的 `uninstall-windows.cmd`

安裝時會以系統內建的 .NET Framework C# 編譯器建立 `IFileOpenDialog` 輔助程式；編譯結果只安裝到使用者的 LocalAppData，不提交到原始碼庫。
