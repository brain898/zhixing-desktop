@echo off
cd /d "%~dp0"

set "ELECTRON_EXE=%~dp0client\node_modules\electron\dist\electron.exe"
set "APP_DIR=%~dp0client"

if not exist "%ELECTRON_EXE%" (
    echo Electron not found. Please run npm install in client first.
    pause
    exit /b 1
)

rem Backend lifecycle is managed by Electron main process.
start "" "%ELECTRON_EXE%" "%APP_DIR%"
exit
