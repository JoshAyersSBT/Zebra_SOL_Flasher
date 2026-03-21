# robot/servo.py
from machine import Pin, PWM

class Servo:
    def __init__(self, gpio: int, freq_hz: int = 50, min_us: int = 500, max_us: int = 2500):
        self.freq_hz = int(freq_hz)
        self.min_us = int(min_us)
        self.max_us = int(max_us)
        self._pwm = PWM(Pin(gpio, Pin.OUT), freq=self.freq_hz)

    def angle(self, deg: int):
        a = int(deg)
        if a < 0: a = 0
        if a > 180: a = 180
        pulse = self.min_us + (self.max_us - self.min_us) * a // 180
        period_us = 1_000_000 // self.freq_hz
        duty_u16 = (pulse * 65535) // period_us
        self._pwm.duty_u16(int(duty_u16))

    def deinit(self):
        try: self._pwm.deinit()
        except Exception: pass