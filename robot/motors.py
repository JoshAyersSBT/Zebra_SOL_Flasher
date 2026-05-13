# robot/motors.py
from machine import Pin, PWM


class Motor:
    """
    2-control-line DC motor driver.

    Wiring table:
      F = encoder/tick flag input  (handled by motor_feedback.py)
      D = direction output
      P = PWM / power output

    Constructor:
      Motor(pwm_gpio, dir_gpio, pwm_freq_hz=20000, invert_pwm=False, invert_dir=False)

    Public power:
      -100..100
       0 = stop
    """

    def __init__(
        self,
        pwm_gpio: int,
        dir_gpio: int,
        pwm_freq_hz: int = 20000,
        invert_pwm: bool = False,
        invert_dir: bool = False,
    ):
        self._pwm_gpio = int(pwm_gpio)
        self._dir_gpio = int(dir_gpio)
        self._pwm_freq_hz = int(pwm_freq_hz)
        self._invert_pwm = bool(invert_pwm)
        self._invert_dir = bool(invert_dir)

        self._dir = Pin(self._dir_gpio, Pin.OUT)
        self._pwm = PWM(Pin(self._pwm_gpio, Pin.OUT), freq=self._pwm_freq_hz)

        self.max_duty = 65535
        self._power = 0
        self._last_raw_duty = 0

        self.stop()

    def _clamp_power(self, power):
        power = int(power)
        if power > 100:
            return 100
        if power < -100:
            return -100
        return power

    def _clamp_duty(self, duty_u16):
        duty_u16 = int(duty_u16)
        if duty_u16 < 0:
            return 0
        if duty_u16 > 65535:
            return 65535
        return duty_u16

    def _write_pwm(self, duty_u16):
        duty_u16 = self._clamp_duty(duty_u16)

        if self._invert_pwm:
            duty_u16 = 65535 - duty_u16

        self._last_raw_duty = duty_u16
        self._pwm.duty_u16(duty_u16)

    def set_power(self, power: int):
        power = self._clamp_power(power)

        if power == 0:
            self.stop()
            return

        forward = power > 0
        dir_value = 1 if forward else 0

        if self._invert_dir:
            dir_value = 0 if forward else 1

        self._dir.value(dir_value)

        duty_u16 = (abs(power) * 65535) // 100
        self._write_pwm(duty_u16)

        self._power = power

    def set(self, forward: bool, duty_u16: int):
        duty_u16 = self._clamp_duty(duty_u16)
        percent = (duty_u16 * 100) // 65535
        self.set_power(percent if forward else -percent)

    def stop(self):
        self._power = 0

        # Keep direction in a known idle state.
        try:
            self._dir.value(0 if not self._invert_dir else 1)
        except Exception:
            pass

        # PWM off. If your motor driver is active-low, set invert_pwm=True
        # in MOTOR_PORT_MAP for that port.
        self._write_pwm(0)

    def brake(self):
        self.stop()

    def power(self):
        return self._power

    def raw_duty(self):
        return self._last_raw_duty

    def deinit(self):
        try:
            self.stop()
        except Exception:
            pass

        try:
            self._pwm.deinit()
        except Exception:
            pass
