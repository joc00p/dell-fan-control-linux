# Dell Fan Control Linux

Fan control GUI and headless daemon for **Dell Latitude laptops** (tested on Latitude 7490) running Linux — specifically Fedora 44 with a 7.x kernel, but should work on any modern distro with the `dell-smm-hwmon` kernel module.

---

## Features

- **Live temperature display** — reads from all hwmon sensors (coretemp, ACPI, Dell SMM)
- **Live fan RPM + level display** — Fan 0 and Fan 1 via `/proc/i8k`
- **Auto mode** — configurable temperature curve; each step maps a °C threshold to Off / Low / High; steps are editable, addable, and removable in the UI; settings persist across sessions
- **Manual mode** — set each fan independently, apply instantly
- **Headless / service mode** — runs without a display, ideal for boot-time auto-control via systemd
- Supports both `i8kctl` (i8kutils) and hwmon sysfs fan control backends

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3 | Usually pre-installed |
| `python3-tkinter` | GUI toolkit |
| `dell-smm-hwmon` kernel module | Built-in on most distros; loaded with `force=1 ignore_dmi=1` |
| `lm_sensors` | For full sensor enumeration |
| `i8kutils` *(optional)* | Provides `i8kctl`; falls back to hwmon sysfs if absent |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/joc00p/dell-fan-control-linux.git
cd dell-fan-control-linux
```

### 2. Run the setup script (once, as root)

```bash
sudo bash setup-fan-control.sh
```

This will:
1. Install `lm_sensors`, `python3-tkinter`, and `i8kutils` (if available in your repos)
2. Load `dell-smm-hwmon` with `force=1 ignore_dmi=1`
3. Persist module options in `/etc/modprobe.d/` (survives reboots)
4. Add udev rules so the `wheel` group can access fan files
5. Install a systemd service for headless auto-control on boot

### 3. Launch the GUI

```bash
sudo python3 dell-fan-control.py
```

> **Why sudo?** Writing to fan PWM files requires root. The udev rule installed by the setup script eventually makes this unnecessary for `wheel` group members after a reboot.

---

## GUI Overview

```
┌─────────────────────────────────────┐
│      Dell Latitude Fan Control      │
├─────────────────────────────────────┤
│ Temperatures                        │
│   CPU (i8k SMM):    52.0 °C         │
│   Package id 0:     54.0 °C         │
│   Core 0:           51.0 °C         │
├─────────────────────────────────────┤
│ Fan Status                          │
│   Fan 0:  Low  (2400 RPM)           │
│   Fan 1:  Low  (2200 RPM)           │
├─────────────────────────────────────┤
│ Control Mode  ● Auto   ○ Manual     │
├─────────────────────────────────────┤
│ Temperature Curve                   │
│  From  40°C → ○ Off ● Low ○ High    │
│    At  65°C → ○ Off ○ Low ● High    │
│  [ + Add Step ]                     │
└─────────────────────────────────────┘
```

### Auto Mode

Each row in the curve sets a temperature threshold and the fan level to apply at or above it. Below the first threshold, the first entry's level is used. The curve is applied every 2 seconds and settings are saved to `~/.config/dell-fan-control.json`.

### Manual Mode

Select Off / Low / High for each fan independently, then press **Apply**.

---

## Headless / Systemd Service

The setup script installs a systemd unit at `/etc/systemd/system/dell-fan-control.service`. It runs the app in `--headless` mode (no display needed) and applies the same temperature curve saved by the GUI.

**Enable and start on boot:**

```bash
sudo systemctl enable --now dell-fan-control
```

**Monitor logs:**

```bash
journalctl -fu dell-fan-control
```

**Stop and disable:**

```bash
sudo systemctl disable --now dell-fan-control
```

On SIGTERM the service safely sets both fans to High before exiting, handing control back to the BIOS.

---

## How It Works

The Dell Latitude 7490 exposes fan control through the **System Management Mode (SMM) BIOS interface**. The Linux kernel driver `dell-smm-hwmon` (formerly `i8k`) wraps this interface and exposes:

- `/proc/i8k` — readable file with CPU temp, fan levels, and fan RPMs
- `/sys/class/hwmon/hwmonX/` — standard hwmon sysfs for temperatures and PWM fan control

Because the 7490 is not on the driver's official DMI allowlist, the module must be loaded with `force=1 ignore_dmi=1`. The setup script handles this automatically and makes it persistent.

### Fan control priority

1. `i8kctl fan <num> <level>` — uses ioctl on `/dev/i8k` (cleanest interface, requires `i8kutils`)
2. hwmon sysfs `pwmN` write — direct kernel interface (fallback if i8kutils not installed)

---

## Troubleshooting

**`/proc/i8k` not found after setup**

```bash
# Check kernel module
lsmod | grep dell
dmesg | grep -i 'dell\|i8k\|smm'

# Try loading manually
sudo modprobe dell-smm-hwmon force=1 ignore_dmi=1
```

**No temperatures shown**

```bash
# Run sensor detection
sudo sensors-detect   # answer yes to all
sensors
```

**Fan writes silently fail**

The BIOS may be overriding manual fan control. Some Dell systems require `dell-bios-fan-control 0` to disable BIOS auto-management. This utility is not packaged for Fedora but can be compiled from source: [https://github.com/TomFreudenberg/dell-bios-fan-control](https://github.com/TomFreudenberg/dell-bios-fan-control)

**App won't start — tkinter missing**

```bash
sudo dnf install python3-tkinter
```

---

## Files

| File | Purpose |
|---|---|
| `dell-fan-control.py` | Main application (GUI + headless mode) |
| `setup-fan-control.sh` | One-time setup script (run as root) |

---

## License

MIT
