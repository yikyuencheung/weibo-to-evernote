@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0platforms\windows\install.ps1"
if errorlevel 1 (
  echo.
  echo 安裝失敗。請保留上方錯誤訊息。
  pause
  exit /b 1
)
echo.
pause
