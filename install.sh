#!/usr/bin/env bash
# Installs wacom-panscroll as a systemd --user service.
# Safe to re-run: it just re-checks everything and reinstalls the unit.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="wacom-panscroll.service"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_PATH="$UNIT_DIR/$SERVICE_NAME"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

bold "wacom-panscroll installer"
echo "repo: $REPO_DIR"
echo

# 1. python3
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install it first."
PYTHON_BIN="$(command -v python3)"
ok "python3 found: $PYTHON_BIN"

# 2. python-evdev
if ! python3 -c "import evdev" >/dev/null 2>&1; then
    warn "python module 'evdev' not found."
    echo "  Arch/CachyOS/Manjaro:  sudo pacman -S python-evdev"
    echo "  Debian/Ubuntu:         sudo apt install python3-evdev"
    echo "  Fedora:                sudo dnf install python3-evdev"
    echo "  Any distro (pip):      pip install --user evdev"
    die "install it and re-run this script."
fi
ok "python-evdev is installed"

# 3. find candidate pen devices (stylus buttons + pressure = a real pen, not the pad)
CANDIDATES="$(python3 - <<'PYEOF'
import evdev
from evdev import ecodes
for path in evdev.list_devices():
    try:
        d = evdev.InputDevice(path)
    except OSError:
        continue
    if d.name.startswith("wacom-panscroll"):
        continue  # our own virtual clone, if the daemon is already running
    caps = d.capabilities()
    keys = caps.get(ecodes.EV_KEY, [])
    abss = [c for c, _ in caps.get(ecodes.EV_ABS, [])]
    if ecodes.BTN_STYLUS in keys and ecodes.ABS_PRESSURE in abss:
        print(d.name)
PYEOF
)"

if [ -z "$CANDIDATES" ]; then
    warn "No stylus-capable device auto-detected. Is the tablet plugged in?"
    echo "  You can still install and set WACOM_PANSCROLL_DEVICE manually later"
    echo "  (see README) by editing $UNIT_PATH after this script finishes."
    DEVICE_NAME=""
elif [ "$(echo "$CANDIDATES" | wc -l)" -eq 1 ]; then
    DEVICE_NAME="$CANDIDATES"
    ok "detected pen device: '$DEVICE_NAME'"
else
    bold "Multiple pen devices found:"
    echo "$CANDIDATES" | nl -w2 -s') '
    read -rp "Which one should wacom-panscroll control? (number): " N
    DEVICE_NAME="$(echo "$CANDIDATES" | sed -n "${N}p")"
    [ -n "$DEVICE_NAME" ] || die "invalid selection"
    ok "using: '$DEVICE_NAME'"
fi

# 4. group membership for /dev/input/*
if ! id -nG "$USER" | grep -qw input; then
    warn "user '$USER' is not in the 'input' group (needed to read the tablet)."
    read -rp "  Add it now? requires sudo [Y/n] " REPLY
    if [ "${REPLY:-Y}" != "n" ] && [ "${REPLY:-Y}" != "N" ]; then
        sudo usermod -aG input "$USER"
        warn "you must log out and back in for this to take effect, then re-run this script."
        exit 0
    else
        die "cannot continue without 'input' group access."
    fi
fi
ok "user is in the 'input' group"

# 5. /dev/uinput access
if [ ! -w /dev/uinput ]; then
    warn "/dev/uinput is not writable by '$USER'."
    RULE=/etc/udev/rules.d/99-wacom-panscroll-uinput.rules
    read -rp "  Install a udev rule granting group 'input' access to it? requires sudo [Y/n] " REPLY
    if [ "${REPLY:-Y}" != "n" ] && [ "${REPLY:-Y}" != "N" ]; then
        echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee "$RULE" >/dev/null
        sudo udevadm control --reload-rules
        sudo udevadm trigger --name-match=uinput
        if [ ! -w /dev/uinput ]; then
            warn "still not writable - you may need to unplug/replug the tablet or reboot."
        else
            ok "/dev/uinput is now writable"
        fi
    else
        die "cannot continue without /dev/uinput access."
    fi
else
    ok "/dev/uinput is writable"
fi

# 6. write the systemd unit (with the real repo path and python3 baked in)
mkdir -p "$UNIT_DIR"
{
    echo "[Unit]"
    echo "Description=Wacom stylus pan-scroll daemon (Wayland xsetwacom PanScroll replacement)"
    echo "PartOf=graphical-session.target"
    echo "After=graphical-session.target"
    echo
    echo "[Service]"
    echo "Type=simple"
    if [ -n "$DEVICE_NAME" ]; then
        # quoted: systemd's Environment= splits on whitespace otherwise, and
        # tablet device names almost always contain spaces
        echo "Environment=\"WACOM_PANSCROLL_DEVICE=$DEVICE_NAME\""
    fi
    echo "ExecStart=$PYTHON_BIN $REPO_DIR/wacom_panscroll.py"
    echo "Restart=on-failure"
    echo "RestartSec=5"
    echo
    echo "[Install]"
    echo "WantedBy=graphical-session.target"
} > "$UNIT_PATH"
ok "wrote $UNIT_PATH"

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
ok "service enabled and started"

echo
bold "Done. Check status with:"
echo "  systemctl --user status $SERVICE_NAME"
echo "  journalctl --user -u $SERVICE_NAME -f"
echo
echo "Tuning (sensitivity, acceleration, which button, ...): see README.md,"
echo "then add Environment= lines to $UNIT_PATH and run:"
echo "  systemctl --user daemon-reload && systemctl --user restart $SERVICE_NAME"
