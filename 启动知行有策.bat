@echo off
cd /d "%~dp0"

netstat -ano | findstr :8766 | findstr LISTENING >nul
if errorlevel 1 (
    where pythonw >nul 2>nul
    if %errorlevel% equ 0 (
        start "" pythonw "%~dp0server\main.py"
    ) else (
        start "" python "%~dp0server\main.py"
    )
    ping 127.0.0.1 -n 3 >nul
)

set "ELECTRON_EXE=%~dp0client\node_modules\electron\dist\electron.exe"
set "APP_DIR=%~dp0client"

start "" "%ELECTRON_EXE%" "%APP_DIR%"
exit
