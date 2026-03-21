# servo_calibrate_slow.py
# Non-interactive servo calibration for MicroPython + mpremote run
# - No input()
# - Slow holds so you can observe
# - Ctrl+C to stop when you find good settings

from machine import Pin, PWM
import time

SERVO_GPIO = 18    # <-- change to the GPIO you're using for the servo signal
LED_GPIO = None     # e.g. 2 for many ESP32 boards; set None to disable LED cue

# Frequencies to test (common)
FREQS = [50, 100, 200, 250, 333]

# "Pulse script" in microseconds: center + endpoints + wide endpoints
PULSES_US = [
    1500,  # center / neutral for most servos
    1000,  # typical min
    2000,  # typical max
    1200,
    1800,
    700,   # wider min
    2300,  # wider max
    1500,
]

HOLD_S = 2.0        # seconds to hold each pulse
BETWEEN_S = 0.6     # seconds between pulses (rest)
REST_CENTER_US = 1500
REST_BETWEEN_FREQ_S = 3.0

def duty_u16_from_pulse_us(freq_hz, pulse_us):
    period_us = 1_000_000 // freq_hz
    if pulse_us < 0:
        pulse_us = 0
    if pulse_us > period_us:
        pulse_us = period_us
    return (pulse_us * 65535) // period_us

def set_pulse(pwm, freq_hz, pulse_us):
    pwm.freq(freq_hz)
    duty = duty_u16_from_pulse_us(freq_hz, pulse_us)
    if hasattr(pwm, "duty_u16"):
        pwm.duty_u16(int(duty))
    else:
        pwm.duty(int(duty * 1023 // 65535))

def blink(led, n=1, on_ms=120, off_ms=120):
    if not led:
        return
    for _ in range(n):
        led.value(1); time.sleep_ms(on_ms)
        led.value(0); time.sleep_ms(off_ms)

def main():
    led = None
    if LED_GPIO is not None:
        try:
            led = Pin(LED_GPIO, Pin.OUT)
            led.value(0)
        except Exception:
            led = None

    print("=== Servo calibration (slow, non-interactive) ===")
    print("SERVO_GPIO =", SERVO_GPIO)
    print("HOLD_S =", HOLD_S, "BETWEEN_S =", BETWEEN_S)
    print("Press Ctrl+C when you see a good behavior; note the last printed freq/pulse.\n")

    pwm = PWM(Pin(SERVO_GPIO, Pin.OUT), freq=50)

    try:
        while True:
            for f in FREQS:
                print("\n--- FREQ =", f, "Hz ---")
                blink(led, n=2)

                # Rest at center before starting this frequency
                print("Resting at", REST_CENTER_US, "us")
                set_pulse(pwm, f, REST_CENTER_US)
                time.sleep(REST_BETWEEN_FREQ_S)

                for us in PULSES_US:
                    print("FREQ", f, "Hz  PULSE", us, "us  (hold", HOLD_S, "s)")
                    blink(led, n=1)

                    set_pulse(pwm, f, us)
                    time.sleep(HOLD_S)

                    # brief rest (center) between moves to reduce hunting
                    set_pulse(pwm, f, REST_CENTER_US)
                    time.sleep(BETWEEN_S)

    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")
    finally:
        try:
            # Stop driving signal (optional) by deinit
            pwm.deinit()
        except Exception:
            pass
        if led:
            led.value(0)

if __name__ == "__main__":
    main()