#!/usr/bin/env python3
"""
Dell Latitude 7490 Fan Control
Fedora Linux — uses dell-smm-hwmon kernel module

Usage:
  sudo python3 dell-fan-control.py            # GUI mode
  sudo python3 dell-fan-control.py --headless # headless/service mode
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, os, glob, threading, time, json, sys, signal
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "dell-fan-control.json"

# Default temp curve: (threshold_°C, fan_level)
#   level 0=Off, 1=Low, 2=High
# Logic: below first threshold → use first entry's level;
#        at/above each threshold → update to that level.
DEFAULT_CURVE = [(40, 0), (55, 1), (70, 2)]

LEVEL_NAMES  = ["Off", "Low", "High"]
LEVEL_COLORS = ["#89dceb", "#a6e3a1", "#f38ba8"]

# ─────────────────────────────────────────────────────────────
#  Backend
# ─────────────────────────────────────────────────────────────

class FanController:
    def __init__(self):
        self.refresh()

    def refresh(self):
        self.i8k_path   = Path("/proc/i8k")
        self.i8k_ok     = self.i8k_path.exists()
        self.hwmon_path = self._find_dell_hwmon()
        self.has_i8kctl = self._which("i8kctl")

    # ── discovery ──────────────────────────────────────────

    def _find_dell_hwmon(self):
        for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
            nf = Path(hwmon) / "name"
            try:
                name = nf.read_text().strip().lower()
                if "dell" in name or "smm" in name:
                    return hwmon
            except OSError:
                pass
        return None

    def _which(self, cmd):
        try:
            return subprocess.run(["which", cmd], capture_output=True).returncode == 0
        except Exception:
            return False

    # ── temperature ────────────────────────────────────────

    def get_temps(self):
        """Return {label: °C} for all hwmon sensors (sanity-filtered)."""
        temps = {}
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                name = (Path(hwmon) / "name").read_text().strip()
            except OSError:
                continue
            for temp_in in sorted(glob.glob(f"{hwmon}/temp*_input")):
                try:
                    val = int(Path(temp_in).read_text()) / 1000
                    if not (0 < val < 120):
                        continue
                    lf = temp_in.replace("_input", "_label")
                    label = Path(lf).read_text().strip() if Path(lf).exists() else name
                    idx = temp_in.split("temp")[1].split("_")[0]
                    key = f"{name}/{label}" if label != name else f"{name}/temp{idx}"
                    temps[key] = val
                except (OSError, ValueError):
                    pass
        return temps

    def get_cpu_temp(self):
        """Best-effort CPU temp: prefer coretemp Package/Core, fall back to i8k."""
        temps = self.get_temps()
        candidates = []
        for key, val in temps.items():
            lkey = key.lower()
            if any(x in lkey for x in ("package", "pkg", "core", "coretemp", "cpu")):
                candidates.append(val)
        if candidates:
            return max(candidates)
        i8k = self.read_i8k()
        if i8k:
            return float(i8k[0])
        return None

    # ── i8k interface ─────────────────────────────────────

    def read_i8k(self):
        """
        Returns (cpu_temp, fan0_level, fan1_level, fan0_rpm, fan1_rpm) or None.
        /proc/i8k fields: version bios_ver serial cpu_temp fan0 fan1 rpm0 rpm1 ...
        """
        if not self.i8k_ok:
            return None
        try:
            fields = self.i8k_path.read_text().split()
            if len(fields) >= 8:
                return (int(fields[3]), int(fields[4]), int(fields[5]),
                        int(fields[6]), int(fields[7]))
        except (OSError, ValueError):
            pass
        return None

    # ── fan control ───────────────────────────────────────

    def set_fan(self, fan_num: int, level: int) -> bool:
        """Set fan_num (0 or 1) to level 0/1/2. Returns True on success."""
        if self.has_i8kctl:
            ok = self._set_via_i8kctl(fan_num, level)
            if ok:
                return True
        return self._set_via_hwmon(fan_num, level)

    def _set_via_i8kctl(self, fan_num, level):
        for prefix in ([], ["sudo"]):
            r = subprocess.run(prefix + ["i8kctl", "fan", str(fan_num), str(level)],
                               capture_output=True)
            if r.returncode == 0:
                return True
        return False

    def _set_via_hwmon(self, fan_num, level):
        if not self.hwmon_path:
            return False
        n = fan_num + 1
        pwm_f   = Path(self.hwmon_path) / f"pwm{n}"
        enable_f = Path(self.hwmon_path) / f"pwm{n}_enable"
        if not pwm_f.exists():
            return False
        pwm_val = {0: 0, 1: 120, 2: 255}.get(level, 255)
        try:
            if enable_f.exists():
                enable_f.write_text("1")
            pwm_f.write_text(str(pwm_val))
            return True
        except PermissionError:
            cmds = []
            if enable_f.exists():
                cmds.append(f"echo 1 > {enable_f}")
            cmds.append(f"echo {pwm_val} > {pwm_f}")
            r = subprocess.run(["sudo", "sh", "-c", " && ".join(cmds)],
                               capture_output=True)
            return r.returncode == 0

    # ── module loading ────────────────────────────────────

    def load_module(self):
        r = subprocess.run(
            ["sudo", "modprobe", "dell-smm-hwmon", "force=1", "ignore_dmi=1"],
            capture_output=True, text=True)
        time.sleep(0.8)
        self.refresh()
        return self.i8k_ok or self.hwmon_path is not None

    def status_text(self):
        parts = []
        if self.i8k_ok:
            parts.append("/proc/i8k")
        if self.hwmon_path:
            parts.append(self.hwmon_path)
        if self.has_i8kctl:
            parts.append("i8kctl")
        return "Active: " + ", ".join(parts) if parts else "Module not loaded"


# ─────────────────────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────────────────────

BG      = "#1e1e2e"
BG2     = "#181825"
PANEL   = "#313244"
FG      = "#cdd6f4"
ACCENT  = "#89b4fa"
SUBTEXT = "#a6adc8"
WARN    = "#fab387"


class FanControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Dell Fan Control")
        self.root.geometry("460x540")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        self.ctrl   = FanController()
        self.cfg    = self._load_config()
        self.mode   = tk.StringVar(value=self.cfg.get("mode", "auto"))
        self.curve  = self.cfg.get("curve", [list(x) for x in DEFAULT_CURVE])
        self.fan0   = tk.IntVar(value=1)
        self.fan1   = tk.IntVar(value=1)
        self._running = True

        self._build_ui()
        self._check_module()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    # ── config ────────────────────────────────────────────

    def _load_config(self):
        try:
            if CONFIG_FILE.exists():
                return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_config(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(
            {"mode": self.mode.get(), "curve": self.curve}, indent=2))

    # ── UI construction ───────────────────────────────────

    def _build_ui(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame",      background=BG)
        s.configure("TLabel",      background=BG, foreground=FG)
        s.configure("TRadiobutton",background=BG, foreground=FG)

        wrap = tk.Frame(self.root, bg=BG, padx=14, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        # Title
        tk.Label(wrap, text="Dell Latitude Fan Control",
                 bg=BG, fg=ACCENT, font=("sans-serif", 13, "bold")).pack(pady=(0, 10))

        # ── Temperatures ──
        self._temp_box = self._section(wrap, "Temperatures")
        self._temp_text = tk.Text(self._temp_box, height=4, width=48,
                                  bg=BG2, fg=FG, font=("monospace", 10),
                                  state="disabled", relief="flat", padx=4, pady=2,
                                  insertbackground=FG)
        self._temp_text.pack(fill=tk.X)

        # ── Fan Status ──
        fan_box = self._section(wrap, "Fan Status")
        self._fan_vars = []
        for i in range(2):
            row = tk.Frame(fan_box, bg=BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"  Fan {i}:", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=8, anchor="w").pack(side=tk.LEFT)
            v = tk.StringVar(value="—")
            tk.Label(row, textvariable=v, bg=BG, fg=WARN,
                     font=("monospace", 10)).pack(side=tk.LEFT)
            self._fan_vars.append(v)

        # ── Mode ──
        mode_box = self._section(wrap, "Control Mode")
        rb_row = tk.Frame(mode_box, bg=BG)
        rb_row.pack(anchor="w")
        for text, val in [("Auto (temp curve)", "auto"), ("Manual", "manual")]:
            tk.Radiobutton(rb_row, text=text, variable=self.mode, value=val,
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           font=("sans-serif", 10),
                           command=self._on_mode_change).pack(side=tk.LEFT, padx=6)

        # ── Swappable panel ──
        self._swap = tk.Frame(wrap, bg=BG)
        self._swap.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._auto_panel   = self._build_auto_panel(self._swap)
        self._manual_panel = self._build_manual_panel(self._swap)
        self._on_mode_change()

        # Status bar
        self._status = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self._status, bg=PANEL, fg=SUBTEXT,
                 font=("sans-serif", 9), anchor="w", padx=8).pack(
                     side=tk.BOTTOM, fill=tk.X)

    def _section(self, parent, title):
        frame = tk.LabelFrame(parent, text=f"  {title}  ",
                              bg=BG, fg=ACCENT, font=("sans-serif", 10),
                              padx=10, pady=6, relief="groove")
        frame.pack(fill=tk.X, pady=(0, 8))
        return frame

    # ── Auto panel ────────────────────────────────────────

    def _build_auto_panel(self, parent):
        box = tk.Frame(parent, bg=BG)
        tk.Label(box, text="Temperature thresholds  →  fan level",
                 bg=BG, fg=SUBTEXT, font=("sans-serif", 9)).pack(anchor="w", pady=(0, 4))
        self._curve_rows_frame = tk.Frame(box, bg=BG)
        self._curve_rows_frame.pack(fill=tk.X)
        self._refresh_curve_rows()
        tk.Button(box, text="+ Add Step", command=self._add_curve_step,
                  bg=PANEL, fg=FG, relief="flat", padx=8, pady=2,
                  activebackground=ACCENT, font=("sans-serif", 9)).pack(
                      anchor="w", pady=(6, 0))
        return box

    def _refresh_curve_rows(self):
        for w in self._curve_rows_frame.winfo_children():
            w.destroy()

        for i, entry in enumerate(self.curve):
            temp, level = entry[0], entry[1]
            row = tk.Frame(self._curve_rows_frame, bg=BG)
            row.pack(fill=tk.X, pady=1)

            lbl_pre = "From" if i == 0 else "  At"
            tk.Label(row, text=f"{lbl_pre}", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=5, anchor="e").pack(side=tk.LEFT)

            tv = tk.StringVar(value=str(temp))
            tk.Entry(row, textvariable=tv, width=4, bg=PANEL, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=("monospace", 10)).pack(side=tk.LEFT, padx=3)

            tk.Label(row, text="°C →", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10)).pack(side=tk.LEFT, padx=(0, 4))

            lv = tk.IntVar(value=level)
            for val, (lname, lcol) in enumerate(zip(LEVEL_NAMES, LEVEL_COLORS)):
                tk.Radiobutton(row, text=lname, variable=lv, value=val,
                               bg=BG, fg=lcol, selectcolor=PANEL,
                               activebackground=BG,
                               font=("sans-serif", 10)).pack(side=tk.LEFT, padx=2)

            # bind updates
            def _make_updater(idx, tv_, lv_):
                def _upd(*_):
                    try:
                        self.curve[idx] = [int(tv_.get()), lv_.get()]
                        self._save_config()
                    except ValueError:
                        pass
                return _upd
            upd = _make_updater(i, tv, lv)
            tv.trace_add("write", upd)
            lv.trace_add("write", upd)

            if i > 0:
                def _make_del(idx):
                    def _del():
                        self.curve.pop(idx)
                        self._refresh_curve_rows()
                        self._save_config()
                    return _del
                tk.Button(row, text="✕", command=_make_del(i),
                          bg=BG, fg="#f38ba8", relief="flat", padx=4,
                          font=("sans-serif", 9)).pack(side=tk.RIGHT)

    def _add_curve_step(self):
        last_t = self.curve[-1][0] if self.curve else 60
        self.curve.append([last_t + 10, 2])
        self._refresh_curve_rows()
        self._save_config()

    # ── Manual panel ──────────────────────────────────────

    def _build_manual_panel(self, parent):
        box = tk.Frame(parent, bg=BG)
        for i, fv in enumerate([self.fan0, self.fan1]):
            row = tk.Frame(box, bg=BG)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=f"Fan {i}:", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=7, anchor="w").pack(side=tk.LEFT)
            for val, (lname, lcol) in enumerate(zip(LEVEL_NAMES, LEVEL_COLORS)):
                tk.Radiobutton(row, text=lname, variable=fv, value=val,
                               bg=BG, fg=lcol, selectcolor=PANEL,
                               activebackground=BG,
                               font=("sans-serif", 11)).pack(side=tk.LEFT, padx=6)

        tk.Button(box, text="Apply", command=self._apply_manual,
                  bg=ACCENT, fg=BG2, relief="flat", padx=20, pady=4,
                  font=("sans-serif", 10, "bold"),
                  activebackground=FG).pack(pady=(10, 0), anchor="w")
        return box

    def _apply_manual(self):
        self.ctrl.set_fan(0, self.fan0.get())
        self.ctrl.set_fan(1, self.fan1.get())

    # ── mode switch ───────────────────────────────────────

    def _on_mode_change(self):
        self._auto_panel.pack_forget()
        self._manual_panel.pack_forget()
        if self.mode.get() == "auto":
            self._auto_panel.pack(fill=tk.BOTH, expand=True)
        else:
            self._manual_panel.pack(fill=tk.BOTH, expand=True)
        self._save_config()

    # ── module check ──────────────────────────────────────

    def _check_module(self):
        if not self.ctrl.i8k_ok and not self.ctrl.hwmon_path:
            if messagebox.askyesno(
                "Module Not Found",
                "dell-smm-hwmon is not loaded.\n\n"
                "Attempt to load it now?\n"
                "(runs: sudo modprobe dell-smm-hwmon force=1 ignore_dmi=1)\n\n"
                "If this fails, run: sudo bash setup-fan-control.sh"
            ):
                if self.ctrl.load_module():
                    self._status.set("Module loaded — " + self.ctrl.status_text())
                else:
                    self._status.set("Module load failed — run setup-fan-control.sh")
                    messagebox.showerror("Failed",
                        "Could not load dell-smm-hwmon.\n\n"
                        "Run the setup script first:\n"
                        "  sudo bash setup-fan-control.sh\n\n"
                        "Then restart this app.")
        else:
            self._status.set(self.ctrl.status_text())

    # ── auto control ──────────────────────────────────────

    def _apply_curve(self, cpu_temp):
        if not self.curve:
            return
        sorted_c = sorted(self.curve, key=lambda x: x[0])
        target = sorted_c[0][1]
        for thresh, lvl in sorted_c:
            if cpu_temp >= thresh:
                target = lvl
        self.ctrl.set_fan(0, target)
        self.ctrl.set_fan(1, target)

    # ── monitor thread ────────────────────────────────────

    def _monitor_loop(self):
        while self._running:
            try:
                temps = self.ctrl.get_temps()
                i8k   = self.ctrl.read_i8k()
                cpu_t = self.ctrl.get_cpu_temp()

                # Build temp display lines
                lines = []
                if i8k:
                    lines.append(f"CPU (i8k SMM):   {i8k[0]:5.1f} °C")
                priority = ("package", "pkg", "core", "coretemp", "cpu", "acpi")
                for key, val in temps.items():
                    lk = key.lower()
                    if any(p in lk for p in priority):
                        label = key.split("/")[-1][:18]
                        lines.append(f"{label:<18s} {val:5.1f} °C")
                if not lines:
                    for key, val in list(temps.items())[:3]:
                        label = key.split("/")[-1][:18]
                        lines.append(f"{label:<18s} {val:5.1f} °C")

                # Fan status
                if i8k:
                    f0n = LEVEL_NAMES[i8k[1]] if 0 <= i8k[1] <= 2 else "?"
                    f1n = LEVEL_NAMES[i8k[2]] if 0 <= i8k[2] <= 2 else "?"
                    f0t = f"{f0n}  ({i8k[3]} RPM)"
                    f1t = f"{f1n}  ({i8k[4]} RPM)"
                else:
                    f0t = f1t = "— (module not active)"

                # Apply auto mode
                if self.mode.get() == "auto" and cpu_t is not None:
                    self._apply_curve(cpu_t)

                self.root.after(0, self._update_ui, lines, f0t, f1t)

            except Exception:
                pass

            time.sleep(2)

    def _update_ui(self, lines, f0t, f1t):
        self._temp_text.config(state="normal")
        self._temp_text.delete("1.0", tk.END)
        self._temp_text.insert("1.0", "\n".join(lines[:5]))
        self._temp_text.config(state="disabled")
        self._fan_vars[0].set(f0t)
        self._fan_vars[1].set(f1t)

    def on_close(self):
        self._running = False
        self._save_config()
        self.root.destroy()


# ─────────────────────────────────────────────────────────────
#  Headless mode (for systemd service)
# ─────────────────────────────────────────────────────────────

class HeadlessController:
    def __init__(self):
        self.ctrl   = FanController()
        self.cfg    = self._load_config()
        self.curve  = self.cfg.get("curve", [list(x) for x in DEFAULT_CURVE])
        self._stop  = False
        signal.signal(signal.SIGTERM, self._handle_sig)
        signal.signal(signal.SIGINT,  self._handle_sig)

    def _load_config(self):
        try:
            if CONFIG_FILE.exists():
                return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
        return {}

    def _handle_sig(self, *_):
        print("Stopping fan control — returning fans to auto.")
        # Restore BIOS control by setting high (safe fallback)
        self.ctrl.set_fan(0, 2)
        self.ctrl.set_fan(1, 2)
        self._stop = True

    def _apply_curve(self, cpu_temp):
        sorted_c = sorted(self.curve, key=lambda x: x[0])
        target = sorted_c[0][1]
        for thresh, lvl in sorted_c:
            if cpu_temp >= thresh:
                target = lvl
        self.ctrl.set_fan(0, target)
        self.ctrl.set_fan(1, target)
        return target

    def run(self):
        if not self.ctrl.i8k_ok and not self.ctrl.hwmon_path:
            print("Loading dell-smm-hwmon…")
            if not self.ctrl.load_module():
                print("ERROR: Could not load dell-smm-hwmon. Run setup-fan-control.sh first.")
                sys.exit(1)

        print(f"dell-fan-control headless started. {self.ctrl.status_text()}")
        while not self._stop:
            cpu_t = self.ctrl.get_cpu_temp()
            if cpu_t is not None:
                lv = self._apply_curve(cpu_t)
                print(f"  CPU {cpu_t:.1f}°C → fan level {lv} ({LEVEL_NAMES[lv]})",
                      flush=True)
            time.sleep(3)


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def main():
    headless = "--headless" in sys.argv

    if os.geteuid() != 0:
        print("WARNING: Not running as root — fan writes may fail.")
        print("Re-run with:  sudo python3 dell-fan-control.py")

    if headless:
        HeadlessController().run()
    else:
        root = tk.Tk()
        app  = FanControlApp(root)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()


if __name__ == "__main__":
    main()
