@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
if errorlevel 1 (
  echo.
  echo 卸載失敗。請保留上方錯誤訊息。
  pause
  exit /b 1
)
echo.
pause
