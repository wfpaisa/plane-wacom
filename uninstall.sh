#!/usr/bin/env bash
# Stops and removes the wacom-panscroll systemd --user service.
# Does NOT touch group membership or the /dev/uinput udev rule (harmless to
# leave in place; remove /etc/udev/rules.d/99-wacom-panscroll-uinput.rules
# yourself with sudo if you want them gone too).
set -euo pipefail

SERVICE_NAME="wacom-panscroll.service"
UNIT_PATH="$HOME/.config/systemd/user/$SERVICE_NAME"

systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$UNIT_PATH"
systemctl --user daemon-reload

echo "wacom-panscroll service removed. The real tablet is back to its default behavior."
