#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PYTHON_BIN="$(command -v python3 || true)"

if [[ -z "$PYTHON_BIN" ]]; then
  echo "錯誤：找不到 Python 3，無法執行卸載程式。"
  read "?按 Enter 結束。"
  exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/bridge/inbox_bridge.py" uninstall

echo ""
echo "請再到 chrome://extensions 移除「微博 Evernote 本機收件匣」。"
echo "卸載不會刪除本機微博、圖片、SQLite 或 ENEX。"
read "?按 Enter 結束。"
