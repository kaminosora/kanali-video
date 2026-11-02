@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0PanoramaMapper.exe" (
    "%~dp0PanoramaMapper.exe"
    exit /b
)
python -c "import sys;sys.path.insert(0,'.deps');import numpy,PIL,av,moderngl,glcontext" >nul 2>nul
if errorlevel 1 (
    python scripts\setup_deps.py
    if errorlevel 1 goto failure
)
python entrypoint.py
if errorlevel 1 goto failure
exit /b 0
:failure
echo.
echo Launch failed. See README.md and check Python, Blender and FFmpeg paths.
pause
exit /b 1
