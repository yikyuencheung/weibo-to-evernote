# macOS 版

macOS 使用共用的 `bridge/inbox_bridge.py` 與 `extension`，本目錄只保留平台安裝實作。

- 一般安裝：雙擊專案根目錄的 `install.command`
- 直接執行：`zsh platforms/macos/install.sh`
- 卸載：雙擊專案根目錄的 `uninstall.command`

安裝後由使用者層級 LaunchAgent 啟動本機橋接器。
