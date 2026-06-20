"""
Background Process Scanner
Finds processes that eat CPU/RAM/disk/network and hurt gaming performance.
Imported by monitor.py
"""

import psutil
import os
import time

# ── Known offenders ────────────────────────────────────────────────────────────
# (process_name_lower, severity, reason)
KNOWN_BAD = [
    # Overlays / hooks
    ("discord.exe",            "INFO",    "Discord — disable hardware acceleration + overlay in Discord settings"),
    ("discordptb.exe",         "INFO",    "Discord PTB — disable overlay"),
    ("discordcanary.exe",      "INFO",    "Discord Canary — disable overlay"),
    ("geforceexperience.exe",  "WARN",    "GeForce Experience — disable in-game overlay (Alt+Z settings)"),
    ("nvcontainer.exe",        "INFO",    "NVIDIA container — needed for GPU but can spike CPU"),
    ("obs64.exe",              "WARN",    "OBS Studio — recording/streaming eats GPU encoder bandwidth"),
    ("obs32.exe",              "WARN",    "OBS (32-bit) — recording/streaming active"),
    ("streamlabs obs.exe",     "WARN",    "Streamlabs OBS — streaming active, GPU/CPU impact"),
    ("xsplit.core.exe",        "WARN",    "XSplit — streaming active"),
    # Antivirus / security (scan spikes)
    ("msmpeng.exe",            "WARN",    "Windows Defender (MsMpEng) — can cause 1-3s lag spikes during real-time scans. Add WoW folder to exclusions."),
    ("avp.exe",                "WARN",    "Kaspersky AV — add WoW folder to exclusions"),
    ("avgnt.exe",              "WARN",    "Avira AV — add WoW folder to exclusions"),
    ("mbam.exe",               "WARN",    "Malwarebytes — disable real-time protection while gaming"),
    ("mbamservice.exe",        "WARN",    "Malwarebytes service — consider disabling while gaming"),
    ("mcshield.exe",           "WARN",    "McAfee — notorious CPU spiker, add WoW to exclusions"),
    ("avgui.exe",              "INFO",    "AVG Antivirus running"),
    ("avguix.exe",             "INFO",    "AVG Antivirus UI"),
    ("bdagent.exe",            "WARN",    "Bitdefender — can spike on file access, add WoW to exclusions"),
    ("nortonsecurity.exe",     "WARN",    "Norton Security — add WoW to exclusions"),
    # Windows background tasks
    ("searchindexer.exe",      "WARN",    "Windows Search Indexer — reschedules aggressively, can spike disk I/O"),
    ("tiworker.exe",           "WARN",    "Windows Update TiWorker — Windows is updating in background, high CPU"),
    ("wuauclt.exe",            "WARN",    "Windows Update client — update downloading in background"),
    ("svchost.exe",            "INFO",    "Windows Service Host — check if high CPU (normal to see many)"),
    ("wsappx.exe",             "WARN",    "Windows Store App update — downloading in background"),
    ("compattelrunner.exe",    "WARN",    "Windows Telemetry — background data collection, CPU spikes"),
    ("dism.exe",               "WARN",    "Windows DISM — system imaging/update task running"),
    ("msiexec.exe",            "INFO",    "Windows Installer — something installing in background"),
    # Game launchers / updaters
    ("battle.net.exe",         "INFO",    "Battle.net launcher — needed for WoW, low impact normally"),
    ("agent.exe",              "INFO",    "Battle.net Agent — update checker, close other games' update tasks"),
    ("steam.exe",              "INFO",    "Steam — generally low impact but shader pre-compilation can spike GPU"),
    ("steamwebhelper.exe",     "INFO",    "Steam web helper — can use RAM"),
    ("epicgameslauncher.exe",  "WARN",    "Epic Games Launcher — known background CPU usage, close if not needed"),
    ("rockstarservice.exe",    "INFO",    "Rockstar Games launcher"),
    ("riotclientservices.exe", "INFO",    "Riot Games client"),
    ("leagueclient.exe",       "WARN",    "League of Legends client — background CPU/RAM usage"),
    ("valorant.exe",           "WARN",    "Valorant running — two games at once sharing GPU"),
    # Browser
    ("chrome.exe",             "INFO",    "Google Chrome — GPU process & RAM usage; disable hardware acceleration if sharing GPU"),
    ("firefox.exe",            "INFO",    "Firefox — background tabs can use CPU; suspend unused tabs"),
    ("msedge.exe",             "INFO",    "Microsoft Edge — especially if running many tabs"),
    ("brave.exe",              "INFO",    "Brave browser"),
    # Creative / heavy tools
    ("adobe desktop service.exe", "INFO", "Adobe background service"),
    ("adobeupdater.exe",       "WARN",    "Adobe Updater — downloading in background"),
    ("acrobat.exe",            "INFO",    "Adobe Acrobat open"),
    ("photoshop.exe",          "WARN",    "Photoshop open — large RAM footprint"),
    ("premiere pro.exe",       "CRIT",    "Adobe Premiere Pro — massive GPU/RAM usage, close before gaming"),
    ("afterfx.exe",            "CRIT",    "Adobe After Effects — close before gaming"),
    ("davinci resolve.exe",    "CRIT",    "DaVinci Resolve — GPU-heavy, close before gaming"),
    # Miners / background GPU abuse
    ("xmrig.exe",              "CRIT",    "XMRig crypto miner — using your CPU/GPU resources"),
    ("nbminer.exe",            "CRIT",    "NBMiner — crypto miner, close immediately"),
    ("phoenixminer.exe",       "CRIT",    "PhoenixMiner — crypto miner"),
    ("t-rex.exe",              "CRIT",    "T-Rex miner — GPU miner"),
    # Network
    ("onedrive.exe",           "WARN",    "OneDrive — can spike disk I/O during sync; pause sync while gaming"),
    ("dropbox.exe",            "INFO",    "Dropbox — sync activity can spike disk"),
    ("googledrivesync.exe",    "INFO",    "Google Drive sync"),
    ("googledrivefs.exe",      "INFO",    "Google Drive FS"),
    # Misc
    ("razer synapse.exe",      "INFO",    "Razer Synapse — RGB/peripheral software, minor CPU"),
    ("corsairservice.exe",     "INFO",    "Corsair iCUE — RGB software"),
    ("ipoint.exe",             "INFO",    "Microsoft IntelliPoint mouse software"),
    ("logioptionsplus.exe",    "INFO",    "Logitech Options+ — peripheral software"),
    ("lghub.exe",              "INFO",    "Logitech G Hub — peripheral software"),
    ("nahimicservice.exe",     "WARN",    "Nahimic audio service — known to cause game stutters and FPS drops. Disable in Services."),
    ("nahimicsvc32.exe",       "WARN",    "Nahimic (32-bit) — known game stutter cause. Disable Nahimic service."),
    ("a-volute.exe",           "WARN",    "A-Volute / Nahimic — audio enhancement causing stutters"),
    ("sonic studio.exe",       "WARN",    "Sonic Studio — can conflict with game audio and cause hitches"),
    ("realtek hd audio manager","INFO",   "Realtek audio manager"),
    ("spotify.exe",            "INFO",    "Spotify — generally fine; disable hardware acceleration in Spotify settings"),
    ("teams.exe",              "WARN",    "Microsoft Teams — significant RAM/CPU even when idle"),
    ("slack.exe",              "INFO",    "Slack — background notifications, minor impact"),
    ("zoom.exe",               "WARN",    "Zoom — camera/mic processing uses CPU/GPU even in background"),
    ("webex.exe",              "WARN",    "Cisco WebEx — similar to Zoom"),
    ("skype.exe",              "INFO",    "Skype"),
    ("taskmgr.exe",            "INFO",    "Task Manager open"),
    ("perfmon.exe",            "INFO",    "Performance Monitor open"),
]

KNOWN_BAD_MAP = {name: (sev, reason) for name, sev, reason in KNOWN_BAD}

# Severity sort order
SEV_ORDER = {"CRIT": 0, "WARN": 1, "INFO": 2}


def scan_processes():
    """
    Returns a dict with:
      flagged   - list of flagged processes with details
      top_cpu   - top 5 processes by CPU
      top_ram   - top 5 processes by RAM
      high_disk - processes with high disk I/O
      network   - processes using significant network
      summary   - list of plain-text summary lines
    """
    flagged   = []
    all_procs = []

    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_info", "status",
         "io_counters", "net_connections"]
    ):
        try:
            info = proc.info
            name = info["name"] or ""
            name_l = name.lower()
            cpu  = proc.cpu_percent(interval=None)
            mem  = info["memory_info"]
            ram_mb = (mem.rss / 1024**2) if mem else 0

            all_procs.append({
                "pid":    info["pid"],
                "name":   name,
                "cpu":    cpu,
                "ram_mb": ram_mb,
            })

            # Check known bad list
            if name_l in KNOWN_BAD_MAP:
                sev, reason = KNOWN_BAD_MAP[name_l]
                flagged.append({
                    "pid":    info["pid"],
                    "name":   name,
                    "cpu":    cpu,
                    "ram_mb": ram_mb,
                    "sev":    sev,
                    "reason": reason,
                })
            # Dynamic detection: unknown process eating lots of CPU
            elif cpu > 15 and name_l not in ("wow.exe", "wowt.exe", "system idle process", ""):
                flagged.append({
                    "pid":    info["pid"],
                    "name":   name,
                    "cpu":    cpu,
                    "ram_mb": ram_mb,
                    "sev":    "WARN",
                    "reason": f"Unknown process using {cpu:.0f}% CPU — investigate in Task Manager",
                })
            # Dynamic: RAM hog > 1.5GB (not WoW itself)
            elif ram_mb > 1500 and name_l not in ("wow.exe", "wowt.exe", "system", ""):
                flagged.append({
                    "pid":    info["pid"],
                    "name":   name,
                    "cpu":    cpu,
                    "ram_mb": ram_mb,
                    "sev":    "INFO",
                    "reason": f"Large RAM footprint ({ram_mb:.0f}MB) — not necessarily harmful but reduces available memory",
                })

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Sort: CRIT first, then WARN, then INFO; within each by CPU desc
    flagged.sort(key=lambda x: (SEV_ORDER.get(x["sev"], 9), -x["cpu"]))

    # Top CPU (exclude system idle)
    top_cpu = sorted(
        [p for p in all_procs if p["name"].lower() != "system idle process"],
        key=lambda x: x["cpu"], reverse=True
    )[:8]

    # Top RAM
    top_ram = sorted(all_procs, key=lambda x: x["ram_mb"], reverse=True)[:8]

    # Windows power plan check
    power_plan = _check_power_plan()

    # Startup items count
    startup_count = _count_startup_items()

    return {
        "flagged":       flagged,
        "top_cpu":       top_cpu,
        "top_ram":       top_ram,
        "power_plan":    power_plan,
        "startup_count": startup_count,
        "total_procs":   len(all_procs),
    }


def _check_power_plan():
    try:
        out = subprocess.check_output(
            ["powercfg", "/getactivescheme"],
            stderr=subprocess.DEVNULL, text=True, timeout=5
        )
        if "Balanced" in out and "High performance" not in out:
            return ("WARN",
                    "Windows power plan is BALANCED — switch to High Performance for gaming. "
                    "Control Panel → Power Options → High Performance.")
        elif "Power saver" in out:
            return ("CRIT",
                    "Windows power plan is POWER SAVER — this is actively throttling your CPU. "
                    "Switch to High Performance immediately.")
        elif "High performance" in out or "Ultimate" in out:
            return ("OK", "High Performance power plan active ✓")
        else:
            return ("INFO", f"Power plan: {out.strip()[:80]}")
    except Exception:
        return ("INFO", "Could not check power plan (run as admin)")


def _count_startup_items():
    try:
        import winreg
        count = 0
        keys = [
            (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        ]
        for hive, path in keys:
            try:
                key = winreg.OpenKey(hive, path)
                count += winreg.QueryInfoKey(key)[1]
                winreg.CloseKey(key)
            except Exception:
                pass
        return count
    except Exception:
        return None


import subprocess
