"""Spike: verify a uinput clone of the Wacom pen is recognized as a real tablet.

Grabs the real pen device, creates a uinput clone with matching vendor/product,
waits a bit so udev/libwacom can tag it, prints the new device's syspath, then
releases everything.
"""
import time
import evdev

PEN_NAME = "Wacom Intuos S Pen"


def find_pen():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if d.name == PEN_NAME:
            return d
    raise RuntimeError(f"device {PEN_NAME!r} not found")


def main():
    real = find_pen()
    print("real device:", real.path, real.info)

    caps = real.capabilities(absinfo=True)
    caps.pop(evdev.ecodes.EV_SYN, None)
    caps.pop(evdev.ecodes.EV_FF, None)
    caps[evdev.ecodes.EV_REL] = [evdev.ecodes.REL_WHEEL, evdev.ecodes.REL_HWHEEL]

    real.grab()
    print("grabbed real device")

    clone = evdev.UInput(
        events=caps,
        name=PEN_NAME + " (panscroll)",
        vendor=real.info.vendor,
        product=real.info.product,
        version=real.info.version,
        bustype=real.info.bustype,
    )
    print("clone device:", clone.device.path)
    with open("/home/projects/plane-wacom/spike/clone_path.txt", "w") as f:
        f.write(clone.device.path)

    time.sleep(15)

    clone.close()
    real.ungrab()
    print("released")


if __name__ == "__main__":
    main()
