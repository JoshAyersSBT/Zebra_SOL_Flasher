# robot/servo.py
from machine import Pin, PWM

class Servo:
    def __init__(self, gpio: int, freq_hz: int = 50, min_us: int = 500, max_us: int = 2500):
        self.freq_hz = int(freq_hz)
        self.min_us = int(min_us)
        self.max_us = int(max_us)

        self._pwm = PWM(Pin(gpio, Pin.OUT))
        self._pwm.freq(self.freq_hz)

        # Detect PWM mode
        self._use_u16 = hasattr(self._pwm, "duty_u16")

    def angle(self, deg: int):
        # Clamp
        a = max(0, min(180, int(deg)))

        # Convert angle → pulse width
        pulse = self.min_us + (self.max_us - self.min_us) * a // 180

        period_us = 1_000_000 // self.freq_hz

        if self._use_u16:
            # RP2040 / newer ports
            duty = (pulse * 65535) // period_us
            self._pwm.duty_u16(int(duty))
        else:
            # ESP32 classic (0–1023)
            duty = (pulse * 1023) // period_us
            self._pwm.duty(int(duty))

    def deinit(self):
        try:
            self._pwm.deinit()
        except Exception:
            pass