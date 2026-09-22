import evdev, time
d = evdev.InputDevice('/dev/input/event10')
end = time.time() + 300
for event in d.read_loop():
    if time.time() > end:
        break
    if event.type == evdev.ecodes.EV_KEY:
        print(time.time(), evdev.categorize(event), flush=True)
