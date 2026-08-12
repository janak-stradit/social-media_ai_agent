@echo off
setlocal

set MODE=%1
if "%MODE%"=="" set MODE=dev

if "%MODE%"=="dev" (
    .\soical-media-ai\Scripts\python.exe app.py
) else (
    echo Usage: run.bat [dev]
    exit /b 1
)

endlocal
