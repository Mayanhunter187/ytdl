@echo off
rem Builds dist\YTDL.exe. Requires uv: https://docs.astral.sh/uv/
cd /d "%~dp0"
uv sync --upgrade-package yt-dlp || exit /b 1
uv run python build.py %* || exit /b 1
