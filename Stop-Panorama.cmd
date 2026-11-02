@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0PanoramaMapper.exe" (
    "%~dp0PanoramaMapper.exe" --stop-server
    exit /b
)
python scripts\stop_server.py
