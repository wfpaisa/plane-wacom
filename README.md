# wacom-panscroll

Pan-scroll for a Wacom stylus button on **Wayland/GNOME** — the thing
`xsetwacom ... Button N "pan"` gave you on X11, which neither libinput nor
mutter implement natively (yet).

🇪🇸 [Leer en español](README.es.md)

Hold a stylus button and drag the pen → smooth, speed-sensitive scroll,
instead of moving the cursor. Release the button → back to normal.

## Why this exists

On X11, the `xf86-input-wacom` driver let you do:

```bash
xsetwacom --set "Wacom Intuos S Pen stylus" Button 3 "pan"
```

and a side button on your pen became a pan/scroll trigger. On Wayland there
is no equivalent:

- [gnome-control-center#1645](https://gitlab.gnome.org/GNOME/gnome-control-center/-/issues/1645)
  and [gtk#5570](https://gitlab.gnome.org/GNOME/gtk/-/issues/5570) have been
  open feature requests for years with no implementation.
- The [`tablet-v2`](https://wayland.app/protocols/tablet-v2) Wayland protocol
  has no "pan" concept for stylus buttons at all — it leaves button-action
  mapping entirely up to the compositor, and mutter doesn't implement one.
- `libinput` 1.30+ added a Lua plugin system that could theoretically do
  this, but (a) the compositor has to explicitly opt in to loading plugins,
  which mutter doesn't, and (b) even a working prototype patch reported on
  [GNOME Discourse](https://discourse.gnome.org/t/scrolling-emulation-with-wacom-tablets-stylus-in-wayland/33611)
  hit a dead end: the scroll events showed up in debug tools but never
  reached the compositor as actual scrolling.

So this exists as a userspace workaround: a small daemon that does at the
`evdev`/`uinput` level what `xf86-input-wacom` used to do at the X11 driver
level — independent of the compositor, works today.

## How it works

Short version: the daemon exclusively grabs your pen's real input device, so
it's the only thing reading it, then re-emits events through two virtual
(`uinput`) devices:

1. **A tablet clone** — same capabilities as the real pen (pressure, tilt,
   absolute position...), so drawing/cursor behavior is unaffected. While the
   configured button is held, its motion is *not* forwarded here anymore.
2. **A small "pointer" device**, used only for scroll. This turned out to be
   necessary for two non-obvious reasons (found by trial and error — see the
   comments at the top of [`wacom_panscroll.py`](wacom_panscroll.py) for the
   full story):
   - Wheel events sent from a device tagged as a tablet are silently dropped
     by libinput's tablet-tool code path. A device that looks like a plain
     absolute pointer (no pressure axis) doesn't have that problem.
   - That second device has its own pointer position as far as mutter is
     concerned, so it needs to be synced to the pen's current position right
     when a pan gesture starts — otherwise the scroll lands wherever the
     pointer was left last, not where the visible cursor actually is.

While the trigger button is held, pen motion is converted into
velocity-sensitive smooth scroll (`REL_WHEEL_HI_RES`, the same thing
touchpads use) instead of moving the cursor — slow drags scroll a little,
fast ones scroll a lot, with an acceleration curve on top so it doesn't feel
linear/robotic. This matches the modern (2022+) `xf86-input-wacom` driver's
own smooth-panscroll behavior, verified against its source.

**Known cosmetic limitation:** that second pointer device briefly shows its
own mouse-style cursor icon overlapping the tablet cursor while you're
actively panning. Wayland has no per-device "hide this cursor" option, so
this can't be fully avoided from a userspace input daemon — see the code
comments for details if you want to dig further.

## Requirements

- Linux with a Wayland session (tested on GNOME; the approach is
  compositor-agnostic at the input level, but the "which button did what"
  investigation and testing was all done on GNOME/mutter)
- Python 3
- The [`evdev`](https://python-evdev.readthedocs.io/) Python module
- A Wacom tablet whose pen reports `BTN_STYLUS`/`BTN_STYLUS2` and
  `ABS_PRESSURE` (i.e. basically any Wacom pen tablet)
- Read access to `/dev/input/event*` (the `input` group) and write access to
  `/dev/uinput` — `install.sh` checks and helps you set both up

## Install

```bash
git clone https://github.com/<you>/wacom-panscroll.git
cd wacom-panscroll
./install.sh
```

The script:
1. Checks for `python3` and the `evdev` module (tells you the right install
   command for your distro if it's missing).
2. Auto-detects your pen device (asks you to pick if it finds more than one).
3. Checks/fixes group membership for `/dev/input/*` and write access to
   `/dev/uinput` (may ask for `sudo` for these two — it explains why before
   doing anything).
4. Generates and installs a `systemd --user` unit, enables and starts it.

Re-run `./install.sh` any time — it's idempotent.

### Manual install

If you'd rather not run the script: see
[`wacom-panscroll.service.example`](wacom-panscroll.service.example) for the
unit file template and what to edit.

## Configuration

All tuning is environment variables, set via `Environment=` lines in the
systemd unit (`systemctl --user edit --full wacom-panscroll.service`, or
edit `~/.config/systemd/user/wacom-panscroll.service` directly), then:

```bash
systemctl --user daemon-reload
systemctl --user restart wacom-panscroll.service
```

| Variable | Default | Meaning |
|---|---|---|
| `WACOM_PANSCROLL_DEVICE` | `Wacom Intuos S Pen` | Exact device name to grab (`install.sh` sets this for you) |
| `WACOM_PANSCROLL_BUTTON` | `stylus2` | Which side button triggers panning: `stylus` (lower) or `stylus2` (upper) |
| `WACOM_PANSCROLL_SENSITIVITY` | `300` | Tablet units of motion per scroll "notch". Lower = faster/more sensitive |
| `WACOM_PANSCROLL_ACCEL` | `1.6` | Exponent of the speed-based acceleration curve. `1.0` = linear, no boost |
| `WACOM_PANSCROLL_ACCEL_MAX` | `6.0` | Cap on the acceleration multiplier, so a very fast flick doesn't fling you across a whole document |
| `WACOM_PANSCROLL_INVERT_Y` | `0` | `1` to invert vertical scroll direction |
| `WACOM_PANSCROLL_HSCROLL` | `1` | `0` to disable horizontal scroll (vertical only) |

## Verifying it works

```bash
systemctl --user status wacom-panscroll.service
journalctl --user -u wacom-panscroll.service -f
```

You should see it find your device and create two virtual ones. Then: draw
normally (should feel unchanged), hold the configured button and drag the
pen (should scroll instead of moving the cursor), release (back to normal).

## Uninstall

```bash
./uninstall.sh
```

Removes the service. Your tablet goes back to whatever the default Wayland
behavior was before. (It doesn't revert group membership or the
`/dev/uinput` udev rule from install — those are harmless to leave, but you
can remove `/etc/udev/rules.d/99-wacom-panscroll-uinput.rules` yourself with
`sudo` if you want them gone too.)

## Alternatives considered

If this doesn't fit your setup, some other routes exist, with tradeoffs
discussed in the code/commit history of this project:

- **[OpenTabletDriver](https://opentabletdriver.net/)** — a full
  cross-platform tablet driver replacement. Much heavier (own daemon, GUI,
  device database) if all you want is pan-scroll, but worth it if you want
  to replace your whole tablet driving stack.
- **[input-remapper](https://github.com/sezanzeb/input-remapper)** — good
  for discrete button→key remapping, but not built for continuous
  motion→scroll gestures like this one.
- **Wait for native support** — track the GNOME issues linked above; if
  mutter/libinput ever ship this natively, disable this daemon
  (`./uninstall.sh`) and use that instead.

## License

MIT — see [LICENSE](LICENSE).
