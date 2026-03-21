# gpio_mapper.py
# Standalone ESP32 MicroPython GPIO mapping helper for servo/motor ports.
#
# What it does:
#  - Mode A: DIGITAL_TOGGLE  -> toggles each candidate GPIO high/low so you can probe with a multimeter/LED
#  - Mode B: SERVO_TWITCH    -> generates 50Hz servo pulses that should make a connected servo twitch/move
#
# Workflow:
# 1) Upload this file to the board and run it in REPL.
# 2) Choose mode.
# 3) For each GPIO it drives, you watch which PHYSICAL PORT responds (servo moves / pin toggles).
# 4) Type the port number, and it records the mapping.
# 5) At the end it prints a Python dict you can paste into your driver PORT_MAP.
#
# Safety:
#  - Don't leave motors connected for long while mapping.
#  - Avoid strapping pins unless you know your board wiring.
#  - Never probe the ESP32 flash pins (GPIO6-11) - they are used internally.

import sys
import utime
from machine import Pin, PWM

# ---- Candidate GPIOs (safe-ish defaults for ESP32) ----
# Excludes GPIO6-11 (flash). Excludes 34-39 (input-only).
# Includes some strapping pins optionally (0,2,12,15). Default is to EXCLUDE them.
SAFE_CANDIDATES = [
    4, 5,
    13, 14,
    16, 17,
    18, 19,
    21, 22, 23,
    25, 26, 27,
    32, 33
]
STRAP_PINS = [0, 2, 12, 15]  # can affect boot if misused on some boards

def _read_line(prompt):
    try:
        return input(prompt)
    except Exception:
        # In case input() isn't available for some reason
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return sys.stdin.readline().strip()

def _print_banner():
    print("\n=== GPIO Mapper (MicroPython ESP32) ===")
    print("Modes:")
    print("  1) DIGITAL_TOGGLE  (probe pins with meter / LED)")
    print("  2) SERVO_TWITCH    (servo should twitch/move)")
    print("Notes:")
    print("  - GPIO6-11 are FLASH pins and are NOT tested.")
    print("  - GPIO34-39 are input-only and are NOT tested.")
    print("  - Strapping pins (0,2,12,15) are optional.\n")

def choose_candidates():
    ans = _read_line("Include strapping pins (0,2,12,15)? [y/N]: ").strip().lower()
    cands = list(SAFE_CANDIDATES)
    if ans == "y":
        cands = STRAP_PINS + cands
    # Remove duplicates while preserving order
    seen = set()
    out = []
    for g in cands:
        if g not in seen:
            out.append(g)
            seen.add(g)
    return out

def digital_toggle_mapper(candidates, port_label="PORT", port_min=1, port_max=7):
    """
    Toggle each candidate GPIO high/low at 2Hz for a few seconds.
    You watch which physical port pin toggles (meter/LED), then type the port number.
    """
    mapping = {}
    print("\n--- DIGITAL_TOGGLE Mapper ---")
    print("Attach a multimeter (DC volts) or LED+resistor to the SIGNAL pin of a board port.")
    print("For each GPIO, you should see it alternate between ~0V and ~3.3V.")
    print("When you identify which %s responds, type its number." % port_label)
    print("Type 's' to skip, 'q' to quit.\n")

    for gpio in candidates:
        if len(mapping) >= (port_max - port_min + 1):
            break

        # Skip if already used
        if gpio in mapping.values():
            continue

        print("\nTesting GPIO %d ..." % gpio)
        p = Pin(gpio, Pin.OUT)
        # toggle for a short window
        t_end = utime.ticks_add(utime.ticks_ms(), 2500)
        state = 0
        while utime.ticks_diff(t_end, utime.ticks_ms()) > 0:
            state ^= 1
            p.value(state)
            utime.sleep_ms(250)

        p.value(0)

        ans = _read_line("Which %s toggled? (%d-%d / s / q): " % (port_label, port_min, port_max)).strip().lower()
        if ans == "q":
            break
        if ans == "s" or ans == "":
            continue
        try:
            port = int(ans)
            if port < port_min or port > port_max:
                print("  ! out of range, skipping")
                continue
            if port in mapping:
                print("  ! %s %d already mapped to GPIO %d; skipping" % (port_label, port, mapping[port]))
                continue
            mapping[port] = gpio
            print("  + mapped %s %d -> GPIO %d" % (port_label, port, gpio))
        except Exception:
            print("  ! invalid input, skipping")

    print("\n=== RESULT: DIGITAL_TOGGLE mapping ===")
    print(mapping)
    print("Copy/paste dict above into your PORT_MAP / SERVO_PORT_MAP.")
    return mapping

def servo_twitch_mapper(candidates, port_label="SERVO_PORT", port_min=1, port_max=7):
    """
    For each candidate GPIO, output a brief servo movement pattern (60->120->90).
    You watch which servo/port moves, then type the port number.
    """
    mapping = {}
    print("\n--- SERVO_TWITCH Mapper ---")
    print("Plug ONE servo at a time (or watch all servos if multiple are connected).")
    print("For each GPIO, the connected servo should twitch/move briefly.")
    print("Then enter which %s moved." % port_label)
    print("Type 's' to skip, 'q' to quit.\n")

    def set_servo_angle(pwm, angle, freq_hz=50, min_us=500, max_us=2500):
        angle = max(0, min(180, int(angle)))
        pulse_us = min_us + (max_us - min_us) * angle // 180
        period_us = 1_000_000 // freq_hz
        duty_u16 = int((pulse_us * 65535) // period_us)
        if hasattr(pwm, "duty_u16"):
            pwm.duty_u16(duty_u16)
        else:
            pwm.duty(int(duty_u16 * 1023 // 65535))

    for gpio in candidates:
        if len(mapping) >= (port_max - port_min + 1):
            break

        if gpio in mapping.values():
            continue

        print("\nTesting GPIO %d (servo pulses)..." % gpio)
        try:
            pwm = PWM(Pin(gpio, Pin.OUT), freq=50)
            # twitch pattern
            set_servo_angle(pwm, 60)
            utime.sleep_ms(350)
            set_servo_angle(pwm, 120)
            utime.sleep_ms(350)
            set_servo_angle(pwm, 90)
            utime.sleep_ms(350)
            pwm.deinit()
        except Exception as e:
            print("  ! PWM failed on GPIO %d: %r" % (gpio, e))
            try:
                pwm.deinit()
            except Exception:
                pass
            continue

        ans = _read_line("Which %s moved? (%d-%d / s / q): " % (port_label, port_min, port_max)).strip().lower()
        if ans == "q":
            break
        if ans == "s" or ans == "":
            continue
        try:
            port = int(ans)
            if port < port_min or port > port_max:
                print("  ! out of range, skipping")
                continue
            if port in mapping:
                print("  ! %s %d already mapped to GPIO %d; skipping" % (port_label, port, mapping[port]))
                continue
            mapping[port] = gpio
            print("  + mapped %s %d -> GPIO %d" % (port_label, port, gpio))
        except Exception:
            print("  ! invalid input, skipping")

    print("\n=== RESULT: SERVO mapping ===")
    print(mapping)
    print("Copy/paste dict above into SERVO_PORT_MAP / PORT_MAP in your driver.")
    return mapping

def main():
    _print_banner()
    candidates = choose_candidates()
    print("\nCandidate GPIOs:", candidates)

    mode = _read_line("Select mode [1=toggle, 2=servo]: ").strip()

    if mode == "1":
        digital_toggle_mapper(candidates, port_label="PORT", port_min=1, port_max=7)
    elif mode == "2":
        servo_twitch_mapper(candidates, port_label="SERVO_PORT", port_min=1, port_max=7)
    else:
        print("Unknown mode.")

if __name__ == "__main__":
    main()