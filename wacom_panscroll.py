#!/usr/bin/env python3
"""Pan-scroll for a Wacom stylus button on Wayland/GNOME.

Replicates `xsetwacom ... Button N "pan"` (X11) at the evdev level, since
neither libinput nor mutter implement it on Wayland yet.

How it works:
  - Grabs the real pen device exclusively (EVIOCGRAB) so its raw events never
    reach libinput directly.
  - Creates a uinput clone with the same vendor/product/bustype and absolute
    axis ranges. udev/libwacom tag this clone as the same physical tablet
    (verified with `libwacom-list-local-devices`), so GNOME's tablet handling
    is unaffected.
  - Creates a second, separate uinput "mouse" device with only REL_X/REL_Y/
    REL_WHEEL/REL_HWHEEL/BTN_LEFT. This is necessary because udev tags the
    tablet clone as ID_INPUT_TABLET, and libinput's tablet-tool code path
    silently drops REL_WHEEL events — they never reach the compositor as
    scroll even though they show up fine in raw evdev dumps. A plain
    relative-pointer device doesn't have that problem.
  - Normal events are passed through 1:1 to the tablet clone. While the
    configured trigger button is held, ABS_X/ABS_Y motion is converted into
    scroll wheel events on the mouse device instead of being forwarded as
    cursor movement, and the trigger button's own press/release is swallowed
    so it doesn't also fire its default click action.

Config via environment variables (all optional):
  WACOM_PANSCROLL_DEVICE     device name to grab (default: "Wacom Intuos S Pen")
  WACOM_PANSCROLL_BUTTON     "stylus" or "stylus2" (default: "stylus2")
  WACOM_PANSCROLL_THRESHOLD  tablet units of motion per scroll step (default: 300)
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

THRESHOLD = int(os.environ.get("WACOM_PANSCROLL_THRESHOLD", "300"))
INVERT_Y = os.environ.get("WACOM_PANSCROLL_INVERT_Y", "0") == "1"
HSCROLL_ENABLED = os.environ.get("WACOM_PANSCROLL_HSCROLL", "1") != "0"

RETRY_SECONDS = 3


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
        name=real.name + " (panscroll)",
        vendor=real.info.vendor,
        product=real.info.product,
        version=real.info.version,
        bustype=real.info.bustype,
    )


def build_scroll_device():
    # Deliberately generic vendor/product (not the Wacom's) and no ABS axes,
    # so udev classifies this as a plain mouse (ID_INPUT_MOUSE) rather than a
    # tablet - that's what makes libinput actually forward REL_WHEEL as scroll.
    caps = {
        ecodes.EV_KEY: [ecodes.BTN_LEFT],
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL],
    }
    return evdev.UInput(events=caps, name="wacom-panscroll wheel")


class PanState:
    def __init__(self):
        self.button_down = False
        self.last_x = None
        self.last_y = None
        self.accum_x = 0
        self.accum_y = 0


def process_frame(frame, ui, scroll_dev, state):
    new_abs = {}
    trigger_seen = False
    trigger_value = None

    for ev in frame:
        if ev.type == ecodes.EV_KEY and ev.code == TRIGGER_BUTTON:
            trigger_seen = True
            trigger_value = ev.value
        elif ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            new_abs[ev.code] = ev.value

    if trigger_seen:
        state.button_down = bool(trigger_value)
        if state.button_down:
            # reset accumulators so a fresh press doesn't inherit stale drift
            state.accum_x = 0
            state.accum_y = 0

    panning = state.button_down
    wrote_any = False

    for ev in frame:
        if ev.type == ecodes.EV_KEY and ev.code == TRIGGER_BUTTON:
            continue  # never forward the trigger button itself
        if panning and ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y):
            continue  # motion becomes scroll instead of cursor movement
        ui.write(ev.type, ev.code, ev.value)
        wrote_any = True

    scroll_wrote_any = False
    if panning and new_abs:
        if state.last_x is not None and ecodes.ABS_X in new_abs:
            state.accum_x += new_abs[ecodes.ABS_X] - state.last_x
        if state.last_y is not None and ecodes.ABS_Y in new_abs:
            state.accum_y += new_abs[ecodes.ABS_Y] - state.last_y

        steps_y = int(state.accum_y / THRESHOLD)
        if steps_y:
            value = -steps_y if not INVERT_Y else steps_y
            scroll_dev.write(ecodes.EV_REL, ecodes.REL_WHEEL, value)
            state.accum_y -= steps_y * THRESHOLD
            scroll_wrote_any = True

        if HSCROLL_ENABLED:
            steps_x = int(state.accum_x / THRESHOLD)
            if steps_x:
                scroll_dev.write(ecodes.EV_REL, ecodes.REL_HWHEEL, steps_x)
                state.accum_x -= steps_x * THRESHOLD
                scroll_wrote_any = True

    for code, value in new_abs.items():
        if code == ecodes.ABS_X:
            state.last_x = value
        elif code == ecodes.ABS_Y:
            state.last_y = value

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
    scroll_dev = build_scroll_device()
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
        f"threshold={THRESHOLD} invert_y={INVERT_Y} hscroll={HSCROLL_ENABLED}"
    )
    while True:
        found = run_once()
        if not found:
            log(f"{DEVICE_NAME!r} not found, retrying in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
