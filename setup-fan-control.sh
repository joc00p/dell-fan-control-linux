#!/usr/bin/env bash
# Dell Latitude Fan Control Setup — Fedora Linux
# Uses hwmon sysfs only. No i8k, no i8kutils.
# Run once as root: sudo bash setup-fan-control.sh

set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GRN}✓${NC}  $*"; }
warn() { echo -e "  ${YLW}!${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC}  $*"; }

echo
echo "══════════════════════════════════════════"
echo "  Dell Latitude Fan Control — Fedora Setup"
echo "  (hwmon sysfs — no i8k/i8kutils)"
echo "══════════════════════════════════════════"
echo

[[ $EUID -ne 0 ]] && { err "Must run as root: sudo bash setup-fan-control.sh"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Packages ─────────────────────────────────────────────
echo "[1/5] Installing packages…"
dnf install -y lm_sensors python3-tkinter 2>/dev/null
ok "Packages installed"

# ── 2. Load dell-smm-hwmon ──────────────────────────────────
echo
echo "[2/5] Loading dell-smm-hwmon kernel module…"

modprobe -r dell-smm-hwmon 2>/dev/null || true
sleep 0.3

if modprobe dell-smm-hwmon force=1 ignore_dmi=1; then
    sleep 0.5
    ok "Module loaded"
else
    err "modprobe dell-smm-hwmon failed."
    warn "Check: dmesg | grep -i dell"
    warn "Your kernel must be built with CONFIG_SENSORS_DELL_SMM=m or =y"
fi

# Show what hwmon devices appeared
echo
echo "  hwmon devices now present:"
for h in /sys/class/hwmon/hwmon*; do
    name=$(cat "$h/name" 2>/dev/null || echo "?")
    echo "    $h  [$name]"
    # Check for fan control
    if ls "$h"/pwm* &>/dev/null 2>&1; then
        ok "    Fan PWM control available: $(ls $h/pwm* 2>/dev/null | tr '\n' ' ')"
    fi
done

# ── 3. Persist module ───────────────────────────────────────
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

# ── 4. udev rule for wheel group access ─────────────────────
echo
echo "[4/5] Setting up udev rules…"

cat > /etc/udev/rules.d/99-dell-fan.rules << 'EOF'
# Allow wheel group to read/write Dell hwmon fan control files
SUBSYSTEM=="hwmon", ATTR{name}=="dell_smm", \
    RUN+="/bin/sh -c 'for f in %S%p/pwm* %S%p/fan*_input; do \
        [ -e \"$f\" ] && chmod 660 \"$f\" && chgrp wheel \"$f\"; done; true'"
EOF

udevadm control --reload-rules
udevadm trigger
ok "udev rules installed"

# ── 5. systemd service ──────────────────────────────────────
echo
echo "[5/5] Installing systemd service…"
PYTHON3="$(which python3)"

cat > /etc/systemd/system/dell-fan-control.service << EOF
[Unit]
Description=Dell Fan Control (hwmon headless daemon)
After=multi-user.target

[Service]
Type=simple
ExecStart=${PYTHON3} ${SCRIPT_DIR}/dell-fan-control.py --headless
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
ok "Service installed (not yet enabled)"

# ── probe ───────────────────────────────────────────────────
echo
echo "══ hwmon probe output ════════════════════"
python3 "${SCRIPT_DIR}/dell-fan-control.py" --probe 2>/dev/null || true

# ── Summary ─────────────────────────────────────────────────
echo
echo "══════════════════════════════════════════"
echo "  Next steps"
echo "══════════════════════════════════════════"
echo
echo "  Run the GUI:"
echo "    sudo python3 ${SCRIPT_DIR}/dell-fan-control.py"
echo
echo "  See what hwmon exposes (share this output if things don't work):"
echo "    sudo python3 ${SCRIPT_DIR}/dell-fan-control.py --probe"
echo
echo "  Enable headless auto-control on boot:"
echo "    sudo systemctl enable --now dell-fan-control"
echo "    journalctl -fu dell-fan-control"
echo
echo "  If no PWM fan control is found, consider nbfc-linux as an alternative:"
echo "    https://github.com/nbfc-linux/nbfc-linux"
echo "    sudo dnf copr enable smoldyn80/nbfc-linux"
echo "    sudo dnf install nbfc-linux"
echo
