# WoW PC Diagnostic Monitor

Real-time GPU/CPU/RAM monitor with automatic problem detection and report generation. Built to diagnose WoW FPS issues.

## Quick Start

1. Install [Python](https://python.org) (3.9+)
2. Double-click `run.bat`
3. Play WoW
4. Hit **Save Report** when you experience FPS drops
5. Share the `.md` file from your Desktop

## What It Does

### Live Stats Tab
- GPU: temp, usage %, VRAM, core clock, power draw, fan speed, throttle reasons
- CPU: temp, overall %, per-core %, clock speed
- RAM: used/total GB, speed (MHz), swap usage
- Disk: free space %

### Problems Tab
Automatically flags:
- 🔴 GPU thermal throttle (>83°C)
- 🔴 CPU overheating (>90°C)
- 🔴 VRAM nearly full
- 🔴 RAM running below rated speed (XMP off)
- 🟡 GPU clock drop under load
- 🟡 GPU at power limit
- 🟡 CPU bottleneck (high CPU, low GPU)
- 🟡 Windows power plan issues
- 🟡 Disk nearly full

### Log Tab
Timestamped line-per-second log of all metrics. Color coded: green = OK, yellow = warning, red = critical.

### Save Report
Generates a Markdown file on your Desktop with:
- Session statistics (min/avg/max for all metrics)
- All detected problems with occurrence counts
- Last 60 seconds of raw data

## Requirements

```
pip install psutil pynvml pywin32 wmi
```

- `psutil` — CPU, RAM, disk
- `pynvml` — NVIDIA GPU (requires NVIDIA GPU)
- `pywin32` + `wmi` — RAM speed detection (Windows only)

## Notes

- Run as Administrator for CPU temperature readings
- Always on top — sits above WoW in windowed/borderless mode
- Designed for NVIDIA GPUs (AMD GPU users: GPU section shows N/A)
