# 平台專屬程式

本目錄只存放作業系統專屬的安裝、卸載與原生整合程式。核心橋接器、Chrome 擴充功能和測試仍由兩個平台共用。

```text
platforms/
├── macos/
│   ├── install.sh
│   └── uninstall.sh
└── windows/
    ├── install.cmd
    ├── install.ps1
    ├── uninstall.cmd
    ├── uninstall.ps1
    └── windows_folder_picker.cs
```

一般使用者可直接使用專案根目錄的相容啟動器：

- macOS：`install.command`、`uninstall.command`
- Windows：`install-windows.cmd`、`uninstall-windows.cmd`

根目錄啟動器只負責轉交到本目錄，避免既有安裝方式失效。
