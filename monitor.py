"""
WoW PC Diagnostic Monitor
Monitors GPU/CPU/RAM in real-time, detects problems, and saves a report.
Install: pip install psutil pynvml wmi pywin32
Run:     python monitor.py
"""

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox
import threading
import time
import datetime
import json
import os
import sys
import platform

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

# ── Palette ────────────────────────────────────────────────────────────────────
BG       = "#0f0f0f"
PANEL    = "#161616"
BORDER   = "#2a2a2a"
FG       = "#e0e0e0"
DIM      = "#555555"
GOOD     = "#00e676"
WARN     = "#ffeb3b"
CRIT     = "#ff1744"
HEADER   = "#00bcd4"
ACCENT   = "#7c4dff"
WHITE    = "#ffffff"

# ── Thresholds ─────────────────────────────────────────────────────────────────
T = {
    "gpu_temp":  (75, 83),
    "cpu_temp":  (80, 90),
    "gpu_usage": (90, 99),
    "cpu_usage": (75, 95),
    "ram_pct":   (75, 90),
    "vram_pct":  (80, 95),
    "gpu_clock_drop_pct": 10,   # % drop from max = throttle suspected
}

LOG_INTERVAL   = 1.0    # seconds between log entries
MAX_LOG_POINTS = 3600   # keep 1 hour in memory

# ── Helpers ────────────────────────────────────────────────────────────────────

def color_val(v, lo, hi):
    if v is None:
        return DIM
    if v >= hi:
        return CRIT
    if v >= lo:
        return WARN
    return GOOD


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── Data collection ────────────────────────────────────────────────────────────

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
        throttle_reasons = 0
        try:
            throttle_reasons = pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(GPU_HANDLE)
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
            "fan": fan_speed,
            "throttle_reasons": throttle_reasons,
        }
    except Exception as e:
        return {"error": str(e)}


def collect_cpu():
    usage    = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    freq     = psutil.cpu_freq()
    ram      = psutil.virtual_memory()
    swap     = psutil.swap_memory()

    temp = None
    try:
        all_temps = psutil.sensors_temperatures()
        if all_temps:
            for key in ("coretemp", "k10temp", "cpu-thermal", "acpitz"):
                if key in all_temps:
                    vals = [t.current for t in all_temps[key] if t.current > 0]
                    if vals:
                        temp = max(vals)
                        break
    except Exception:
        pass

    # RAM speed via WMI
    ram_speed = None
    if HAS_WMI:
        try:
            for stick in WMI.Win32_PhysicalMemory():
                if stick.Speed:
                    ram_speed = int(stick.Speed)
                    break
        except Exception:
            pass

    # Disk for WoW drive
    disk = psutil.disk_usage(os.path.splitdrive(sys.executable)[0] + "\\")

    return {
        "usage": usage, "per_core": per_core,
        "freq_mhz": freq.current if freq else 0,
        "freq_max": freq.max if freq else 0,
        "temp": temp,
        "ram_used": ram.used / 1024**3,
        "ram_total": ram.total / 1024**3,
        "ram_pct": ram.percent,
        "ram_speed": ram_speed,
        "swap_pct": swap.percent,
        "disk_pct": disk.percent,
        "disk_free_gb": disk.free / 1024**3,
    }


def detect_problems(cpu, gpu, history):
    issues = []

    # GPU thermal throttle
    if gpu and "temp" in gpu:
        if gpu["temp"] >= T["gpu_temp"][1]:
            issues.append(("CRITICAL", "GPU THERMAL THROTTLE",
                           f"GPU at {gpu['temp']}°C — card is throttling clocks to protect itself. "
                           "Check GPU fan curve, case airflow, and thermal paste."))
        elif gpu["temp"] >= T["gpu_temp"][0]:
            issues.append(("WARNING", "GPU running hot",
                           f"GPU at {gpu['temp']}°C — approaching throttle territory."))

        # Clock drop detection
        if gpu.get("max_clock") and gpu["max_clock"] > 0:
            drop_pct = (1 - gpu["clock"] / gpu["max_clock"]) * 100
            if drop_pct >= T["gpu_clock_drop_pct"] and gpu["usage"] > 60:
                issues.append(("WARNING", "GPU clock below max under load",
                               f"Running at {gpu['clock']} / {gpu['max_clock']} MHz "
                               f"({drop_pct:.0f}% below max) with {gpu['usage']}% load. "
                               "Possible throttle or power limit hit."))

        # Power limit
        if gpu.get("power_limit") and gpu.get("power"):
            if gpu["power"] >= gpu["power_limit"] * 0.97:
                issues.append(("WARNING", "GPU at power limit",
                               f"Drawing {gpu['power']:.0f}W / {gpu['power_limit']:.0f}W limit. "
                               "GPU is power-limited — check PSU and power limit setting in Afterburner."))

        # VRAM
        if gpu["vram_pct"] >= T["vram_pct"][1]:
            issues.append(("CRITICAL", "VRAM nearly full",
                           f"VRAM at {gpu['vram_used']:.1f}/{gpu['vram_total']:.0f}GB "
                           f"({gpu['vram_pct']:.0f}%). WoW will stutter loading textures. "
                           "Lower Texture Quality to 7."))
        elif gpu["vram_pct"] >= T["vram_pct"][0]:
            issues.append(("WARNING", "VRAM usage high",
                           f"VRAM at {gpu['vram_pct']:.0f}% — watch for texture streaming hitches."))

    # CPU thermal
    if cpu["temp"] is not None:
        if cpu["temp"] >= T["cpu_temp"][1]:
            issues.append(("CRITICAL", "CPU OVERHEATING",
                           f"CPU at {cpu['temp']:.0f}°C — throttling. "
                           "Check CPU cooler, thermal paste, and airflow."))
        elif cpu["temp"] >= T["cpu_temp"][0]:
            issues.append(("WARNING", "CPU running hot",
                           f"CPU at {cpu['temp']:.0f}°C."))

    # CPU clock vs max
    if cpu["freq_max"] > 0:
        freq_ratio = cpu["freq_mhz"] / cpu["freq_max"]
        if freq_ratio < 0.6 and cpu["usage"] > 50:
            issues.append(("WARNING", "CPU clock low under load",
                           f"Running at {cpu['freq_mhz']:.0f} / {cpu['freq_max']:.0f} MHz "
                           f"({freq_ratio*100:.0f}%) with {cpu['usage']:.0f}% CPU load. "
                           "Check Windows power plan — set to High Performance or AMD Balanced."))

    # RAM speed
    if cpu["ram_speed"] is not None:
        if cpu["ram_speed"] < 2800:
            issues.append(("CRITICAL", "RAM running below rated speed",
                           f"RAM detected at {cpu['ram_speed']}MHz. "
                           "Your kit is rated for 3200MHz — XMP is OFF in BIOS. "
                           "Enable XMP/DOCP in BIOS for a significant FPS boost."))
        elif cpu["ram_speed"] < 3100:
            issues.append(("WARNING", "RAM speed slightly low",
                           f"RAM at {cpu['ram_speed']}MHz (rated 3200MHz). "
                           "Check XMP setting in BIOS."))

    # RAM usage
    if cpu["ram_pct"] >= T["ram_pct"][1]:
        issues.append(("WARNING", "High RAM usage",
                       f"RAM at {cpu['ram_used']:.1f}/{cpu['ram_total']:.0f}GB "
                       f"({cpu['ram_pct']:.0f}%). Close background apps."))

    # Disk space
    if cpu["disk_pct"] >= 90:
        issues.append(("WARNING", "Disk nearly full",
                       f"Drive is {cpu['disk_pct']:.0f}% full ({cpu['disk_free_gb']:.1f}GB free). "
                       "Windows needs headroom for temp files and shader cache."))

    # Sustained high CPU usage (WoW single-thread)
    if cpu["usage"] >= 85 and (not gpu or gpu.get("usage", 0) < 70):
        issues.append(("WARNING", "CPU bottleneck detected",
                       f"CPU at {cpu['usage']:.0f}% while GPU is low. "
                       "WoW is CPU-bound on its main thread — this is the cause of FPS dips."))

    return issues


# ── Main App ───────────────────────────────────────────────────────────────────

class Monitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WoW PC Diagnostic Monitor")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.geometry("680x800")

        self._history   = []
        self._problems  = []
        self._running   = True
        self._start_ts  = now_iso()
        self._gpu_name  = "Unknown GPU"
        self._log_lines = []

        self._mono  = tkfont.Font(family="Consolas", size=10)
        self._mono_b = tkfont.Font(family="Consolas", size=10, weight="bold")
        self._big   = tkfont.Font(family="Consolas", size=12, weight="bold")
        self._h1    = tkfont.Font(family="Consolas", size=9)

        self._build_ui()

        # warm up psutil
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        # Title bar
        hdr = tk.Frame(self, bg=ACCENT, height=3)
        hdr.pack(fill="x")

        title_row = tk.Frame(self, bg=BG)
        title_row.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(title_row, text="WoW PC Diagnostic Monitor",
                 fg=WHITE, bg=BG, font=self._big).pack(side="left")
        self._status_dot = tk.Label(title_row, text="●", fg=GOOD, bg=BG,
                                     font=self._big)
        self._status_dot.pack(side="right")
        self._status_lbl = tk.Label(title_row, text="Starting…",
                                     fg=DIM, bg=BG, font=self._h1)
        self._status_lbl.pack(side="right", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._style_notebook()

        self._tab_live     = tk.Frame(nb, bg=BG)
        self._tab_problems = tk.Frame(nb, bg=BG)
        self._tab_log      = tk.Frame(nb, bg=BG)

        nb.add(self._tab_live,     text="  Live Stats  ")
        nb.add(self._tab_problems, text="  Problems  ")
        nb.add(self._tab_log,      text="  Log  ")

        self._build_live(self._tab_live)
        self._build_problems(self._tab_problems)
        self._build_log(self._tab_log)

        # Bottom bar
        bot = tk.Frame(self, bg=PANEL, pady=6)
        bot.pack(fill="x", side="bottom")
        tk.Button(bot, text="💾  Save Report",
                  command=self._save_report,
                  bg=ACCENT, fg=WHITE, font=self._mono_b,
                  relief="flat", padx=14, pady=4,
                  cursor="hand2").pack(side="left", padx=12)
        tk.Button(bot, text="🗑  Clear Log",
                  command=self._clear_log,
                  bg=BORDER, fg=FG, font=self._mono,
                  relief="flat", padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=4)
        self._uptime_lbl = tk.Label(bot, text="Uptime: 0s",
                                     fg=DIM, bg=PANEL, font=self._h1)
        self._uptime_lbl.pack(side="right", padx=12)

    def _style_notebook(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook",          background=BG,    borderwidth=0)
        style.configure("TNotebook.Tab",      background=PANEL, foreground=DIM,
                        padding=[12, 5],      font=("Consolas", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", BORDER)],
                  foreground=[("selected", WHITE)])

    def _build_live(self, parent):
        scroll = self._scrollable(parent)

        self._sec_gpu = self._section(scroll, "GPU")
        self._r_gpu_name  = self._stat_row(self._sec_gpu, "Model")
        self._r_gpu_temp  = self._stat_row(self._sec_gpu, "Temperature")
        self._r_gpu_usage = self._stat_row(self._sec_gpu, "Core Usage")
        self._r_gpu_vram  = self._stat_row(self._sec_gpu, "VRAM")
        self._r_gpu_clock = self._stat_row(self._sec_gpu, "Core Clock")
        self._r_gpu_power = self._stat_row(self._sec_gpu, "Power Draw")
        self._r_gpu_fan   = self._stat_row(self._sec_gpu, "Fan Speed")
        self._r_gpu_throt = self._stat_row(self._sec_gpu, "Throttle")

        self._sec_cpu = self._section(scroll, "CPU")
        self._r_cpu_temp  = self._stat_row(self._sec_cpu, "Temperature")
        self._r_cpu_usage = self._stat_row(self._sec_cpu, "Overall Usage")
        self._r_cpu_freq  = self._stat_row(self._sec_cpu, "Clock Speed")
        self._r_cpu_cores = self._stat_row(self._sec_cpu, "Per-Core %")

        self._sec_ram = self._section(scroll, "RAM & STORAGE")
        self._r_ram       = self._stat_row(self._sec_ram, "System RAM")
        self._r_ram_speed = self._stat_row(self._sec_ram, "RAM Speed")
        self._r_swap      = self._stat_row(self._sec_ram, "Swap Usage")
        self._r_disk      = self._stat_row(self._sec_ram, "Disk")

    def _build_problems(self, parent):
        tk.Label(parent,
                 text="Problems are detected automatically and cleared each refresh.",
                 fg=DIM, bg=BG, font=self._h1,
                 wraplength=600, justify="left").pack(anchor="w", padx=14, pady=(10, 4))

        self._prob_frame = tk.Frame(parent, bg=BG)
        self._prob_frame.pack(fill="both", expand=True, padx=8)

        self._no_prob_lbl = tk.Label(self._prob_frame,
                                      text="✅  No problems detected yet.",
                                      fg=GOOD, bg=BG, font=self._mono_b)
        self._no_prob_lbl.pack(pady=30)

    def _build_log(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=8, pady=(8, 0))
        tk.Label(top, text="Live log — 1 entry per second",
                 fg=DIM, bg=BG, font=self._h1).pack(side="left")

        self._log_text = tk.Text(parent, bg=PANEL, fg=FG,
                                  font=("Consolas", 9),
                                  relief="flat", bd=0,
                                  state="disabled", wrap="none")
        sb = ttk.Scrollbar(parent, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True, padx=8, pady=6)

        self._log_text.tag_configure("crit", foreground=CRIT)
        self._log_text.tag_configure("warn", foreground=WARN)
        self._log_text.tag_configure("ok",   foreground=GOOD)
        self._log_text.tag_configure("dim",  foreground=DIM)

    # ── Section / row builders ─────────────────────────────────────────────────

    def _scrollable(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg=BG)
        frame.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        return frame

    def _section(self, parent, title):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=8, pady=(10, 0))
        tk.Label(outer, text=f"  {title}  ",
                 fg=HEADER, bg=BG, font=self._big).pack(anchor="w", pady=(0, 4))
        inner = tk.Frame(outer, bg=PANEL, pady=6, padx=10,
                         highlightbackground=BORDER, highlightthickness=1)
        inner.pack(fill="x")
        return inner

    def _stat_row(self, parent, label):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=f"{label:<18}", fg=DIM, bg=PANEL,
                 font=self._mono).pack(side="left")
        val = tk.Label(row, text="—", fg=FG, bg=PANEL, font=self._mono_b)
        val.pack(side="left")
        return val

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll_loop(self):
        t0 = time.time()
        while self._running:
            loop_start = time.time()
            try:
                gpu = collect_gpu()
                cpu = collect_cpu()
                problems = detect_problems(cpu, gpu, self._history)

                entry = {
                    "ts": now_iso(),
                    "cpu": cpu,
                    "gpu": gpu,
                    "problems": problems,
                }
                self._history.append(entry)
                if len(self._history) > MAX_LOG_POINTS:
                    self._history.pop(0)

                uptime = int(time.time() - t0)
                self.after(0, self._refresh_ui, cpu, gpu, problems, uptime)
            except Exception as e:
                self.after(0, self._status_lbl.config, {"text": f"Error: {e}"})

            elapsed = time.time() - loop_start
            time.sleep(max(0, LOG_INTERVAL - elapsed))

    # ── UI refresh ─────────────────────────────────────────────────────────────

    def _refresh_ui(self, cpu, gpu, problems, uptime):
        self._refresh_live(cpu, gpu)
        self._refresh_problems(problems)
        self._append_log(cpu, gpu, problems)

        any_crit = any(p[0] == "CRITICAL" for p in problems)
        any_warn = any(p[0] == "WARNING"  for p in problems)
        if any_crit:
            dot_color, status = CRIT, "CRITICAL ISSUE DETECTED"
        elif any_warn:
            dot_color, status = WARN, f"{len(problems)} warning(s)"
        else:
            dot_color, status = GOOD, "All systems OK"

        self._status_dot.config(fg=dot_color)
        self._status_lbl.config(text=status, fg=dot_color)
        self._uptime_lbl.config(text=f"Uptime: {uptime}s  |  Samples: {len(self._history)}")

    def _refresh_live(self, cpu, gpu):
        # GPU
        if gpu and "temp" in gpu:
            self._r_gpu_name.config(text=gpu["name"], fg=FG)
            self._r_gpu_temp.config(
                text=f"{gpu['temp']}°C",
                fg=color_val(gpu["temp"], *T["gpu_temp"]))
            self._r_gpu_usage.config(
                text=f"{gpu['usage']}%",
                fg=color_val(gpu["usage"], *T["gpu_usage"]))
            self._r_gpu_vram.config(
                text=f"{gpu['vram_used']:.1f} / {gpu['vram_total']:.0f} GB  ({gpu['vram_pct']:.0f}%)",
                fg=color_val(gpu["vram_pct"], *T["vram_pct"]))
            max_c = gpu.get("max_clock") or 0
            clock_str = f"{gpu['clock']} MHz"
            if max_c:
                clock_str += f"  (max {max_c} MHz)"
            self._r_gpu_clock.config(text=clock_str, fg=FG)
            pl = gpu.get("power_limit")
            pw_str = f"{gpu['power']:.0f}W"
            if pl:
                pw_str += f" / {pl:.0f}W limit"
            self._r_gpu_power.config(
                text=pw_str,
                fg=WARN if pl and gpu["power"] >= pl * 0.97 else FG)
            fan = gpu.get("fan")
            self._r_gpu_fan.config(
                text=f"{fan}%" if fan is not None else "N/A",
                fg=WARN if fan and fan > 85 else FG)

            tr = gpu.get("throttle_reasons", 0)
            throt_str, throt_color = "None", GOOD
            if tr and tr != 0x0000000000000001:  # 1 = "no throttle" on some drivers
                reasons = []
                if tr & 0x0000000000000002: reasons.append("GPU Idle")
                if tr & 0x0000000000000004: reasons.append("App Clocks")
                if tr & 0x0000000000000008: reasons.append("SW Power Cap")
                if tr & 0x0000000000000010: reasons.append("HW Slowdown")
                if tr & 0x0000000000000020: reasons.append("SW Thermal")
                if tr & 0x0000000000000040: reasons.append("HW Thermal")
                if tr & 0x0000000000000080: reasons.append("HW Power Brake")
                if tr & 0x0000000000000200: reasons.append("Sync Boost")
                if reasons:
                    throt_str  = ", ".join(reasons)
                    throt_color = CRIT if any(r in ("HW Thermal", "HW Slowdown", "HW Power Brake") for r in reasons) else WARN
            self._r_gpu_throt.config(text=throt_str, fg=throt_color)
        else:
            msg = "pynvml not installed — run: pip install pynvml" if not HAS_NVML else "Error reading GPU"
            for w in (self._r_gpu_name, self._r_gpu_temp, self._r_gpu_usage,
                      self._r_gpu_vram, self._r_gpu_clock, self._r_gpu_power,
                      self._r_gpu_fan, self._r_gpu_throt):
                w.config(text=msg, fg=DIM)

        # CPU
        temp_str = f"{cpu['temp']:.0f}°C" if cpu["temp"] else "N/A (run as admin)"
        self._r_cpu_temp.config(
            text=temp_str,
            fg=color_val(cpu["temp"], *T["cpu_temp"]) if cpu["temp"] else DIM)
        self._r_cpu_usage.config(
            text=f"{cpu['usage']:.1f}%",
            fg=color_val(cpu["usage"], *T["cpu_usage"]))
        freq_str = f"{cpu['freq_mhz']:.0f} MHz"
        if cpu["freq_max"]:
            freq_str += f"  (max {cpu['freq_max']:.0f} MHz)"
        self._r_cpu_freq.config(text=freq_str, fg=FG)
        core_parts = [f"C{i}:{v:.0f}%" for i, v in enumerate(cpu["per_core"])]
        cores_str = "  ".join(core_parts)
        self._r_cpu_cores.config(text=cores_str, fg=FG)

        # RAM
        self._r_ram.config(
            text=f"{cpu['ram_used']:.1f} / {cpu['ram_total']:.0f} GB  ({cpu['ram_pct']:.0f}%)",
            fg=color_val(cpu["ram_pct"], *T["ram_pct"]))
        if cpu["ram_speed"]:
            rs_color = CRIT if cpu["ram_speed"] < 2800 else (WARN if cpu["ram_speed"] < 3100 else GOOD)
            self._r_ram_speed.config(
                text=f"{cpu['ram_speed']} MHz  {'⚠ XMP OFF?' if cpu['ram_speed'] < 3000 else '✓ XMP OK'}",
                fg=rs_color)
        else:
            self._r_ram_speed.config(text="N/A (install pywin32)", fg=DIM)

        self._r_swap.config(
            text=f"{cpu['swap_pct']:.0f}%",
            fg=WARN if cpu["swap_pct"] > 20 else GOOD)
        self._r_disk.config(
            text=f"{cpu['disk_pct']:.0f}% full  ({cpu['disk_free_gb']:.1f} GB free)",
            fg=color_val(cpu["disk_pct"], 80, 90))

    def _refresh_problems(self, problems):
        for w in self._prob_frame.winfo_children():
            w.destroy()

        if not problems:
            tk.Label(self._prob_frame,
                     text="✅  No problems detected.",
                     fg=GOOD, bg=BG, font=self._mono_b).pack(pady=30)
            return

        for severity, title, detail in problems:
            color = CRIT if severity == "CRITICAL" else WARN
            card = tk.Frame(self._prob_frame, bg=PANEL,
                            highlightbackground=color, highlightthickness=1)
            card.pack(fill="x", pady=4, padx=4)
            hdr_row = tk.Frame(card, bg=color)
            hdr_row.pack(fill="x")
            tk.Label(hdr_row,
                     text=f"  {'🔴' if severity=='CRITICAL' else '🟡'} {severity}: {title}  ",
                     fg=WHITE, bg=color,
                     font=self._mono_b, anchor="w").pack(side="left", pady=3)
            tk.Label(card, text=detail,
                     fg=FG, bg=PANEL,
                     font=self._h1, wraplength=580,
                     justify="left", anchor="w").pack(fill="x", padx=10, pady=6)

    def _append_log(self, cpu, gpu, problems):
        line_parts = [f"[{ts()}]"]
        if gpu and "temp" in gpu:
            line_parts.append(f"GPU {gpu['temp']}°C {gpu['usage']}%")
            line_parts.append(f"VRAM {gpu['vram_pct']:.0f}%")
            line_parts.append(f"CLK {gpu['clock']}MHz")
        line_parts.append(f"CPU {cpu['usage']:.0f}%")
        if cpu["temp"]:
            line_parts.append(f"{cpu['temp']:.0f}°C")
        line_parts.append(f"RAM {cpu['ram_pct']:.0f}%")
        if problems:
            line_parts.append(f"⚠ {len(problems)} issue(s)")

        line = "  |  ".join(line_parts) + "\n"
        tag = "crit" if any(p[0]=="CRITICAL" for p in problems) else \
              "warn" if problems else "ok"

        self._log_text.configure(state="normal")
        self._log_text.insert("end", line, tag)
        # keep last 500 lines
        lines = int(self._log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self._log_text.delete("1.0", f"{lines-500}.0")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ── Report generation ──────────────────────────────────────────────────────

    def _save_report(self):
        if not self._history:
            messagebox.showinfo("No data", "No data collected yet.")
            return

        report_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(report_dir, exist_ok=True)
        fname = f"wow_diag_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path  = os.path.join(report_dir, fname)

        gpu_entries = [e for e in self._history if e["gpu"] and "temp" in e["gpu"]]
        cpu_entries = self._history

        def avg(vals):
            return sum(vals) / len(vals) if vals else 0

        gpu_temps   = [e["gpu"]["temp"]      for e in gpu_entries]
        gpu_usage   = [e["gpu"]["usage"]     for e in gpu_entries]
        gpu_clocks  = [e["gpu"]["clock"]     for e in gpu_entries]
        gpu_vram    = [e["gpu"]["vram_pct"]  for e in gpu_entries]
        cpu_usage   = [e["cpu"]["usage"]     for e in cpu_entries]
        cpu_temps   = [e["cpu"]["temp"]      for e in cpu_entries if e["cpu"]["temp"]]
        ram_pct     = [e["cpu"]["ram_pct"]   for e in cpu_entries]

        all_problems = {}
        for e in self._history:
            for sev, title, detail in e["problems"]:
                if title not in all_problems:
                    all_problems[title] = {"severity": sev, "detail": detail, "count": 0}
                all_problems[title]["count"] += 1

        last = self._history[-1]
        gpu  = last["gpu"] or {}
        cpu  = last["cpu"]

        lines = [
            "# WoW PC Diagnostic Report",
            f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Session start: {self._start_ts}",
            f"Samples collected: {len(self._history)}",
            f"Platform: {platform.system()} {platform.version()}",
            "",
            "---",
            "",
            "## System Overview",
            "",
            f"| Component | Value |",
            f"|---|---|",
            f"| GPU | {gpu.get('name', 'N/A')} |",
            f"| CPU | {platform.processor()} |",
            f"| RAM | {cpu['ram_total']:.0f} GB total |",
            f"| RAM Speed | {cpu.get('ram_speed', 'N/A')} MHz |",
            f"| OS | {platform.platform()} |",
            "",
            "---",
            "",
            "## Session Statistics",
            "",
            "### GPU",
            f"| Metric | Min | Avg | Max |",
            f"|---|---|---|---|",
        ]

        if gpu_temps:
            lines += [
                f"| Temperature (°C) | {min(gpu_temps)} | {avg(gpu_temps):.1f} | {max(gpu_temps)} |",
                f"| Core Usage (%) | {min(gpu_usage)} | {avg(gpu_usage):.1f} | {max(gpu_usage)} |",
                f"| Core Clock (MHz) | {min(gpu_clocks)} | {avg(gpu_clocks):.0f} | {max(gpu_clocks)} |",
                f"| VRAM Usage (%) | {min(gpu_vram):.0f} | {avg(gpu_vram):.1f} | {max(gpu_vram):.0f} |",
            ]
        else:
            lines.append("| No GPU data available | — | — | — |")

        lines += [
            "",
            "### CPU & RAM",
            f"| Metric | Min | Avg | Max |",
            f"|---|---|---|---|",
            f"| CPU Usage (%) | {min(cpu_usage):.0f} | {avg(cpu_usage):.1f} | {max(cpu_usage):.0f} |",
        ]
        if cpu_temps:
            lines.append(f"| CPU Temp (°C) | {min(cpu_temps):.0f} | {avg(cpu_temps):.1f} | {max(cpu_temps):.0f} |")
        lines += [
            f"| RAM Usage (%) | {min(ram_pct):.0f} | {avg(ram_pct):.1f} | {max(ram_pct):.0f} |",
            "",
            "---",
            "",
            "## Detected Problems",
            "",
        ]

        if all_problems:
            for title, info in sorted(all_problems.items(),
                                       key=lambda x: (x[1]["severity"] != "CRITICAL", x[0])):
                icon = "🔴" if info["severity"] == "CRITICAL" else "🟡"
                lines += [
                    f"### {icon} {info['severity']}: {title}",
                    f"**Detected in:** {info['count']} of {len(self._history)} samples ({info['count']/len(self._history)*100:.0f}%)",
                    f"",
                    f"{info['detail']}",
                    "",
                ]
        else:
            lines.append("✅ No problems detected during this session.")

        lines += [
            "",
            "---",
            "",
            "## Raw Log (last 60 samples)",
            "",
            "```",
            "Timestamp            | GPU°C | GPU% | VRAMused | CLKMHz | CPU% | CPU°C | RAM%",
            "-" * 80,
        ]

        for e in self._history[-60:]:
            g = e["gpu"] or {}
            c = e["cpu"]
            gt   = f"{g.get('temp',0):4d}" if "temp"    in g else "  --"
            gu   = f"{g.get('usage',0):3d}" if "usage"   in g else " --"
            gv   = f"{g.get('vram_pct',0):5.1f}" if "vram_pct" in g else "  --.-"
            gc   = f"{g.get('clock',0):5d}" if "clock"   in g else "   --"
            ct   = f"{c['temp']:5.1f}" if c["temp"] else "  --.-"
            lines.append(
                f"{e['ts']}  | {gt}°  | {gu}%  | {gv}%    | {gc}   | "
                f"{c['usage']:4.0f}% | {ct}° | {c['ram_pct']:4.0f}%"
            )

        lines += ["```", "", "---", "_Generated by WoW PC Diagnostic Monitor_"]

        report = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)

        messagebox.showinfo("Report saved",
                            f"Saved to Desktop:\n{fname}\n\nShare this file for diagnosis.")

    def _on_close(self):
        self._running = False
        self.destroy()


if __name__ == "__main__":
    # psutil needs a warm-up cycle
    psutil.cpu_percent(interval=0.1)
    app = Monitor()
    app.mainloop()
