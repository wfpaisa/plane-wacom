# wacom-panscroll

Pan-scroll for a Wacom stylus button on **Wayland/GNOME** — the thing
`xsetwacom ... Button N "pan"` gave you on X11, which neither libinput nor
mutter implement natively (yet).

🇪🇸 [Leer en español](README.es.md)

## Install & run

```bash
git clone https://github.com/<you>/wacom-panscroll.git
cd wacom-panscroll
./install.sh
```

`install.sh` auto-detects your pen, checks/fixes the permissions it needs
(`input` group, `/dev/uinput` — may ask for `sudo`), and installs+starts a
`systemd --user` service. Re-run it anytime, it's idempotent.

Hold the configured stylus button and drag the pen → smooth scroll instead of
moving the cursor. Release → back to normal.

```bash
systemctl --user status wacom-panscroll.service    # check it's running
journalctl --user -u wacom-panscroll.service -f     # watch logs
./uninstall.sh                                       # remove it
```

**Manual install** (no script): copy
[`wacom-panscroll.service.example`](wacom-panscroll.service.example) to
`~/.config/systemd/user/wacom-panscroll.service`, edit the paths in it, then
`systemctl --user daemon-reload && systemctl --user enable --now wacom-panscroll.service`.

### Requirements

Wayland session (tested on GNOME/mutter) · Python 3 +
[`evdev`](https://python-evdev.readthedocs.io/) · a Wacom pen reporting
`BTN_STYLUS`/`BTN_STYLUS2` + `ABS_PRESSURE` · read access to
`/dev/input/event*` and write access to `/dev/uinput` (`install.sh` checks
both).

## Configuration

Set these as `Environment=` lines in the systemd unit
(`~/.config/systemd/user/wacom-panscroll.service`), then:

```bash
systemctl --user daemon-reload && systemctl --user restart wacom-panscroll.service
```

| Variable | Default | Meaning |
|---|---|---|
| `WACOM_PANSCROLL_DEVICE` | `Wacom Intuos S Pen` | Exact device name to grab (`install.sh` sets this for you) |
| `WACOM_PANSCROLL_BUTTON` | `stylus2` | Trigger button: `stylus` (lower) or `stylus2` (upper) |
| `WACOM_PANSCROLL_SENSITIVITY` | `320` | Tablet units of motion per scroll notch. Lower = faster |
| `WACOM_PANSCROLL_ACCEL` | `1.6` | Speed-based acceleration exponent. `1.0` = linear, no boost |
| `WACOM_PANSCROLL_ACCEL_MAX` | `4.0` | Cap on the acceleration multiplier |
| `WACOM_PANSCROLL_INVERT_Y` | `0` | `1` to invert vertical scroll direction |
| `WACOM_PANSCROLL_HSCROLL` | `1` | `0` to disable horizontal scroll (vertical only) |
| `WACOM_PANSCROLL_EDGE_SYNC` | `1` | `0` to disable syncing the cursor near screen edges (see below) |
| `WACOM_PANSCROLL_EDGE_ZONE` | `0.05` | Fraction of the tablet's range, from each edge, counted as "near an edge" |

## How it works

This exists because Wayland has no equivalent to X11's pan-button mapping:
[gnome-control-center#1645](https://gitlab.gnome.org/GNOME/gnome-control-center/-/issues/1645)
and [gtk#5570](https://gitlab.gnome.org/GNOME/gtk/-/issues/5570) are
long-open feature requests, the
[`tablet-v2`](https://wayland.app/protocols/tablet-v2) protocol has no "pan"
concept for stylus buttons, and a `libinput` Lua-plugin prototype
[hit a dead end](https://discourse.gnome.org/t/scrolling-emulation-with-wacom-tablets-stylus-in-wayland/33611).

So this daemon does it in userspace, the same way `xf86-input-wacom` used to
do it in the X11 driver: it grabs the pen exclusively and re-emits events
through two virtual (`uinput`) devices — a tablet clone (drawing/cursor
unaffected) and a small "mouse"-class pointer used only for scroll, because a
device tagged as a tablet has its wheel events silently dropped by libinput's
tablet-tool code path. While the trigger button is held, pen motion becomes
velocity-sensitive smooth scroll instead of moving the cursor — matching the
modern (2022+) `xf86-input-wacom` driver's own behavior, verified against its
source.

That second pointer's position is also kept synced to the pen near screen
edges, because GNOME's hot-corner / dock-reveal logic only reacts to
"mouse"-class pointer motion — never to the pen directly, even though both
move the same visible cursor.

**Known cosmetic limitation:** that second pointer briefly shows its own
cursor icon during panning and near screen edges. Wayland has no per-device
"hide cursor" option, so this can't be fully avoided from a userspace input
daemon.

The full story — every dead end, the crash one wrong approach caused, and
why — is documented in the comments at the top of
[`wacom_panscroll.py`](wacom_panscroll.py).

## Alternatives considered

- **[OpenTabletDriver](https://opentabletdriver.net/)** — full cross-platform
  tablet driver replacement. Heavier if all you want is pan-scroll.
- **[input-remapper](https://github.com/sezanzeb/input-remapper)** — good for
  discrete button→key remapping, not continuous motion→scroll.
- **Wait for native support** — track the GNOME issues linked above.

## License

MIT — see [LICENSE](LICENSE).
