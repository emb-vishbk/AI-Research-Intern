@echo off
setlocal
rem Use the existing Linux web environment without opening an interactive WSL shell.
rem The trailing dot prevents a quoted Windows path from ending in a backslash.
where wsl.exe >nul 2>nul
if errorlevel 1 (
    echo WSL is required to use this workspace's existing web environment.
    exit /b 1
)
echo Starting Research Intern. Keep this terminal open.
echo The server will print its localhost address after acquiring the workspace lock.
echo Press Ctrl+C in this terminal to stop the server.
wsl.exe --cd "%~dp0." --exec env PYTHONPATH=src .runtime/web-venv/bin/python -B -m research_intern.main serve %*
exit /b %errorlevel%
