@echo off
chcp 65001 >nul
if exist "%~dp0release\CoalVolume\CoalVolume.exe" (
    start "" "%~dp0release\CoalVolume\CoalVolume.exe" %*
    exit /b 0
)
if exist "F:\Anaconda\envs\deeplearning\python.exe" (
    "F:\Anaconda\envs\deeplearning\python.exe" -X utf8 "%~dp0tools\live_coal_volume.py" %*
) else (
    python -X utf8 "%~dp0tools\live_coal_volume.py" %*
)
if errorlevel 1 pause
