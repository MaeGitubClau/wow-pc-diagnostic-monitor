@echo off
echo ============================================
echo  WoW PC Diagnostic Monitor - Setup
echo ============================================
echo.
echo Installing dependencies...
pip install psutil pynvml pywin32 wmi --quiet
echo.
echo Starting monitor...
echo.
python monitor.py
pause
