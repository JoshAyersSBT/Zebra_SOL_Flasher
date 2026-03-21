# /rtos/drivers/zebra_servo.py
from machine import Pin, PWM
from .port_map import SERVO_PORT_MAP

class ZebraServo:
    def __init__(self, port: int, freq_hz: int = 50, min_us: int = 500, max_us: int = 2500):
        self.port = int(port)
        self.freq_hz = int(freq_hz)
        self.min_us = int(min_us)
        self.max_us = int(max_us)

        self.gpio = SERVO_PORT_MAP.get(self.port, None)
        self._pwm = None

    def begin(self):
        if self.gpio is None:
            raise ValueError(f"Servo port {self.port} not mapped in SERVO_PORT_MAP")

        pin = Pin(self.gpio, Pin.OUT)
        self._pwm = PWM(pin, freq=self.freq_hz)
        self.run_angles(90)

    def deinit(self):
        if self._pwm:
            try: self._pwm.deinit()
            except Exception: pass
            self._pwm = None

    def run_angles(self, angles: int):
        if not self._pwm:
            raise RuntimeError("Servo not initialized; call begin()")

        a = int(angles)
        if a < 0: a = 0
        if a > 180: a = 180

        pulse_us = self.min_us + (self.max_us - self.min_us) * a // 180
        period_us = 1_000_000 // self.freq_hz
        duty_u16 = int((pulse_us * 65535) // period_us)

        if hasattr(self._pwm, "duty_u16"):
            self._pwm.duty_u16(duty_u16)
        else:
            self._pwm.duty(int(duty_u16 * 1023 // 65535))