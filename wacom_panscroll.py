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
      3. GNOME's screen-edge triggers (Dash to Dock's autohide reveal, hot
         corners) turned out to never fire from the pen at all, confirmed by
         reading gnome-shell/Dash to Dock's own source: both the pressure
         barrier and its dwell fallback key off core-pointer ("mouse")
         motion tracking specifically, which tablet-tool motion never drives
         even though it moves the same visible cursor - they're delivered
         through separate protocol paths (tablet-v2 vs wl_pointer). Normal
         widget hover/leave (interacting with an already-visible dock, etc.)
         works fine with the pen since that goes through Clutter's actor
         picking instead. Fix: mirror the pen's position onto the scroll
         device whenever it's within EDGE_ZONE of any tablet axis edge (not
         just on trigger_down) - this dips into the same double-cursor
         tradeoff above, but only near edges, where it's actually needed for
         GNOME to notice the pen is there.
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

  Beyond plain proportional scrolling, a velocity-based acceleration curve
  boosts fast drags disproportionately (like mouse pointer acceleration):
  below a reference speed the motion is scaled 1:1 (unchanged), and above it
  the effective distance gets multiplied by (velocity/reference)^exponent,
  capped at a maximum multiplier - so slow, deliberate drags stay exactly as
  precise as before, while a fast flick scrolls much further than a linear
  mapping would give it.

Config via environment variables (all optional):
  WACOM_PANSCROLL_DEVICE     device name to grab (default: "Wacom Intuos S Pen")
  WACOM_PANSCROLL_BUTTON     "stylus" or "stylus2" (default: "stylus2")
  WACOM_PANSCROLL_SENSITIVITY tablet units of motion per 120 hi-res scroll
                              units, i.e. per "notch" (default: 300; lower = faster)
  WACOM_PANSCROLL_ACCEL      exponent of the speed-based acceleration curve
                              (default: 1.6; 1.0 = no acceleration, linear)
  WACOM_PANSCROLL_ACCEL_MAX  cap on the acceleration multiplier (default: 6.0)
  WACOM_PANSCROLL_INVERT_Y   "1" to invert vertical scroll direction
  WACOM_PANSCROLL_HSCROLL    "0" to disable horizontal scroll (vertical only)
  WACOM_PANSCROLL_EDGE_SYNC  "0" to disable the screen-edge mirroring described
                              above (default: "1")
  WACOM_PANSCROLL_EDGE_ZONE  fraction of each tablet axis' range, from either
                              end, considered "near an edge" (default: 0.05)
"""
import os
import sys
import time

import evdev
from evdev import ecodes

DEVICE_NAME = os.environ.get("WACOM_PANSCROLL_DEVICE", "Wacom Intuos S Pen")

_BUTTON_MAP = {"stylus": ecodes.BTN_STYLUS, "stylus2": ecodes.BTN_STYLUS2}
TRIGGER_BUTTON = _BUTTON_MAP[os.environ.get("WACOM_PANSCROLL_BUTTON", "stylus2")]

SENSITIVITY = float(os.environ.get("WACOM_PANSCROLL_SENSITIVITY", "320"))
ACCEL_EXPONENT = float(os.environ.get("WACOM_PANSCROLL_ACCEL", "1.6")) # Scroll aceleration
ACCEL_MAX = float(os.environ.get("WACOM_PANSCROLL_ACCEL_MAX", "4.0")) # Max scroll 
INVERT_Y = os.environ.get("WACOM_PANSCROLL_INVERT_Y", "0") == "1"
HSCROLL_ENABLED = os.environ.get("WACOM_PANSCROLL_HSCROLL", "1") != "0"
EDGE_SYNC_ENABLED = os.environ.get("WACOM_PANSCROLL_EDGE_SYNC", "1") != "0"
EDGE_ZONE_FRACTION = float(os.environ.get("WACOM_PANSCROLL_EDGE_ZONE", "0.05"))

HI_RES_UNIT = 120  # libinput/kernel convention: 120 hi-res units == 1 legacy notch
# Tablet units/sec below which motion is left unscaled (1x) - only drags
# faster than this get the acceleration boost. ~40mm/s at this pen's 100
# units/mm resolution: a calm, deliberate drag speed.
ACCEL_REF_VELOCITY = 4000.0
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
    def __init__(self, abs_x_range=None, abs_y_range=None):
        self.panning = False  # trigger button is held: committed to a pan gesture
        self.last_x = None
        self.last_y = None
        self.last_t = None  # timestamp of the last processed frame, for velocity
        self.accum_hires_y = 0.0  # fractional hi-res units not yet emitted
        self.accum_hires_x = 0.0
        self.legacy_carry_y = 0  # hi-res units emitted but not yet folded into a legacy notch
        self.legacy_carry_x = 0
        self.abs_x_range = abs_x_range  # (min, max) raw ABS_X, for edge detection
        self.abs_y_range = abs_y_range  # (min, max) raw ABS_Y, for edge detection


def _near_edge(value, axis_range, zone):
    if not axis_range:
        return False
    lo, hi = axis_range
    span = hi - lo
    if span <= 0:
        return False
    margin = span * zone
    return value <= lo + margin or value >= hi - margin


def process_frame(frame, frame_ts, ui, scroll_dev, state):
    new_abs = {}
    trigger_down = False
    trigger_up = False
    tool_out = False  # pen leaving proximity this frame

    for ev in frame:
        if ev.type == ecodes.EV_KEY and ev.code == TRIGGER_BUTTON:
            if ev.value == 1:
                trigger_down = True
            elif ev.value == 0:
                trigger_up = True
        elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOOL_PEN and ev.value == 0:
            tool_out = True
        elif ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            new_abs[ev.code] = ev.value

    if tool_out:
        # Proximity-out frames often carry a garbage reset position (commonly
        # ABS_X=0, ABS_Y=0) rather than the pen's actual last location - the
        # kernel driver's doing, not a real pen movement. Trusting it made the
        # scroll device's cursor jump to (and get stuck in) the top-left
        # corner every time the pen was lifted. Drop it.
        new_abs.clear()

    prev_x, prev_y = state.last_x, state.last_y
    prev_t = state.last_t
    state.last_t = frame_ts

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
        if tool_out and ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            continue  # don't forward the proximity-out garbage position either
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

    # GNOME's screen-edge triggers (autohide reveal, hot corners) only react
    # to core-pointer motion, never to the pen directly - see module
    # docstring point 3. Keep the scroll device's position glued to the pen
    # whenever it's near any tablet edge, so those triggers actually see it
    # arrive, without parking a second cursor on screen the rest of the time.
    if EDGE_SYNC_ENABLED and new_abs and state.last_x is not None and state.last_y is not None:
        if _near_edge(state.last_x, state.abs_x_range, EDGE_ZONE_FRACTION) or \
                _near_edge(state.last_y, state.abs_y_range, EDGE_ZONE_FRACTION):
            scroll_dev.write(ecodes.EV_ABS, ecodes.ABS_X, state.last_x)
            scroll_dev.write(ecodes.EV_ABS, ecodes.ABS_Y, state.last_y)
            scroll_wrote_any = True

    if state.panning and new_abs:
        dx = new_abs[ecodes.ABS_X] - prev_x if prev_x is not None and ecodes.ABS_X in new_abs else 0
        dy = new_abs[ecodes.ABS_Y] - prev_y if prev_y is not None and ecodes.ABS_Y in new_abs else 0

        dt = (frame_ts - prev_t) if (prev_t is not None and frame_ts is not None) else None
        if dt and dt > 0:
            velocity = (dx * dx + dy * dy) ** 0.5 / dt  # tablet units/sec
            if velocity > ACCEL_REF_VELOCITY:
                accel = min(ACCEL_MAX, (velocity / ACCEL_REF_VELOCITY) ** ACCEL_EXPONENT)
                dx *= accel
                dy *= accel

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
    real_abs = dict(real.capabilities(absinfo=True)[ecodes.EV_ABS])
    abs_x_range = (real_abs[ecodes.ABS_X].min, real_abs[ecodes.ABS_X].max)
    abs_y_range = (real_abs[ecodes.ABS_Y].min, real_abs[ecodes.ABS_Y].max)
    real.grab()
    ui = build_clone(real)
    scroll_dev = build_scroll_device(real)
    log(f"tablet clone at {ui.device.path}, scroll device at {scroll_dev.device.path}")

    state = PanState(abs_x_range=abs_x_range, abs_y_range=abs_y_range)
    frame = []
    try:
        for event in real.read_loop():
            if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                process_frame(frame, event.timestamp(), ui, scroll_dev, state)
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
        f"sensitivity={SENSITIVITY} invert_y={INVERT_Y} hscroll={HSCROLL_ENABLED} "
        f"edge_sync={EDGE_SYNC_ENABLED} edge_zone={EDGE_ZONE_FRACTION}"
    )
    while True:
        found = run_once()
        if not found:
            log(f"{DEVICE_NAME!r} not found, retrying in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
