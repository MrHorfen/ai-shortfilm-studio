@echo off
chcp 65001 >nul
title AI Studio
cd /d "%~dp0"

rem ---- 端口与路径：环境变量优先，默认值兜底 ----
set "COMFY_DIR=%AI_COMFY_DIR%"
if not defined COMFY_DIR set "COMFY_DIR=%~dp0..\ComfyUI"
set "COMFY_PORT=%AI_COMFY_PORT%"
if not defined COMFY_PORT set "COMFY_PORT=8188"
set "STUDIO_PORT=%AI_STUDIO_PORT%"
if not defined STUDIO_PORT set "STUDIO_PORT=8899"
set "PY=%COMFY_DIR%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [错误] 找不到 Python 解释器：%PY%
  echo 请把本目录放在 ComfyUI 同级目录，或设置环境变量 AI_COMFY_DIR 指向 ComfyUI 目录。
  pause
  exit /b 2
)

rem ---- 1. ComfyUI 是否已在监听 ----
powershell -NoProfile -Command "if(Get-NetTCPConnection -LocalPort %COMFY_PORT% -State Listen -ErrorAction SilentlyContinue){exit 0}else{exit 1}"
if errorlevel 1 (
  echo 正在启动 ComfyUI，首次加载约需 1 分钟...
  set "COMFY_LAUNCHER=%COMFY_DIR%\..\start_comfyui.bat"
  if exist "%COMFY_LAUNCHER%" (
    start "ComfyUI" /min cmd /c "%COMFY_LAUNCHER%"
  ) else (
    start "ComfyUI" /min "%PY%" "%COMFY_DIR%\main.py" --reserve-vram 1 --port %COMFY_PORT%
  )
) else (
  echo ComfyUI 已在运行，直接复用。
)

echo 等待 ComfyUI 就绪...
powershell -NoProfile -Command "$d=(Get-Date).AddMinutes(8);while((Get-Date)-lt $d){try{Invoke-WebRequest http://127.0.0.1:%COMFY_PORT%/system_stats -TimeoutSec 5 -UseBasicParsing|Out-Null;Write-Host 'ComfyUI ready';exit 0}catch{Start-Sleep -Seconds 2}};Write-Host 'ComfyUI timeout';exit 1"
if errorlevel 1 (
  echo.
  echo ComfyUI 在 8 分钟内未就绪，请检查最小化的 ComfyUI 窗口日志。
  pause
  exit /b 1
)

rem ---- 2. 启动工作台 ----
powershell -NoProfile -Command "if(Get-NetTCPConnection -LocalPort %STUDIO_PORT% -State Listen -ErrorAction SilentlyContinue){exit 0}else{exit 1}"
if errorlevel 1 (
  set "AI_COMFY_DIR=%COMFY_DIR%"
  set "AI_COMFY_URL=http://127.0.0.1:%COMFY_PORT%"
  set "AI_STUDIO_PORT=%STUDIO_PORT%"
  start "AI Studio Server" /min "%PY%" "%~dp0server.py"
  timeout /t 3 >nul
) else (
  echo 工作台已在运行，直接复用。
)

rem ---- 3. 打开浏览器 ----
start "" http://127.0.0.1:%STUDIO_PORT%/preview.html
exit
