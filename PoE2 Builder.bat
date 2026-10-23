@echo off
rem Double-click to open PoE2 Builder in your browser. Keep the window open while you use it.
title PoE2 Builder
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m builder.web
) else (
    uv run python -m builder.web
)
if errorlevel 1 (
    echo.
    echo PoE2 Builder stopped with an error. See the message above.
    pause
)
