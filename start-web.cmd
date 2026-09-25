@echo off
setlocal
rem Use a project virtual environment; never fall back to system Python.
echo Starting Research Intern. Keep this terminal open.
echo The server will print its localhost address after acquiring the workspace lock.
echo Press Ctrl+C in this terminal to stop the server.
if exist "%~dp0.venv\Scripts\python.exe" (
    cd /d "%~dp0."
    set PYTHONPATH=%~dp0src
    "%~dp0.venv\Scripts\python.exe" -B tools\launch_web.py %*
) else (
    where wsl.exe >nul 2>nul
    if errorlevel 1 (
        echo Create the Windows project .venv or install WSL. See README.md.
        exit /b 1
    )
    if not exist "%~dp0.venv\bin\python" (
        echo Create the WSL project .venv first. See README.md.
        exit /b 1
    )
    wsl.exe --cd "%~dp0." --exec env PYTHONPATH=src .venv/bin/python -B tools/launch_web.py %*
)
exit /b %errorlevel%
