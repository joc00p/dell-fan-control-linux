#!/usr/bin/env bash
# Dell Latitude 7490 Fan Control Setup — Fedora Linux
# Run once as root: sudo bash setup-fan-control.sh

set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GRN}✓${NC}  $*"; }
warn() { echo -e "  ${YLW}!${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC}  $*"; }

echo
echo "══════════════════════════════════════════"
echo "  Dell Latitude Fan Control — Fedora Setup"
echo "══════════════════════════════════════════"
echo

# ── 0. Root check ───────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "Must run as root.  Try:  sudo bash setup-fan-control.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Packages ─────────────────────────────────────────────
echo "[1/5] Installing packages…"
dnf install -y \
    lm_sensors \
    python3-tkinter \
    i2c-tools \
    acpid 2>/dev/null || true

# i8kutils may not be in Fedora repos — try, don't fail
dnf install -y i8kutils 2>/dev/null && ok "i8kutils installed" \
    || warn "i8kutils not found in repos (fan control will use hwmon sysfs instead)"

ok "Packages done"

# ── 2. Load module now ──────────────────────────────────────
echo
echo "[2/5] Loading dell-smm-hwmon kernel module…"

# Unload first if already loaded with wrong options
modprobe -r dell-smm-hwmon 2>/dev/null || true
sleep 0.3

if modprobe dell-smm-hwmon force=1 ignore_dmi=1; then
    sleep 0.5
    if [[ -e /proc/i8k ]]; then
        ok "Module loaded — /proc/i8k is available"
        echo "     i8k data: $(cat /proc/i8k)"
    else
        warn "Module loaded but /proc/i8k not found — hwmon sysfs will be used"
    fi
else
    err "modprobe failed. Your kernel may need a rebuild with CONFIG_I8K or CONFIG_SENSORS_DELL_SMM."
    warn "Check: lsmod | grep dell   and   dmesg | grep -i dell"
fi

# ── 3. Persist module options ───────────────────────────────
echo
echo "[3/5] Persisting module configuration…"

cat > /etc/modules-load.d/dell-smm-hwmon.conf << 'EOF'
dell-smm-hwmon
EOF

cat > /etc/modprobe.d/dell-smm-hwmon.conf << 'EOF'
options dell-smm-hwmon force=1 ignore_dmi=1
EOF

ok "Created /etc/modules-load.d/dell-smm-hwmon.conf"
ok "Created /etc/modprobe.d/dell-smm-hwmon.conf"

# ── 4. udev rules for non-root access ──────────────────────
echo
echo "[4/5] Setting up udev rules (wheel group access)…"

cat > /etc/udev/rules.d/99-dell-fan.rules << 'EOF'
# Dell SMM fan control — allow wheel group to read/write
KERNEL=="i8k",    GROUP="wheel", MODE="0660"
SUBSYSTEM=="hwmon", ATTR{name}=="dell_smm", \
    RUN+="/bin/sh -c 'chmod 660 %S%p/pwm* %S%p/pwm*_enable 2>/dev/null; \
                      chgrp wheel %S%p/pwm* %S%p/pwm*_enable 2>/dev/null; true'"
EOF

udevadm control --reload-rules
udevadm trigger
ok "udev rules installed"

# ── 5. Systemd service ──────────────────────────────────────
echo
echo "[5/5] Installing systemd service (headless/auto-control on boot)…"

PYTHON_BIN="$(which python3)"

cat > /etc/systemd/system/dell-fan-control.service << EOF
[Unit]
Description=Dell Latitude Fan Control (headless)
After=multi-user.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/dell-fan-control.py --headless
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
ok "Systemd service installed: dell-fan-control.service"
warn "Service NOT auto-enabled — see options below"

# ── Summary ─────────────────────────────────────────────────
echo
echo "══════════════════════════════════════════"
echo "  Setup complete!"
echo "══════════════════════════════════════════"
echo
echo "  Module status:"
if [[ -e /proc/i8k ]]; then
    ok "/proc/i8k available"
else
    warn "/proc/i8k not found (reboot may be needed)"
fi

HWMON_DELL=""
for h in /sys/class/hwmon/hwmon*; do
    n="$h/name"
    [[ -f "$n" ]] && grep -qi "dell\|smm" "$n" 2>/dev/null && HWMON_DELL="$h"
done
if [[ -n "$HWMON_DELL" ]]; then
    ok "hwmon sysfs: $HWMON_DELL"
else
    warn "Dell hwmon not found"
fi

echo
echo "  Run the GUI:"
echo "    sudo python3 ${SCRIPT_DIR}/dell-fan-control.py"
echo
echo "  Or enable headless auto-control on boot:"
echo "    sudo systemctl enable --now dell-fan-control"
echo
echo "  Monitor service logs:"
echo "    journalctl -fu dell-fan-control"
echo
echo "  Tip: run 'sensors-detect' (answer yes) to discover all temp sensors."
echo
