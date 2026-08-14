#!/bin/zsh
set -euo pipefail

PLATFORM_DIR="${0:A:h}"
PROJECT_DIR="${PLATFORM_DIR:h:h}"
DEFAULT_ARCHIVE="$HOME/Documents/Weibo Evernote Inbox"
PYTHON_BIN="$(command -v python3 || true)"

if [[ -z "$PYTHON_BIN" ]]; then
  echo "錯誤：找不到 Python 3。請先安裝 Python 3，再重新執行。"
  read "?按 Enter 結束。"
  exit 1
fi

echo "微博 Evernote 本機收件匣安裝程式"
echo ""
read "ARCHIVE_DIR?本機資料位置（直接按 Enter 使用 $DEFAULT_ARCHIVE）："
ARCHIVE_DIR="${ARCHIVE_DIR:-$DEFAULT_ARCHIVE}"

"$PYTHON_BIN" "$PROJECT_DIR/bridge/inbox_bridge.py" install \
  --extension-dir "$PROJECT_DIR/extension" \
  --archive-dir "$ARCHIVE_DIR"

echo ""
echo "下一步："
echo "1. 在 Chrome 打開 chrome://extensions"
echo "2. 開啟右上角「開發人員模式」"
echo "3. 點「載入未封裝項目」，選擇："
echo "   $PROJECT_DIR/extension"
echo "4. 重新載入已打開的微博頁面"
echo ""
read "?按 Enter 結束。"
