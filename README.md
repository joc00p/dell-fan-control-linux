# Dell Fan Control Linux

Fan control GUI and headless daemon for **Dell Latitude laptops** on Linux (Fedora 44, kernel 7.x).

Uses the **hwmon sysfs interface** (`/sys/class/hwmon/`) exclusively — no i8k, no i8kutils, no `/proc/i8k`. Those are defunct. The `dell-smm-hwmon` kernel module is still used, but only for its modern hwmon interface.

---

## How it works

The `dell-smm-hwmon` kernel module exposes fan and temperature sensors via the standard Linux hwmon sysfs tree:

```
/sys/class/hwmon/hwmonX/
  name          → "dell_smm" (or "coretemp", "acpitz", etc.)
  temp*_input   → temperature in millidegrees C (read)
  temp*_label   → sensor name (read)
  fan*_input    → fan speed in RPM (read)
  pwm*          → fan PWM 0–255 (write to control)
  pwm*_enable   → 1 = manual control, 2 = auto/BIOS
```

The app reads all hwmon nodes for temperatures and fan RPMs, and writes to `pwm*` files for fan control. No proprietary interfaces, no deprecated utilities.

---

## Features

- **Live temperature display** — reads from all hwmon sensors, prioritizes CPU core/package temps
- **Live fan RPM display** — reads `fan*_input` from all hwmon nodes
- **Auto mode** — configurable temperature curve; applies every 2 seconds; persists to `~/.config/dell-fan-control.json`
- **Manual mode** — set each controllable fan to Off / Low / High and apply
- **`--probe` mode** — dumps all hwmon nodes and their values; use this to diagnose your specific hardware
- **`--headless` mode** — no display needed; designed for systemd service

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3 | Pre-installed on Fedora |
| `python3-tkinter` | `sudo dnf install python3-tkinter` |
| `lm_sensors` | `sudo dnf install lm_sensors` |
| `dell-smm-hwmon` kernel module | Built into Fedora kernels; loaded by setup script |

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/joc00p/dell-fan-control-linux.git
cd dell-fan-control-linux
```

### 2. Setup (once, as root)

```bash
sudo bash setup-fan-control.sh
```

This loads `dell-smm-hwmon`, persists it across reboots, sets up a udev rule for `wheel` group access to fan files, installs the systemd service, and runs `--probe` so you can see exactly what your hardware exposes.

### 3. Run

```bash
sudo python3 dell-fan-control.py
```

---

## Diagnosing your hardware

Before the GUI is useful, check what your machine actually exposes:

```bash
sudo python3 dell-fan-control.py --probe
```

Share the output if fan control isn't working — it shows every hwmon node, every sensor file, and every value. That's the ground truth for what the kernel driver exposes on your specific hardware.

You can also check manually:

```bash
# What modules are loaded
lsmod | grep dell

# What hwmon nodes exist
ls /sys/class/hwmon/
cat /sys/class/hwmon/hwmon*/name

# Does fan PWM control exist?
ls /sys/class/hwmon/hwmon*/pwm* 2>/dev/null || echo "No PWM files found"

# What do the sensors show?
sensors
```

---

## GUI overview

```
┌──────────────────────────────────────────┐
│           Dell Fan Control               │
├──────────────────────────────────────────┤
│ Temperatures                             │
│   Package id 0          54.0 °C          │
│   Core 0                51.0 °C          │
│   Core 1                52.0 °C          │
│   acpitz/temp1          48.0 °C          │
├──────────────────────────────────────────┤
│ Fans                                     │
│   dell_smm/fan1: [ctrl]  2400 RPM        │
│   dell_smm/fan2: [ctrl]  2200 RPM        │
├──────────────────────────────────────────┤
│ Control Mode  ● Auto   ○ Manual          │
├──────────────────────────────────────────┤
│ Temperature Curve                        │
│  Below  45°C → ● Off ○ Low ○ High        │
│     At  60°C → ○ Off ● Low ○ High        │
│     At  75°C → ○ Off ○ Low ● High        │
│  [ + Add Step ]                          │
└──────────────────────────────────────────┘
```

Fans marked `[ctrl]` have writable `pwm*` files and can be controlled. Fans without it are read-only (RPM display only).

### Auto mode

Reads the highest CPU temp every 2 seconds and applies the first matching threshold from the curve (bottom wins). Settings save automatically.

### Manual mode

Applies immediately per-fan when you click Apply. Off = PWM 0, Low ≈ 35%, High = 100%.

---

## Headless / systemd service

The setup script installs `/etc/systemd/system/dell-fan-control.service`. It runs `--headless`, applying the same curve saved by the GUI. On SIGTERM it restores BIOS auto-control before exiting.

```bash
# Enable and start
sudo systemctl enable --now dell-fan-control

# Logs
journalctl -fu dell-fan-control

# Stop
sudo systemctl disable --now dell-fan-control
```

---

## If no fan control is found

Some Dell models do not expose writable `pwm*` files through `dell-smm-hwmon`. In that case, [`nbfc-linux`](https://github.com/nbfc-linux/nbfc-linux) is the recommended alternative — it controls fans via direct EC (Embedded Controller) access and has profiles for many Dell Latitude models.

```bash
sudo dnf copr enable smoldyn80/nbfc-linux
sudo dnf install nbfc-linux
nbfc config --recommend
```

---

## Files

| File | Purpose |
|---|---|
| `dell-fan-control.py` | Main app — GUI, headless daemon, probe mode |
| `setup-fan-control.sh` | One-time setup script (run as root) |

---

## License

MIT
