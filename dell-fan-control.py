#!/usr/bin/env python3
"""
Dell Latitude Fan Control — hwmon sysfs backend
/sys/class/hwmon only. No i8k, no i8kutils, no /proc/i8k.

Usage:
  sudo python3 dell-fan-control.py            # GUI
  sudo python3 dell-fan-control.py --headless # daemon / systemd
  sudo python3 dell-fan-control.py --probe    # dump hwmon info and exit
"""

import tkinter as tk
import subprocess, os, glob, threading, time, json, sys, signal
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "dell-fan-control.json"

DEFAULT_CURVE = [[45, 0], [60, 1], [75, 2]]   # [°C threshold, fan level]

LEVEL_NAMES  = ["Off", "Low", "High"]
LEVEL_COLORS = ["#89dceb", "#a6e3a1", "#f38ba8"]
# Maps fan level 0/1/2 to PWM value 0-255
LEVEL_PWM    = {0: 0, 1: 90, 2: 255}

BG, BG2, PANEL = "#1e1e2e", "#181825", "#313244"
FG, ACCENT, SUBTEXT, WARN = "#cdd6f4", "#89b4fa", "#a6adc8", "#fab387"


# ─────────────────────────────────────────────────────────────
#  hwmon helpers
# ─────────────────────────────────────────────────────────────

def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None

def _write(path, value):
    try:
        Path(path).write_text(str(value))
        return True
    except PermissionError:
        r = subprocess.run(
            ["sudo", "sh", "-c", f"echo {value} > {path}"],
            capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


class HwmonTemp:
    def __init__(self, hwmon_dir, n):
        base = Path(hwmon_dir) / f"temp{n}"
        self.input_path = base.parent / f"temp{n}_input"
        label_path      = base.parent / f"temp{n}_label"
        self.hwmon_name = _read(Path(hwmon_dir) / "name") or Path(hwmon_dir).name
        raw_label       = _read(label_path)
        self.label      = raw_label if raw_label else f"{self.hwmon_name}/temp{n}"

    @property
    def celsius(self):
        raw = _read(self.input_path)
        if raw is None:
            return None
        try:
            v = int(raw) / 1000
            return v if 0 < v < 120 else None
        except ValueError:
            return None


class HwmonFan:
    def __init__(self, hwmon_dir, n):
        d = Path(hwmon_dir)
        self.input_path  = d / f"fan{n}_input"
        self.pwm_path    = d / f"pwm{n}"
        self.enable_path = d / f"pwm{n}_enable"
        self.hwmon_name  = _read(d / "name") or d.name
        self.n           = n
        self.label       = f"{self.hwmon_name}/fan{n}"

    @property
    def rpm(self):
        raw = _read(self.input_path)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    @property
    def can_control(self):
        return self.pwm_path.exists()

    def set_manual(self, pwm_value):
        """Set fan to manual control with given PWM (0-255). Returns True on success."""
        if not self.can_control:
            return False
        if self.enable_path.exists():
            _write(self.enable_path, "1")
        return _write(self.pwm_path, str(pwm_value))

    def set_auto(self):
        """Return fan to automatic (BIOS) control."""
        if self.enable_path.exists():
            _write(self.enable_path, "2")


# ─────────────────────────────────────────────────────────────
#  Controller
# ─────────────────────────────────────────────────────────────

class FanController:
    def __init__(self):
        self.refresh()

    def refresh(self):
        self.temps = self._discover_temps()
        self.fans  = self._discover_fans()

    def _discover_temps(self):
        temps = []
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            for inp in sorted(glob.glob(f"{hwmon}/temp*_input")):
                n = inp.split("temp")[1].split("_")[0]
                t = HwmonTemp(hwmon, n)
                if t.celsius is not None:
                    temps.append(t)
        return temps

    def _discover_fans(self):
        fans = []
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            for inp in sorted(glob.glob(f"{hwmon}/fan*_input")):
                n = inp.split("fan")[1].split("_")[0]
                fans.append(HwmonFan(hwmon, n))
        return fans

    def controllable_fans(self):
        return [f for f in self.fans if f.can_control]

    def get_cpu_temp(self):
        """Highest temp from coretemp or acpitz sensors."""
        priority_keys = ("package", "pkg", "core", "coretemp", "cpu", "acpitz")
        candidates = []
        for t in self.temps:
            lbl = t.label.lower()
            if any(k in lbl for k in priority_keys):
                c = t.celsius
                if c is not None:
                    candidates.append(c)
        return max(candidates) if candidates else None

    def set_all_fans(self, level):
        """Set all controllable fans to level 0/1/2."""
        pwm = LEVEL_PWM[level]
        ok = False
        for f in self.controllable_fans():
            if f.set_manual(pwm):
                ok = True
        return ok

    def restore_auto(self):
        for f in self.controllable_fans():
            f.set_auto()

    def any_available(self):
        return bool(self.temps or self.fans)

    def status_text(self):
        ctrl = self.controllable_fans()
        return (f"{len(self.temps)} temp sensors, "
                f"{len(self.fans)} fans "
                f"({len(ctrl)} controllable)")

    def probe_report(self):
        lines = ["── hwmon probe ──────────────────────────"]
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read(f"{hwmon}/name") or "?"
            lines.append(f"\n{hwmon}  [{name}]")
            for f in sorted(os.listdir(hwmon)):
                if f.endswith(("_input", "_enable")) or f in ("name",):
                    val = _read(f"{hwmon}/{f}")
                    lines.append(f"  {f:<28s} = {val}")
                elif f.startswith("pwm") and not f.endswith(("_enable", "_mode")):
                    val = _read(f"{hwmon}/{f}")
                    lines.append(f"  {f:<28s} = {val}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────────────────────

class FanControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Dell Fan Control")
        self.root.geometry("480x580")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        self.ctrl  = FanController()
        self.cfg   = self._load_config()
        self.mode  = tk.StringVar(value=self.cfg.get("mode", "auto"))
        self.curve = self.cfg.get("curve", [list(r) for r in DEFAULT_CURVE])
        # Per-fan manual level vars (created dynamically after fan discovery)
        self._fan_level_vars = []
        self._running = True

        self._build_ui()
        self._check_fans()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    # ── config ────────────────────────────────────────────

    def _load_config(self):
        try:
            return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        except Exception:
            return {}

    def _save_config(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(
            {"mode": self.mode.get(), "curve": self.curve}, indent=2))

    # ── UI ───────────────────────────────────────────────

    def _section(self, parent, title):
        f = tk.LabelFrame(parent, text=f"  {title}  ",
                          bg=BG, fg=ACCENT, font=("sans-serif", 10),
                          padx=10, pady=6, relief="groove")
        f.pack(fill=tk.X, pady=(0, 8))
        return f

    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=BG, padx=14, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        tk.Label(wrap, text="Dell Fan Control",
                 bg=BG, fg=ACCENT, font=("sans-serif", 14, "bold")).pack(pady=(0, 10))

        # Temperatures
        temp_box = self._section(wrap, "Temperatures")
        self._temp_text = tk.Text(temp_box, height=5, width=50, bg=BG2, fg=FG,
                                  font=("monospace", 10), state="disabled",
                                  relief="flat", padx=4, pady=2)
        self._temp_text.pack(fill=tk.X)

        # Fans
        fan_box = self._section(wrap, "Fans")
        self._fan_status_frame = tk.Frame(fan_box, bg=BG)
        self._fan_status_frame.pack(fill=tk.X)
        self._rebuild_fan_status()

        # Mode
        mode_box = self._section(wrap, "Control Mode")
        rb_row = tk.Frame(mode_box, bg=BG)
        rb_row.pack(anchor="w")
        for text, val in [("Auto (temp curve)", "auto"), ("Manual", "manual")]:
            tk.Radiobutton(rb_row, text=text, variable=self.mode, value=val,
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           font=("sans-serif", 10),
                           command=self._on_mode_change).pack(side=tk.LEFT, padx=6)

        self._swap = tk.Frame(wrap, bg=BG)
        self._swap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._auto_panel   = self._build_auto_panel(self._swap)
        self._manual_panel = self._build_manual_panel(self._swap)
        self._on_mode_change()

        self._status = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self._status, bg=PANEL, fg=SUBTEXT,
                 font=("sans-serif", 9), anchor="w", padx=8).pack(
                     side=tk.BOTTOM, fill=tk.X)

    def _rebuild_fan_status(self):
        for w in self._fan_status_frame.winfo_children():
            w.destroy()
        self._fan_rpm_vars = []
        fans = self.ctrl.fans or [None]
        for i, fan in enumerate(fans):
            row = tk.Frame(self._fan_status_frame, bg=BG)
            row.pack(fill=tk.X, pady=1)
            lbl = fan.label if fan else "No fans found"
            tk.Label(row, text=f"  {lbl}:", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=22, anchor="w").pack(side=tk.LEFT)
            if fan:
                v = tk.StringVar(value="— RPM")
                ctrl_marker = " [ctrl]" if fan.can_control else ""
                tk.Label(row, textvariable=v, bg=BG, fg=WARN,
                         font=("monospace", 10)).pack(side=tk.LEFT)
                tk.Label(row, text=ctrl_marker, bg=BG, fg=ACCENT,
                         font=("monospace", 9)).pack(side=tk.LEFT)
                self._fan_rpm_vars.append(v)

    # ── Auto panel ────────────────────────────────────────

    def _build_auto_panel(self, parent):
        box = tk.Frame(parent, bg=BG)
        tk.Label(box, text="At or above each threshold → set fan level:",
                 bg=BG, fg=SUBTEXT, font=("sans-serif", 9)).pack(anchor="w", pady=(0, 4))
        self._curve_frame = tk.Frame(box, bg=BG)
        self._curve_frame.pack(fill=tk.X)
        self._refresh_curve()
        tk.Button(box, text="+ Add Step", command=self._add_step,
                  bg=PANEL, fg=FG, relief="flat", padx=8, pady=2,
                  font=("sans-serif", 9)).pack(anchor="w", pady=(6, 0))
        return box

    def _refresh_curve(self):
        for w in self._curve_frame.winfo_children():
            w.destroy()
        for i, entry in enumerate(self.curve):
            temp, level = entry[0], entry[1]
            row = tk.Frame(self._curve_frame, bg=BG)
            row.pack(fill=tk.X, pady=1)
            prefix = "Below" if i == 0 else "   At"
            tk.Label(row, text=prefix, bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=6, anchor="e").pack(side=tk.LEFT)
            tv = tk.StringVar(value=str(temp))
            tk.Entry(row, textvariable=tv, width=4, bg=PANEL, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=("monospace", 10)).pack(side=tk.LEFT, padx=3)
            tk.Label(row, text="°C →", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10)).pack(side=tk.LEFT, padx=(0, 4))
            lv = tk.IntVar(value=level)
            for val, (name, col) in enumerate(zip(LEVEL_NAMES, LEVEL_COLORS)):
                tk.Radiobutton(row, text=name, variable=lv, value=val,
                               bg=BG, fg=col, selectcolor=PANEL, activebackground=BG,
                               font=("sans-serif", 10)).pack(side=tk.LEFT, padx=2)

            def _make_upd(idx, t_, l_):
                def _upd(*_):
                    try:
                        self.curve[idx] = [int(t_.get()), l_.get()]
                        self._save_config()
                    except ValueError:
                        pass
                return _upd
            upd = _make_upd(i, tv, lv)
            tv.trace_add("write", upd)
            lv.trace_add("write", upd)

            if i > 0:
                def _make_del(idx):
                    def _del():
                        self.curve.pop(idx)
                        self._refresh_curve()
                        self._save_config()
                    return _del
                tk.Button(row, text="✕", command=_make_del(i),
                          bg=BG, fg="#f38ba8", relief="flat", padx=4,
                          font=("sans-serif", 9)).pack(side=tk.RIGHT)

    def _add_step(self):
        last = self.curve[-1][0] if self.curve else 60
        self.curve.append([last + 10, 2])
        self._refresh_curve()
        self._save_config()

    # ── Manual panel ──────────────────────────────────────

    def _build_manual_panel(self, parent):
        box = tk.Frame(parent, bg=BG)
        ctrl_fans = self.ctrl.controllable_fans()

        if not ctrl_fans:
            tk.Label(box,
                     text="No controllable fans found.\nRun --probe to see available hwmon nodes.",
                     bg=BG, fg=WARN, font=("sans-serif", 10),
                     justify="left").pack(anchor="w", pady=4)
            return box

        self._fan_level_vars = []
        for fan in ctrl_fans:
            row = tk.Frame(box, bg=BG)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=f"{fan.label}:", bg=BG, fg=SUBTEXT,
                     font=("sans-serif", 10), width=20, anchor="w").pack(side=tk.LEFT)
            lv = tk.IntVar(value=1)
            self._fan_level_vars.append((fan, lv))
            for val, (name, col) in enumerate(zip(LEVEL_NAMES, LEVEL_COLORS)):
                tk.Radiobutton(row, text=name, variable=lv, value=val,
                               bg=BG, fg=col, selectcolor=PANEL, activebackground=BG,
                               font=("sans-serif", 10)).pack(side=tk.LEFT, padx=5)

        tk.Button(box, text="Apply", command=self._apply_manual,
                  bg=ACCENT, fg=BG2, relief="flat", padx=20, pady=4,
                  font=("sans-serif", 10, "bold")).pack(pady=(10, 0), anchor="w")
        return box

    def _apply_manual(self):
        for fan, lv in self._fan_level_vars:
            fan.set_manual(LEVEL_PWM[lv.get()])

    # ── mode switch ───────────────────────────────────────

    def _on_mode_change(self):
        self._auto_panel.pack_forget()
        self._manual_panel.pack_forget()
        if self.mode.get() == "auto":
            self._auto_panel.pack(fill=tk.BOTH, expand=True)
        else:
            self._manual_panel.pack(fill=tk.BOTH, expand=True)
        self._save_config()

    # ── startup check ─────────────────────────────────────

    def _check_fans(self):
        if not self.ctrl.any_available():
            from tkinter import messagebox
            messagebox.showwarning(
                "No hwmon devices found",
                "No temperature or fan sensors found under /sys/class/hwmon/.\n\n"
                "Try loading the module:\n"
                "  sudo modprobe dell-smm-hwmon force=1 ignore_dmi=1\n\n"
                "Then run:  sudo python3 dell-fan-control.py --probe\n"
                "to see what's available, and share the output for help.")
            self._status.set("No sensors found — see warning")
        else:
            ctrl = len(self.ctrl.controllable_fans())
            self._status.set(self.ctrl.status_text())
            if ctrl == 0:
                self._status.set(
                    self.ctrl.status_text() + " — no PWM control; run --probe")

    # ── auto control ──────────────────────────────────────

    def _apply_curve(self, cpu_temp):
        if not self.curve:
            return
        sorted_c = sorted(self.curve, key=lambda x: x[0])
        target = sorted_c[0][1]
        for thresh, lvl in sorted_c:
            if cpu_temp >= thresh:
                target = lvl
        self.ctrl.set_all_fans(target)

    # ── monitor thread ────────────────────────────────────

    def _monitor_loop(self):
        while self._running:
            try:
                self.ctrl.refresh()
                cpu_t = self.ctrl.get_cpu_temp()

                # Temperature lines — priority sort
                priority = ("package", "pkg", "core", "coretemp", "acpitz")
                shown, lines = set(), []
                for key in priority:
                    for t in self.ctrl.temps:
                        if key in t.label.lower() and t.label not in shown:
                            c = t.celsius
                            if c is not None:
                                lines.append(f"{t.label[:22]:<22s}  {c:5.1f} °C")
                                shown.add(t.label)
                # Fill remaining slots
                for t in self.ctrl.temps:
                    if t.label not in shown and len(lines) < 6:
                        c = t.celsius
                        if c is not None:
                            lines.append(f"{t.label[:22]:<22s}  {c:5.1f} °C")

                # Fan RPMs
                rpm_texts = []
                for f in self.ctrl.fans:
                    rpm = f.rpm
                    rpm_texts.append(f"{rpm} RPM" if rpm is not None else "— RPM")

                # Auto control
                if self.mode.get() == "auto" and cpu_t is not None:
                    self._apply_curve(cpu_t)

                self.root.after(0, self._update_ui, lines, rpm_texts)

            except Exception:
                pass
            time.sleep(2)

    def _update_ui(self, temp_lines, rpm_texts):
        self._temp_text.config(state="normal")
        self._temp_text.delete("1.0", tk.END)
        self._temp_text.insert("1.0", "\n".join(temp_lines[:5]))
        self._temp_text.config(state="disabled")

        for var, text in zip(self._fan_rpm_vars, rpm_texts):
            var.set(text)

    def on_close(self):
        self._running = False
        self._save_config()
        self.root.destroy()


# ─────────────────────────────────────────────────────────────
#  Headless daemon
# ─────────────────────────────────────────────────────────────

class HeadlessDaemon:
    def __init__(self):
        self.ctrl  = FanController()
        cfg        = self._load_config()
        self.curve = cfg.get("curve", [list(r) for r in DEFAULT_CURVE])
        self._stop = False
        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT,  self._sig)

    def _load_config(self):
        try:
            return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        except Exception:
            return {}

    def _sig(self, *_):
        print("Stopping — restoring fan auto control.")
        self.ctrl.restore_auto()
        self._stop = True

    def _apply_curve(self, cpu_temp):
        sorted_c = sorted(self.curve, key=lambda x: x[0])
        target = sorted_c[0][1]
        for thresh, lvl in sorted_c:
            if cpu_temp >= thresh:
                target = lvl
        self.ctrl.set_all_fans(target)
        return target

    def run(self):
        self.ctrl.refresh()
        if not self.ctrl.any_available():
            print("ERROR: No hwmon sensors found.")
            print("Run: sudo modprobe dell-smm-hwmon force=1 ignore_dmi=1")
            print("Then: sudo python3 dell-fan-control.py --probe")
            sys.exit(1)
        if not self.ctrl.controllable_fans():
            print("WARNING: No controllable fans (no pwm* files). "
                  "Running in monitor-only mode.")

        print(f"Started. {self.ctrl.status_text()}")

        while not self._stop:
            self.ctrl.refresh()
            cpu_t = self.ctrl.get_cpu_temp()
            if cpu_t is not None and self.ctrl.controllable_fans():
                lv = self._apply_curve(cpu_t)
                rpms = [f.rpm for f in self.ctrl.fans]
                rpm_str = "  ".join(f"{r} RPM" for r in rpms if r is not None)
                print(f"  {cpu_t:.1f}°C → {LEVEL_NAMES[lv]}   {rpm_str}", flush=True)
            time.sleep(3)


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def main():
    if os.geteuid() != 0:
        print("WARNING: not root — fan writes may be silently ignored.")
        print("Re-run: sudo python3 dell-fan-control.py")

    if "--probe" in sys.argv:
        ctrl = FanController()
        print(ctrl.probe_report())
        return

    if "--headless" in sys.argv:
        HeadlessDaemon().run()
        return

    root = tk.Tk()
    app  = FanControlApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
