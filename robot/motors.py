# robot/motors.py
from machine import Pin, PWM

class Motor:
    """
    Simple direction + PWM motor.
    speed_u16: 0..65535
    direction: 1 forward, 0 reverse (may need swapping per wiring)
    """
    def __init__(self, pwm_gpio: int, dir_gpio: int, pwm_freq_hz: int = 20000):
        self._dir = Pin(dir_gpio, Pin.OUT)
        self._pwm = PWM(Pin(pwm_gpio, Pin.OUT), freq=pwm_freq_hz)
        self.stop()

    def set(self, forward: bool, duty_u16: int):
        if duty_u16 < 0: duty_u16 = 0
        if duty_u16 > 65535: duty_u16 = 65535
        self._dir.value(1 if forward else 0)
        self._pwm.duty_u16(duty_u16)

    def stop(self):
        self._pwm.duty_u16(0)

    def deinit(self):
        try: self._pwm.deinit()
        except Exception: pass