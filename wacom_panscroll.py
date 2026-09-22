#!/usr/bin/env python3
"""Pan-scroll for a Wacom stylus button on Wayland/GNOME.

Replicates `xsetwacom ... Button N "pan"` (X11) at the evdev level, since
neither libinput nor mutter implement it on Wayland yet.

How it works:
  - Grabs the real pen device exclusively (EVIOCGRAB) so its raw events never
    reach libinput directly.
  - Creates a uinput clone with the same absolute axis ranges/resolution as
    the real pen (so cursor mapping/pressure/tilt behave identically), but
    with a distinct, clearly-synthetic identity (BUS_VIRTUAL, made-up
    vendor/product). IMPORTANT: earlier versions of this daemon copied the
    real device's vendor/product/bustype so libwacom would group the clone
    under the same tablet entry. That caused mutter to treat the clone and
    the (now silent) real device as the same physical tablet colliding with
    itself, especially across the grab/recreate cycles that happen on
    reconnect or service restart - it corrupted mutter's internal device
    bookkeeping (GObject signal-handler warnings piling up) and eventually
    crashed gnome-shell with a SIGSEGV. Do not reintroduce identity spoofing
    here without re-validating carefully.
  - Creates a second, separate uinput "pointer" device for scroll, with
    ABS_X/ABS_Y (same range/resolution as the real pen, but no pressure or
    other tablet-only axes), BTN_LEFT, and the wheel codes. Two things drove
    this design, both found the hard way:
      1. Adding REL_WHEEL directly to the tablet clone doesn't work: udev
         tags that device ID_INPUT_TABLET, and libinput's tablet-tool code
         path silently drops REL_WHEEL - it shows up fine in raw evdev dumps
         but never reaches the compositor as scroll. ABS_X/ABS_Y *without*
         ABS_PRESSURE, on the other hand, gets tagged ID_INPUT_MOUSE (verified
         with udevadm) - an absolute pointer, not a tablet - so wheel events
         from it work normally.
      2. A second device inherently has its own, separate pointer position as
         far as mutter is concerned. If we only ever sent relative deltas (or
         nothing) on it, its position would go stale the moment the real pen
         moves the visible cursor via the tablet clone - scroll would then
         land whatever window the pointer was last "really" in, not where the
         cursor is visually shown now (this was reported and reproduced: two
         windows, cursor visibly over window B, scroll landing in window A).
         Fix: mirror the pen's current ABS_X/ABS_Y onto this device the
         moment a pan gesture starts, so its position matches the visible
         cursor before any wheel event is sent. (Only at gesture start, not
         continuously - this device gets its own mouse-style cursor sprite
         from mutter, and there's no per-device "hide cursor" option on
         Wayland, so touching its position keeps a second cursor icon
         visible; syncing only when panning begins limits how long that
         second cursor is parked on screen instead of it sitting there for
         the whole session. It's a known cosmetic tradeoff of this approach,
         not something fixable purely at the input-device level.)
  - Normal events are passed through 1:1 to the tablet clone. This part
    matches the real xf86-input-wacom driver's AC_PANSCROLL action exactly
    (verified against its source, src/wcmCommon.c): the moment the trigger
    button goes down, the gesture commits to panning immediately - there is
    no click-vs-drag ambiguity and no fallback to the button's normal click
    action. While held, ABS_X/ABS_Y motion is converted into smooth,
    velocity-proportional scroll instead of moving the cursor (the modern
    xf86-input-wacom driver does this via relative scroll valuators; here we
    use REL_WHEEL_HI_RES, the equivalent unit libinput/GTK use for
    touchpad-style smooth scrolling), and the button press/release itself is
    never forwarded. A button assigned to panscroll simply stops being a
    click button, same as on X11.

Config via environment variables (all optional):
  WACOM_PANSCROLL_DEVICE     device name to grab (default: "Wacom Intuos S Pen")
  WACOM_PANSCROLL_BUTTON     "stylus" or "stylus2" (default: "stylus2")
  WACOM_PANSCROLL_SENSITIVITY tablet units of motion per 120 hi-res scroll
                              units, i.e. per "notch" (default: 300; lower = faster)
  WACOM_PANSCROLL_INVERT_Y   "1" to invert vertical scroll direction
  WACOM_PANSCROLL_HSCROLL    "0" to disable horizontal scroll (vertical only)
"""
import os
import sys
import time

import evdev
from evdev import ecodes

DEVICE_NAME = os.environ.get("WACOM_PANSCROLL_DEVICE", "Wacom Intuos S Pen")

_BUTTON_MAP = {"stylus": ecodes.BTN_STYLUS, "stylus2": ecodes.BTN_STYLUS2}
TRIGGER_BUTTON = _BUTTON_MAP[os.environ.get("WACOM_PANSCROLL_BUTTON", "stylus2")]

SENSITIVITY = float(os.environ.get("WACOM_PANSCROLL_SENSITIVITY", "300"))
INVERT_Y = os.environ.get("WACOM_PANSCROLL_INVERT_Y", "0") == "1"
HSCROLL_ENABLED = os.environ.get("WACOM_PANSCROLL_HSCROLL", "1") != "0"

HI_RES_UNIT = 120  # libinput/kernel convention: 120 hi-res units == 1 legacy notch
RETRY_SECONDS = 3

# Synthetic identity for our virtual devices - deliberately NOT the real
# Wacom's vendor/product. See module docstring for why this matters.
SYNTH_VENDOR = 0x0a5a
SYNTH_TABLET_PRODUCT = 0x1001
SYNTH_MOUSE_PRODUCT = 0x1002


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def find_device():
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if d.name == DEVICE_NAME:
            return d
        d.close()
    return None


def build_clone(real):
    caps = real.capabilities(absinfo=True)
    caps.pop(ecodes.EV_SYN, None)
    caps.pop(ecodes.EV_FF, None)
    return evdev.UInput(
        events=caps,
        name="wacom-panscroll tablet",
        vendor=SYNTH_VENDOR,
        product=SYNTH_TABLET_PRODUCT,
        version=1,
        bustype=ecodes.BUS_VIRTUAL,
    )


def build_scroll_device(real):
    # ABS_X/ABS_Y (no pressure/distance) + BTN_LEFT + wheel: udev classifies
    # this ID_INPUT_MOUSE (verified with udevadm), i.e. a plain absolute
    # pointer, not a tablet - so unlike the tablet clone, wheel events from
    # it actually reach the compositor as scroll. Same axis ranges as the
    # real pen so a mirrored coordinate lands in the same screen location.
    real_caps = dict(real.capabilities(absinfo=True)[ecodes.EV_ABS])
    caps = {
        ecodes.EV_ABS: [
            (ecodes.ABS_X, real_caps[ecodes.ABS_X]),
            (ecodes.ABS_Y, real_caps[ecodes.ABS_Y]),
        ],
        ecodes.EV_KEY: [ecodes.BTN_LEFT],
        ecodes.EV_REL: [
            ecodes.REL_WHEEL,
            ecodes.REL_HWHEEL,
            ecodes.REL_WHEEL_HI_RES,
            ecodes.REL_HWHEEL_HI_RES,
        ],
    }
    return evdev.UInput(
        events=caps,
        name="wacom-panscroll pointer",
        vendor=SYNTH_VENDOR,
        product=SYNTH_MOUSE_PRODUCT,
        version=1,
        bustype=ecodes.BUS_VIRTUAL,
    )


class PanState:
    def __init__(self):
        self.panning = False  # trigger button is held: committed to a pan gesture
        self.last_x = None
        self.last_y = None
        self.accum_hires_y = 0.0  # fractional hi-res units not yet emitted
        self.accum_hires_x = 0.0
        self.legacy_carry_y = 0  # hi-res units emitted but not yet folded into a legacy notch
        self.legacy_carry_x = 0


def process_frame(frame, ui, scroll_dev, state):
    new_abs = {}
    trigger_down = False
    trigger_up = False

    for ev in frame:
        if ev.type == ecodes.EV_KEY and ev.code == TRIGGER_BUTTON:
            if ev.value == 1:
                trigger_down = True
            elif ev.value == 0:
                trigger_up = True
        elif ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            new_abs[ev.code] = ev.value

    prev_x, prev_y = state.last_x, state.last_y

    if trigger_down:
        # commit to panning immediately, same as xf86-input-wacom's AC_PANSCROLL
        state.panning = True
        state.accum_hires_x = 0.0
        state.accum_hires_y = 0.0

    wrote_any = False
    for ev in frame:
        if ev.type == ecodes.EV_KEY and ev.code == TRIGGER_BUTTON:
            continue  # a button assigned to panscroll never forwards its own click
        if state.panning and ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            continue  # motion becomes scroll instead of cursor movement
        if state.panning and ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
            continue  # resting the tip while panning must not also fire a left-click-drag
        ui.write(ev.type, ev.code, ev.value)
        wrote_any = True

    for code, value in new_abs.items():
        if code == ecodes.ABS_X:
            state.last_x = value
        elif code == ecodes.ABS_Y:
            state.last_y = value

    # Mirror the pen's position onto the scroll device only at the moment a
    # pan gesture starts (not continuously) - this device gets its own
    # mouse-style cursor sprite from mutter (there's no per-device "hide
    # cursor" knob on Wayland), so touching its position keeps that second
    # cursor visibly parked wherever it last was. Syncing only on trigger_down
    # confines that second cursor's visibility to while a gesture is active,
    # instead of it sitting on screen for the whole session.
    scroll_wrote_any = False
    if trigger_down and state.last_x is not None and state.last_y is not None:
        scroll_dev.write(ecodes.EV_ABS, ecodes.ABS_X, state.last_x)
        scroll_dev.write(ecodes.EV_ABS, ecodes.ABS_Y, state.last_y)
        scroll_wrote_any = True

    if state.panning and new_abs:
        dx = new_abs[ecodes.ABS_X] - prev_x if prev_x is not None and ecodes.ABS_X in new_abs else 0
        dy = new_abs[ecodes.ABS_Y] - prev_y if prev_y is not None and ecodes.ABS_Y in new_abs else 0

        hires_y = (-dy if not INVERT_Y else dy) * HI_RES_UNIT / SENSITIVITY
        state.accum_hires_y += hires_y
        step_y = int(state.accum_hires_y)
        if step_y:
            scroll_dev.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, step_y)
            state.accum_hires_y -= step_y
            scroll_wrote_any = True

            state.legacy_carry_y += step_y
            notches_y = int(state.legacy_carry_y / HI_RES_UNIT)
            if notches_y:
                scroll_dev.write(ecodes.EV_REL, ecodes.REL_WHEEL, notches_y)
                state.legacy_carry_y -= notches_y * HI_RES_UNIT

        if HSCROLL_ENABLED:
            hires_x = dx * HI_RES_UNIT / SENSITIVITY
            state.accum_hires_x += hires_x
            step_x = int(state.accum_hires_x)
            if step_x:
                scroll_dev.write(ecodes.EV_REL, ecodes.REL_HWHEEL_HI_RES, step_x)
                state.accum_hires_x -= step_x
                scroll_wrote_any = True

                state.legacy_carry_x += step_x
                notches_x = int(state.legacy_carry_x / HI_RES_UNIT)
                if notches_x:
                    scroll_dev.write(ecodes.EV_REL, ecodes.REL_HWHEEL, notches_x)
                    state.legacy_carry_x -= notches_x * HI_RES_UNIT

    if trigger_up:
        state.panning = False

    if wrote_any:
        ui.syn()
    if scroll_wrote_any:
        scroll_dev.syn()


def run_once():
    real = find_device()
    if real is None:
        return False

    log(f"found {DEVICE_NAME} at {real.path}, grabbing")
    real.grab()
    ui = build_clone(real)
    scroll_dev = build_scroll_device(real)
    log(f"tablet clone at {ui.device.path}, scroll device at {scroll_dev.device.path}")

    state = PanState()
    frame = []
    try:
        for event in real.read_loop():
            if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                process_frame(frame, ui, scroll_dev, state)
                frame = []
            else:
                frame.append(event)
    except OSError as e:
        log(f"device error, will retry: {e}")
        return True
    finally:
        try:
            ui.close()
        except Exception:
            pass
        try:
            scroll_dev.close()
        except Exception:
            pass
        try:
            real.ungrab()
        except Exception:
            pass
        real.close()
    return True


def main():
    log(
        f"wacom-panscroll starting: device={DEVICE_NAME!r} "
        f"trigger={'BTN_STYLUS2' if TRIGGER_BUTTON == ecodes.BTN_STYLUS2 else 'BTN_STYLUS'} "
        f"sensitivity={SENSITIVITY} invert_y={INVERT_Y} hscroll={HSCROLL_ENABLED}"
    )
    while True:
        found = run_once()
        if not found:
            log(f"{DEVICE_NAME!r} not found, retrying in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
