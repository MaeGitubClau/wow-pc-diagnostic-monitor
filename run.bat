@echo off
echo ============================================
echo  WoW PC Diagnostic Monitor
echo ============================================
echo.
echo Installing Python dependencies...
pip install psutil pynvml pywin32 wmi --quiet
echo.

:: Check if PresentMon exists (for FPS capture)
if not exist "PresentMon64.exe" (
    echo PresentMon not found - the monitor will auto-download it on first launch.
    echo If download fails, get it from:
    echo https://github.com/GameTechDev/PresentMon/releases
    echo.
)

echo Starting monitor...
echo TIP: Right-click run.bat and "Run as Administrator" for CPU temps + FPS capture.
echo.
python monitor.py
pause
