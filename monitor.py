"""
WoW PC Diagnostic Monitor
Monitors GPU/CPU/RAM + real WoW FPS via PresentMon in real-time.
Detects problems and saves a full diagnostic report.

Install:  pip install psutil pynvml wmi pywin32
Run:      python monitor.py   (as Administrator for FPS capture)
"""

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox
import threading
import time
import datetime
import os
import sys
import platform
import subprocess
import collections
import statistics
import urllib.request
import zipfile
import shutil

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    HAS_NVML = True
except Exception:
    HAS_NVML = False

try:
    import wmi
    WMI = wmi.WMI()
    HAS_WMI = True
except Exception:
    HAS_WMI = False

# ── Config ─────────────────────────────────────────────────────────────────────
WOW_PROCESS_NAMES = ["Wow.exe", "WowT.exe", "WowB.exe", "WowClassic.exe"]
PRESENTMON_EXE    = os.path.join(os.path.dirname(__file__), "PresentMon64.exe")
PRESENTMON_URL    = "https://github.com/GameTechDev/PresentMon/releases/download/v1.10.0/PresentMon-1.10.0-x64.exe"
FPS_HISTORY_LEN   = 300   # keep last 5 min of FPS samples
FPS_DIP_THRESHOLD = 0.75  # dip = below 75% of avg FPS
LOG_INTERVAL      = 1.0
MAX_HISTORY       = 3600

# ── Palette ────────────────────────────────────────────────────────────────────
BG     = "#0f0f0f"
PANEL  = "#161616"
BORDER = "#2a2a2a"
FG     = "#e0e0e0"
DIM    = "#555555"
GOOD   = "#00e676"
WARN   = "#ffeb3b"
CRIT   = "#ff1744"
HEADER = "#00bcd4"
ACCENT = "#7c4dff"
WHITE  = "#ffffff"
BLUE   = "#2979ff"

T = {
    "gpu_temp":  (75, 83),
    "cpu_temp":  (80, 90),
    "gpu_usage": (90, 99),
    "cpu_usage": (75, 95),
    "ram_pct":   (75, 90),
    "vram_pct":  (80, 95),
}


def color_val(v, lo, hi):
    if v is None:
        return DIM
    return CRIT if v >= hi else WARN if v >= lo else GOOD


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── FPS capture via PresentMon ─────────────────────────────────────────────────

class FPSCapture:
    """
    Launches PresentMon targeting WoW and reads frame times from stdout.
    Falls back to None if PresentMon isn't available or WoW isn't running.
    """

    def __init__(self):
        self._proc        = None
        self._wow_pid     = None
        self._fps_history = collections.deque(maxlen=FPS_HISTORY_LEN)
        self._dips        = []          # list of (timestamp, fps) for dips
        self._current_fps = None
        self._avg_fps     = None
        self._min_fps     = None
        self._max_fps     = None
        self._dip_count   = 0
        self._running     = True
        self._status      = "Searching for WoW…"
        self._thread      = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _find_wow(self):
        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info["name"] in WOW_PROCESS_NAMES:
                return proc.info["pid"], proc.info["name"]
        return None, None

    def _ensure_presentmon(self):
        if os.path.exists(PRESENTMON_EXE):
            return True
        # Try to download it
        try:
            self._status = "Downloading PresentMon…"
            tmp = PRESENTMON_EXE + ".tmp"
            urllib.request.urlretrieve(PRESENTMON_URL, tmp)
            shutil.move(tmp, PRESENTMON_EXE)
            return True
        except Exception as e:
            self._status = f"PresentMon download failed: {e}"
            return False

    def _run(self):
        while self._running:
            pid, name = self._find_wow()
            if not pid:
                self._status = "WoW not running — launch WoW to enable FPS tracking"
                self._current_fps = None
                time.sleep(3)
                continue

            if not self._ensure_presentmon():
                time.sleep(10)
                continue

            self._wow_pid = pid
            self._status  = f"Capturing FPS from {name} (PID {pid})"
            try:
                self._capture(pid)
            except Exception as e:
                self._status = f"PresentMon error: {e}"
            finally:
                if self._proc:
                    try:
                        self._proc.terminate()
                    except Exception:
                        pass
                    self._proc = None
                self._current_fps = None
            time.sleep(2)

    def _capture(self, pid):
        cmd = [
            PRESENTMON_EXE,
            "-process_id", str(pid),
            "-output_stdout",
            "-no_top",
            "-stop_existing_session",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        header = None
        col_ms = None

        for raw_line in self._proc.stdout:
            if not self._running:
                break
            line = raw_line.strip()
            if not line:
                continue

            if header is None:
                header = line.split(",")
                # PresentMon uses msBetweenPresents for frame time
                for i, col in enumerate(header):
                    if "msBetweenPresents" in col:
                        col_ms = i
                        break
                continue

            if col_ms is None:
                continue

            try:
                parts = line.split(",")
                ms = float(parts[col_ms])
                if ms <= 0:
                    continue
                fps = 1000.0 / ms
                if fps < 1 or fps > 1000:
                    continue

                self._fps_history.append((time.time(), fps))
                self._current_fps = fps

                vals = [f for _, f in self._fps_history]
                if len(vals) >= 2:
                    self._avg_fps = statistics.mean(vals)
                    self._min_fps = min(vals)
                    self._max_fps = max(vals)

                    # Detect dip: FPS below 75% of rolling avg
                    if fps < self._avg_fps * FPS_DIP_THRESHOLD:
                        self._dip_count += 1
                        self._dips.append((ts(), round(fps, 1)))
                        if len(self._dips) > 200:
                            self._dips.pop(0)
            except (ValueError, IndexError):
                continue

    def snapshot(self):
        return {
            "status":  self._status,
            "fps":     self._current_fps,
            "avg":     self._avg_fps,
            "min":     self._min_fps,
            "max":     self._max_fps,
            "dips":    list(self._dips[-10:]),
            "dip_count": self._dip_count,
            "history": list(self._fps_history),
        }

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


# ── Hardware collection ────────────────────────────────────────────────────────

def collect_gpu():
    if not HAS_NVML:
        return None
    try:
        name    = pynvml.nvmlDeviceGetName(GPU_HANDLE)
        if isinstance(name, bytes):
            name = name.decode()
        temp    = pynvml.nvmlDeviceGetTemperature(GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        util    = pynvml.nvmlDeviceGetUtilizationRates(GPU_HANDLE)
        mem     = pynvml.nvmlDeviceGetMemoryInfo(GPU_HANDLE)
        clock   = pynvml.nvmlDeviceGetClockInfo(GPU_HANDLE, pynvml.NVML_CLOCK_GRAPHICS)
        max_clk = pynvml.nvmlDeviceGetMaxClockInfo(GPU_HANDLE, pynvml.NVML_CLOCK_GRAPHICS)
        power   = pynvml.nvmlDeviceGetPowerUsage(GPU_HANDLE) / 1000.0
        try:
            power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(GPU_HANDLE) / 1000.0
        except Exception:
            power_limit = None
        fan_speed = None
        try:
            fan_speed = pynvml.nvmlDeviceGetFanSpeed(GPU_HANDLE)
        except Exception:
            pass
        throttle = 0
        try:
            throttle = pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(GPU_HANDLE)
        except Exception:
            pass
        vram_used  = mem.used  / 1024**3
        vram_total = mem.total / 1024**3
        vram_pct   = (mem.used / mem.total) * 100
        return {
            "name": name, "temp": temp,
            "usage": util.gpu, "mem_usage": util.memory,
            "vram_used": vram_used, "vram_total": vram_total, "vram_pct": vram_pct,
            "clock": clock, "max_clock": max_clk,
            "power": power, "power_limit": power_limit,
            "fan": fan_speed, "throttle": throttle,
        }
    except Exception as e:
        return {"error": str(e)}


def collect_cpu():
    usage    = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    freq     = psutil.cpu_freq()
    ram      = psutil.virtual_memory()
    swap     = psutil.swap_memory()
    temp     = None
    try:
        all_t = psutil.sensors_temperatures()
        if all_t:
            for key in ("coretemp", "k10temp", "cpu-thermal", "acpitz"):
                if key in all_t:
                    vals = [t.current for t in all_t[key] if t.current > 0]
                    if vals:
                        temp = max(vals)
                        break
    except Exception:
        pass
    ram_speed = None
    if HAS_WMI:
        try:
            for stick in WMI.Win32_PhysicalMemory():
                if stick.Speed:
                    ram_speed = int(stick.Speed)
                    break
        except Exception:
            pass
    disk = psutil.disk_usage(os.path.splitdrive(sys.executable)[0] + "\\")
    return {
        "usage": usage, "per_core": per_core,
        "freq_mhz": freq.current if freq else 0,
        "freq_max":  freq.max     if freq else 0,
        "temp": temp,
        "ram_used": ram.used / 1024**3, "ram_total": ram.total / 1024**3,
        "ram_pct": ram.percent, "ram_speed": ram_speed,
        "swap_pct": swap.percent,
        "disk_pct": disk.percent, "disk_free_gb": disk.free / 1024**3,
    }


def detect_problems(cpu, gpu, fps_snap):
    issues = []

    # ── FPS dips ──
    if fps_snap and fps_snap["fps"] is not None:
        if fps_snap["dip_count"] > 0 and fps_snap["avg"]:
            pct = fps_snap["dip_count"] / max(len(fps_snap["history"]), 1) * 100
            if pct > 5:
                issues.append(("WARNING", "Frequent FPS dips detected",
                               f"FPS dropped below 75% of average {fps_snap['dip_count']} times "
                               f"({pct:.0f}% of frames). "
                               f"Avg: {fps_snap['avg']:.0f}  Min: {fps_snap['min']:.0f}  "
                               f"Max: {fps_snap['max']:.0f} fps. "
                               "Check GPU throttle, CPU single-thread load, or addon overhead."))
        if fps_snap["fps"] < 60 and fps_snap["avg"] and fps_snap["avg"] > 80:
            issues.append(("CRITICAL", "Severe FPS drop right now",
                           f"Current FPS: {fps_snap['fps']:.0f} (avg {fps_snap['avg']:.0f}). "
                           "Watch GPU clock and temp — likely throttle event in progress."))

    # ── GPU ──
    if gpu and "temp" in gpu:
        if gpu["temp"] >= T["gpu_temp"][1]:
            issues.append(("CRITICAL", "GPU THERMAL THROTTLE",
                           f"GPU at {gpu['temp']}°C. Card is throttling clocks to protect itself. "
                           "Check GPU fan curve, case airflow, and thermal paste."))
        elif gpu["temp"] >= T["gpu_temp"][0]:
            issues.append(("WARNING", "GPU running hot",
                           f"GPU at {gpu['temp']}°C — approaching throttle territory (83°C)."))

        if gpu.get("max_clock") and gpu["max_clock"] > 0:
            drop = (1 - gpu["clock"] / gpu["max_clock"]) * 100
            if drop >= 10 and gpu["usage"] > 60:
                issues.append(("WARNING", "GPU clock below max under load",
                               f"Clock: {gpu['clock']} / {gpu['max_clock']} MHz "
                               f"({drop:.0f}% below max) with {gpu['usage']}% load. "
                               "Possible power or thermal throttle."))

        if gpu.get("power_limit") and gpu.get("power"):
            if gpu["power"] >= gpu["power_limit"] * 0.97:
                issues.append(("WARNING", "GPU at power limit",
                               f"Drawing {gpu['power']:.0f}W / {gpu['power_limit']:.0f}W. "
                               "GPU is power-limited. Check PSU and Afterburner power limit setting."))

        if gpu["vram_pct"] >= T["vram_pct"][1]:
            issues.append(("CRITICAL", "VRAM nearly full",
                           f"VRAM {gpu['vram_used']:.1f}/{gpu['vram_total']:.0f}GB "
                           f"({gpu['vram_pct']:.0f}%). WoW will stutter loading textures. "
                           "Lower Texture Quality to 7."))
        elif gpu["vram_pct"] >= T["vram_pct"][0]:
            issues.append(("WARNING", "VRAM usage high",
                           f"VRAM at {gpu['vram_pct']:.0f}%."))

    # ── CPU ──
    if cpu["temp"] is not None:
        if cpu["temp"] >= T["cpu_temp"][1]:
            issues.append(("CRITICAL", "CPU OVERHEATING",
                           f"CPU at {cpu['temp']:.0f}°C. Throttling active. "
                           "Check CPU cooler, thermal paste, and airflow."))
        elif cpu["temp"] >= T["cpu_temp"][0]:
            issues.append(("WARNING", "CPU running hot", f"CPU at {cpu['temp']:.0f}°C."))

    if cpu["freq_max"] > 0:
        ratio = cpu["freq_mhz"] / cpu["freq_max"]
        if ratio < 0.6 and cpu["usage"] > 50:
            issues.append(("WARNING", "CPU clock low under load",
                           f"Running at {cpu['freq_mhz']:.0f} / {cpu['freq_max']:.0f} MHz "
                           f"({ratio*100:.0f}%) with {cpu['usage']:.0f}% load. "
                           "Check Windows power plan — set to High Performance or AMD Balanced."))

    if cpu["ram_speed"] is not None and cpu["ram_speed"] < 2800:
        issues.append(("CRITICAL", "RAM running below rated speed",
                       f"RAM at {cpu['ram_speed']}MHz (rated 3200MHz). "
                       "XMP is OFF in BIOS. Enable XMP/DOCP for a significant FPS boost in WoW."))
    elif cpu["ram_speed"] is not None and cpu["ram_speed"] < 3100:
        issues.append(("WARNING", "RAM speed slightly below rated",
                       f"RAM at {cpu['ram_speed']}MHz. Check XMP setting in BIOS."))

    if cpu["ram_pct"] >= T["ram_pct"][1]:
        issues.append(("WARNING", "High RAM usage",
                       f"RAM {cpu['ram_used']:.1f}/{cpu['ram_total']:.0f}GB. Close background apps."))

    if cpu["disk_pct"] >= 90:
        issues.append(("WARNING", "Disk nearly full",
                       f"Drive is {cpu['disk_pct']:.0f}% full ({cpu['disk_free_gb']:.1f}GB free). "
                       "WoW needs headroom for shader cache."))

    if cpu["usage"] >= 85 and (not gpu or gpu.get("usage", 0) < 70):
        issues.append(("WARNING", "CPU bottleneck detected",
                       f"CPU at {cpu['usage']:.0f}% while GPU is low. "
                       "WoW is CPU-bound on its main thread — common cause of arena FPS dips."))

    return issues


# ── Main App ───────────────────────────────────────────────────────────────────

class Monitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WoW PC Diagnostic Monitor")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.geometry("720x860")

        self._history   = []
        self._running   = True
        self._start_ts  = now_iso()
        self._start_t   = time.time()

        self._mono   = tkfont.Font(family="Consolas", size=10)
        self._mono_b = tkfont.Font(family="Consolas", size=10, weight="bold")
        self._big    = tkfont.Font(family="Consolas", size=12, weight="bold")
        self._h1     = tkfont.Font(family="Consolas", size=9)

        self._fps = FPSCapture()

        self._build_ui()

        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")

        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(hdr, text="WoW PC Diagnostic Monitor",
                 fg=WHITE, bg=BG, font=self._big).pack(side="left")
        self._dot = tk.Label(hdr, text="●", fg=GOOD, bg=BG, font=self._big)
        self._dot.pack(side="right")
        self._status_lbl = tk.Label(hdr, text="Starting…", fg=DIM, bg=BG, font=self._h1)
        self._status_lbl.pack(side="right", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._style_nb()

        t_live = tk.Frame(nb, bg=BG)
        t_fps  = tk.Frame(nb, bg=BG)
        t_prob = tk.Frame(nb, bg=BG)
        t_log  = tk.Frame(nb, bg=BG)

        nb.add(t_live, text="  Live Stats  ")
        nb.add(t_fps,  text="  FPS Monitor  ")
        nb.add(t_prob, text="  Problems  ")
        nb.add(t_log,  text="  Log  ")

        self._build_live(t_live)
        self._build_fps(t_fps)
        self._build_problems(t_prob)
        self._build_log(t_log)

        bot = tk.Frame(self, bg=PANEL, pady=6)
        bot.pack(fill="x", side="bottom")
        tk.Button(bot, text="💾  Save Report", command=self._save_report,
                  bg=ACCENT, fg=WHITE, font=self._mono_b,
                  relief="flat", padx=14, pady=4, cursor="hand2").pack(side="left", padx=12)
        tk.Button(bot, text="🗑  Clear Log", command=self._clear_log,
                  bg=BORDER, fg=FG, font=self._mono,
                  relief="flat", padx=12, pady=4, cursor="hand2").pack(side="left", padx=4)
        self._uptime_lbl = tk.Label(bot, text="Uptime: 0s", fg=DIM, bg=PANEL, font=self._h1)
        self._uptime_lbl.pack(side="right", padx=12)

    def _style_nb(self):
        s = ttk.Style()
        s.theme_use("default")
        s.configure("TNotebook",     background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                    padding=[12, 5], font=("Consolas", 10))
        s.map("TNotebook.Tab",
              background=[("selected", BORDER)],
              foreground=[("selected", WHITE)])

    def _scrollable(self, parent):
        c  = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=c.yview)
        f  = tk.Frame(c, bg=BG)
        f.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0, 0), window=f, anchor="nw")
        c.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        c.pack(side="left", fill="both", expand=True)
        return f

    def _section(self, parent, title):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=8, pady=(10, 0))
        tk.Label(outer, text=f"  {title}  ",
                 fg=HEADER, bg=BG, font=self._big).pack(anchor="w", pady=(0, 4))
        inner = tk.Frame(outer, bg=PANEL, pady=6, padx=10,
                         highlightbackground=BORDER, highlightthickness=1)
        inner.pack(fill="x")
        return inner

    def _row(self, parent, label):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=f"{label:<20}", fg=DIM, bg=PANEL, font=self._mono).pack(side="left")
        v = tk.Label(row, text="—", fg=FG, bg=PANEL, font=self._mono_b)
        v.pack(side="left")
        return v

    # ── Live Stats tab ─────────────────────────────────────────────────────────

    def _build_live(self, p):
        s = self._scrollable(p)
        g = self._section(s, "GPU")
        self.r_gpu_name  = self._row(g, "Model")
        self.r_gpu_temp  = self._row(g, "Temperature")
        self.r_gpu_usage = self._row(g, "Core Usage")
        self.r_gpu_vram  = self._row(g, "VRAM")
        self.r_gpu_clock = self._row(g, "Core Clock")
        self.r_gpu_power = self._row(g, "Power Draw")
        self.r_gpu_fan   = self._row(g, "Fan Speed")
        self.r_gpu_throt = self._row(g, "Throttle")

        c = self._section(s, "CPU")
        self.r_cpu_temp  = self._row(c, "Temperature")
        self.r_cpu_usage = self._row(c, "Overall Usage")
        self.r_cpu_freq  = self._row(c, "Clock Speed")
        self.r_cpu_cores = self._row(c, "Per-Core %")

        r = self._section(s, "RAM & STORAGE")
        self.r_ram       = self._row(r, "System RAM")
        self.r_ram_speed = self._row(r, "RAM Speed")
        self.r_swap      = self._row(r, "Swap")
        self.r_disk      = self._row(r, "Disk")

    # ── FPS Monitor tab ────────────────────────────────────────────────────────

    def _build_fps(self, p):
        top = self._section(p, "FPS — World of Warcraft")
        self.r_fps_status = self._row(top, "Capture Status")
        self.r_fps_now    = self._row(top, "Current FPS")
        self.r_fps_avg    = self._row(top, "Average FPS")
        self.r_fps_min    = self._row(top, "Min FPS (session)")
        self.r_fps_max    = self._row(top, "Max FPS (session)")
        self.r_fps_dips   = self._row(top, "Dip Events")

        dip_sec = self._section(p, "Recent FPS Dips")
        self._dip_list = tk.Text(dip_sec, bg=PANEL, fg=WARN,
                                  font=("Consolas", 9), relief="flat",
                                  height=8, state="disabled")
        self._dip_list.pack(fill="x", padx=4, pady=4)

        tk.Label(p,
                 text=(
                     "ℹ  PresentMon must be in the same folder as monitor.py.\n"
                     "   It will be downloaded automatically on first run (needs internet).\n"
                     "   Run monitor.py as Administrator for best results."
                 ),
                 fg=DIM, bg=BG, font=self._h1,
                 justify="left", anchor="w").pack(anchor="w", padx=14, pady=(8, 0))

    # ── Problems tab ───────────────────────────────────────────────────────────

    def _build_problems(self, p):
        tk.Label(p, text="Auto-detected issues — refreshed every second.",
                 fg=DIM, bg=BG, font=self._h1).pack(anchor="w", padx=14, pady=(10, 4))
        self._prob_frame = tk.Frame(p, bg=BG)
        self._prob_frame.pack(fill="both", expand=True, padx=8)
        self._no_prob = tk.Label(self._prob_frame, text="✅  No problems detected.",
                                  fg=GOOD, bg=BG, font=self._mono_b)
        self._no_prob.pack(pady=30)

    # ── Log tab ────────────────────────────────────────────────────────────────

    def _build_log(self, p):
        tk.Label(p, text="1 entry/sec — FPS | GPU | CPU | RAM",
                 fg=DIM, bg=BG, font=self._h1).pack(anchor="w", padx=8, pady=(8, 0))
        self._log = tk.Text(p, bg=PANEL, fg=FG, font=("Consolas", 9),
                             relief="flat", bd=0, state="disabled", wrap="none")
        sb = ttk.Scrollbar(p, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True, padx=8, pady=6)
        self._log.tag_configure("crit", foreground=CRIT)
        self._log.tag_configure("warn", foreground=WARN)
        self._log.tag_configure("ok",   foreground=GOOD)
        self._log.tag_configure("fps",  foreground=BLUE)

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            t0 = time.time()
            try:
                gpu      = collect_gpu()
                cpu      = collect_cpu()
                fps_snap = self._fps.snapshot()
                problems = detect_problems(cpu, gpu, fps_snap)
                self._history.append({
                    "ts": now_iso(), "cpu": cpu, "gpu": gpu,
                    "fps": fps_snap, "problems": problems,
                })
                if len(self._history) > MAX_HISTORY:
                    self._history.pop(0)
                uptime = int(time.time() - self._start_t)
                self.after(0, self._refresh, cpu, gpu, fps_snap, problems, uptime)
            except Exception as e:
                self.after(0, self._status_lbl.config, {"text": f"Error: {e}"})
            time.sleep(max(0, LOG_INTERVAL - (time.time() - t0)))

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh(self, cpu, gpu, fps, problems, uptime):
        self._refresh_live(cpu, gpu)
        self._refresh_fps(fps)
        self._refresh_problems(problems)
        self._append_log(cpu, gpu, fps, problems)

        any_crit = any(p[0] == "CRITICAL" for p in problems)
        any_warn = any(p[0] == "WARNING"  for p in problems)
        if any_crit:
            self._dot.config(fg=CRIT)
            self._status_lbl.config(text="CRITICAL ISSUE DETECTED", fg=CRIT)
        elif any_warn:
            self._dot.config(fg=WARN)
            self._status_lbl.config(text=f"{len(problems)} warning(s)", fg=WARN)
        else:
            self._dot.config(fg=GOOD)
            self._status_lbl.config(text="All systems OK", fg=GOOD)

        self._uptime_lbl.config(
            text=f"Uptime: {uptime}s  |  Samples: {len(self._history)}")

    def _refresh_live(self, cpu, gpu):
        if gpu and "temp" in gpu:
            self.r_gpu_name.config(text=gpu["name"], fg=FG)
            self.r_gpu_temp.config(text=f"{gpu['temp']}°C",
                                    fg=color_val(gpu["temp"], *T["gpu_temp"]))
            self.r_gpu_usage.config(text=f"{gpu['usage']}%",
                                     fg=color_val(gpu["usage"], *T["gpu_usage"]))
            self.r_gpu_vram.config(
                text=f"{gpu['vram_used']:.1f}/{gpu['vram_total']:.0f}GB ({gpu['vram_pct']:.0f}%)",
                fg=color_val(gpu["vram_pct"], *T["vram_pct"]))
            mc  = gpu.get("max_clock") or 0
            clk = f"{gpu['clock']} MHz" + (f" / {mc} max" if mc else "")
            self.r_gpu_clock.config(text=clk, fg=FG)
            pl  = gpu.get("power_limit")
            pw  = f"{gpu['power']:.0f}W" + (f" / {pl:.0f}W" if pl else "")
            self.r_gpu_power.config(
                text=pw, fg=WARN if pl and gpu["power"] >= pl * 0.97 else FG)
            fan = gpu.get("fan")
            self.r_gpu_fan.config(text=f"{fan}%" if fan else "N/A",
                                   fg=WARN if fan and fan > 85 else FG)
            tr = gpu.get("throttle", 0)
            reasons, color = [], GOOD
            if tr and tr not in (0, 1):
                if tr & 0x08: reasons.append("HW Slowdown");  color = CRIT
                if tr & 0x20: reasons.append("SW Thermal");   color = CRIT
                if tr & 0x40: reasons.append("HW Thermal");   color = CRIT
                if tr & 0x80: reasons.append("Power Brake");  color = CRIT
                if tr & 0x04: reasons.append("App Clocks");   color = max(color, WARN)
                if tr & 0x08: reasons.append("SW Power Cap");
            self.r_gpu_throt.config(
                text=", ".join(reasons) if reasons else "None", fg=color)
        else:
            msg = "pynvml not installed — pip install pynvml" if not HAS_NVML else "GPU read error"
            for w in (self.r_gpu_name, self.r_gpu_temp, self.r_gpu_usage,
                      self.r_gpu_vram, self.r_gpu_clock, self.r_gpu_power,
                      self.r_gpu_fan, self.r_gpu_throt):
                w.config(text=msg, fg=DIM)

        t = cpu["temp"]
        self.r_cpu_temp.config(
            text=f"{t:.0f}°C" if t else "N/A (run as admin)",
            fg=color_val(t, *T["cpu_temp"]) if t else DIM)
        self.r_cpu_usage.config(
            text=f"{cpu['usage']:.1f}%",
            fg=color_val(cpu["usage"], *T["cpu_usage"]))
        fm = cpu["freq_max"]
        self.r_cpu_freq.config(
            text=f"{cpu['freq_mhz']:.0f} MHz" + (f" / {fm:.0f} max" if fm else ""), fg=FG)
        cores = "  ".join(f"C{i}:{v:.0f}%" for i, v in enumerate(cpu["per_core"]))
        self.r_cpu_cores.config(text=cores, fg=FG)

        self.r_ram.config(
            text=f"{cpu['ram_used']:.1f}/{cpu['ram_total']:.0f}GB ({cpu['ram_pct']:.0f}%)",
            fg=color_val(cpu["ram_pct"], *T["ram_pct"]))
        if cpu["ram_speed"]:
            c = CRIT if cpu["ram_speed"] < 2800 else WARN if cpu["ram_speed"] < 3100 else GOOD
            self.r_ram_speed.config(
                text=f"{cpu['ram_speed']}MHz  {'⚠ XMP OFF?' if cpu['ram_speed'] < 3000 else '✓ XMP OK'}",
                fg=c)
        else:
            self.r_ram_speed.config(text="N/A (install pywin32)", fg=DIM)
        self.r_swap.config(
            text=f"{cpu['swap_pct']:.0f}%",
            fg=WARN if cpu["swap_pct"] > 20 else GOOD)
        self.r_disk.config(
            text=f"{cpu['disk_pct']:.0f}% full ({cpu['disk_free_gb']:.1f}GB free)",
            fg=color_val(cpu["disk_pct"], 80, 90))

    def _refresh_fps(self, fps):
        self.r_fps_status.config(text=fps["status"], fg=DIM)
        if fps["fps"] is not None:
            f = fps["fps"]
            avg = fps["avg"] or f
            c = CRIT if f < avg * 0.6 else WARN if f < avg * 0.75 else GOOD
            self.r_fps_now.config(text=f"{f:.0f} fps", fg=c)
            if fps["avg"]:
                self.r_fps_avg.config(text=f"{fps['avg']:.0f} fps", fg=FG)
            if fps["min"]:
                self.r_fps_min.config(
                    text=f"{fps['min']:.0f} fps",
                    fg=CRIT if fps["min"] < 60 else WARN if fps["min"] < 100 else GOOD)
            if fps["max"]:
                self.r_fps_max.config(text=f"{fps['max']:.0f} fps", fg=GOOD)
            self.r_fps_dips.config(
                text=f"{fps['dip_count']} dip(s) detected",
                fg=CRIT if fps["dip_count"] > 20 else WARN if fps["dip_count"] > 5 else GOOD)
        else:
            for w in (self.r_fps_now, self.r_fps_avg, self.r_fps_min,
                      self.r_fps_max, self.r_fps_dips):
                w.config(text="—", fg=DIM)

        self._dip_list.configure(state="normal")
        self._dip_list.delete("1.0", "end")
        if fps["dips"]:
            for (t_dip, f_dip) in reversed(fps["dips"]):
                self._dip_list.insert("end", f"[{t_dip}]  {f_dip} fps\n")
        else:
            self._dip_list.insert("end", "No dips recorded yet.")
        self._dip_list.configure(state="disabled")

    def _refresh_problems(self, problems):
        for w in self._prob_frame.winfo_children():
            w.destroy()
        if not problems:
            tk.Label(self._prob_frame, text="✅  No problems detected.",
                     fg=GOOD, bg=BG, font=self._mono_b).pack(pady=30)
            return
        for sev, title, detail in problems:
            color = CRIT if sev == "CRITICAL" else WARN
            card  = tk.Frame(self._prob_frame, bg=PANEL,
                             highlightbackground=color, highlightthickness=1)
            card.pack(fill="x", pady=4, padx=4)
            tk.Frame(card, bg=color, height=1).pack(fill="x")
            tk.Label(card,
                     text=f"  {'🔴' if sev=='CRITICAL' else '🟡'} {sev}: {title}  ",
                     fg=WHITE, bg=color, font=self._mono_b, anchor="w").pack(
                         fill="x", pady=3)
            tk.Label(card, text=detail, fg=FG, bg=PANEL,
                     font=self._h1, wraplength=620,
                     justify="left", anchor="w").pack(fill="x", padx=10, pady=6)

    def _append_log(self, cpu, gpu, fps, problems):
        parts = [f"[{ts()}]"]
        if fps["fps"] is not None:
            f   = fps["fps"]
            avg = fps["avg"] or f
            parts.append(f"FPS:{f:.0f}(avg {avg:.0f})")
        if gpu and "temp" in gpu:
            parts.append(f"GPU:{gpu['temp']}°C {gpu['usage']}% VRAM:{gpu['vram_pct']:.0f}% CLK:{gpu['clock']}MHz")
        parts.append(f"CPU:{cpu['usage']:.0f}%")
        if cpu["temp"]:
            parts.append(f"{cpu['temp']:.0f}°C")
        parts.append(f"RAM:{cpu['ram_pct']:.0f}%")
        if problems:
            parts.append(f"⚠{len(problems)}")

        line = "  ".join(parts) + "\n"
        tag  = "crit" if any(p[0]=="CRITICAL" for p in problems) else \
               "warn" if problems else "fps" if fps["fps"] else "ok"

        self._log.configure(state="normal")
        self._log.insert("end", line, tag)
        rows = int(self._log.index("end-1c").split(".")[0])
        if rows > 500:
            self._log.delete("1.0", f"{rows-500}.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── Report ─────────────────────────────────────────────────────────────────

    def _save_report(self):
        if not self._history:
            messagebox.showinfo("No data", "No data collected yet.")
            return

        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        fname = f"wow_diag_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path  = os.path.join(desktop, fname)

        ge = [e for e in self._history if e["gpu"] and "temp" in e["gpu"]]
        ce = self._history
        fe = [e for e in self._history if e["fps"]["fps"] is not None]

        def avg(v): return sum(v)/len(v) if v else 0

        gpu_temps  = [e["gpu"]["temp"]     for e in ge]
        gpu_usage  = [e["gpu"]["usage"]    for e in ge]
        gpu_clocks = [e["gpu"]["clock"]    for e in ge]
        gpu_vram   = [e["gpu"]["vram_pct"] for e in ge]
        cpu_usage  = [e["cpu"]["usage"]    for e in ce]
        cpu_temps  = [e["cpu"]["temp"]     for e in ce if e["cpu"]["temp"]]
        ram_pct    = [e["cpu"]["ram_pct"]  for e in ce]
        fps_vals   = [e["fps"]["fps"]      for e in fe]

        all_problems = {}
        for e in self._history:
            for sev, title, detail in e["problems"]:
                if title not in all_problems:
                    all_problems[title] = {"severity": sev, "detail": detail, "count": 0}
                all_problems[title]["count"] += 1

        last    = self._history[-1]
        gpu     = last["gpu"] or {}
        cpu     = last["cpu"]
        fps_end = last["fps"]

        lines = [
            "# WoW PC Diagnostic Report",
            f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Session start: {self._start_ts}",
            f"Samples: {len(self._history)}  |  Platform: {platform.platform()}",
            "",
            "---",
            "## System",
            "",
            "| Component | Value |",
            "|---|---|",
            f"| GPU | {gpu.get('name','N/A')} |",
            f"| RAM | {cpu['ram_total']:.0f} GB |",
            f"| RAM Speed | {cpu.get('ram_speed','N/A')} MHz |",
            "",
            "---",
            "## FPS Summary",
            "",
        ]

        if fps_vals:
            dip_total = fps_end.get("dip_count", 0)
            lines += [
                "| Metric | Value |",
                "|---|---|",
                f"| Average FPS | {avg(fps_vals):.0f} |",
                f"| Min FPS | {min(fps_vals):.0f} |",
                f"| Max FPS | {max(fps_vals):.0f} |",
                f"| Dip Events (<75% of avg) | {dip_total} |",
                "",
                "### Recent FPS Dips",
                "",
            ]
            dips = fps_end.get("dips", [])
            if dips:
                lines.append("| Time | FPS |")
                lines.append("|---|---|")
                for t_d, f_d in dips:
                    lines.append(f"| {t_d} | {f_d} |")
            else:
                lines.append("No dips recorded.")
        else:
            lines.append("FPS not captured — WoW was not running or PresentMon unavailable.")

        lines += [
            "",
            "---",
            "## Hardware Statistics",
            "",
            "### GPU",
            "| Metric | Min | Avg | Max |",
            "|---|---|---|---|",
        ]
        if gpu_temps:
            lines += [
                f"| Temp (°C) | {min(gpu_temps)} | {avg(gpu_temps):.1f} | {max(gpu_temps)} |",
                f"| Usage (%) | {min(gpu_usage)} | {avg(gpu_usage):.1f} | {max(gpu_usage)} |",
                f"| Clock (MHz) | {min(gpu_clocks)} | {avg(gpu_clocks):.0f} | {max(gpu_clocks)} |",
                f"| VRAM (%) | {min(gpu_vram):.0f} | {avg(gpu_vram):.1f} | {max(gpu_vram):.0f} |",
            ]
        lines += [
            "",
            "### CPU & RAM",
            "| Metric | Min | Avg | Max |",
            "|---|---|---|---|",
            f"| CPU Usage (%) | {min(cpu_usage):.0f} | {avg(cpu_usage):.1f} | {max(cpu_usage):.0f} |",
        ]
        if cpu_temps:
            lines.append(
                f"| CPU Temp (°C) | {min(cpu_temps):.0f} | {avg(cpu_temps):.1f} | {max(cpu_temps):.0f} |")
        lines += [
            f"| RAM Usage (%) | {min(ram_pct):.0f} | {avg(ram_pct):.1f} | {max(ram_pct):.0f} |",
            "",
            "---",
            "## Detected Problems",
            "",
        ]
        if all_problems:
            for title, info in sorted(all_problems.items(),
                                       key=lambda x: x[1]["severity"] != "CRITICAL"):
                icon = "🔴" if info["severity"] == "CRITICAL" else "🟡"
                pct  = info["count"] / len(self._history) * 100
                lines += [
                    f"### {icon} {info['severity']}: {title}",
                    f"**Frequency:** {info['count']}/{len(self._history)} samples ({pct:.0f}%)",
                    "",
                    info["detail"],
                    "",
                ]
        else:
            lines.append("✅ No problems detected.")

        lines += [
            "",
            "---",
            "## Raw Log (last 60 samples)",
            "```",
            "Timestamp            | FPS   | GPU°C | GPU%  | VRAM% | CLKMHz | CPU%  | CPU°C | RAM%",
            "-" * 95,
        ]
        for e in self._history[-60:]:
            g = e["gpu"] or {}
            c = e["cpu"]
            f = e["fps"]["fps"]
            lines.append(
                f"{e['ts']}  | "
                f"{f:5.0f} | " if f else "  --- | " +
                f"{g.get('temp',0):4d}° | {g.get('usage',0):4d}% | "
                f"{g.get('vram_pct',0):4.0f}% | {g.get('clock',0):6d} | "
                f"{c['usage']:4.0f}% | "
                f"{c['temp']:5.1f}° | " if c["temp"] else "   --° | " +
                f"{c['ram_pct']:4.0f}%"
            )
        lines += ["```", "", "---", "_Generated by WoW PC Diagnostic Monitor_"]

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))

        messagebox.showinfo("Report saved",
                            f"Saved to Desktop:\n{fname}\n\nShare this file for remote diagnosis.")

    def _on_close(self):
        self._running = False
        self._fps.stop()
        self.destroy()


if __name__ == "__main__":
    psutil.cpu_percent(interval=0.1)
    app = Monitor()
    app.mainloop()
