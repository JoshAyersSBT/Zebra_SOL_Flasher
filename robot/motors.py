from machine import Pin, PWM

class Motor:
    """
    Simple direction + PWM motor.

    Active-low PWM:
      duty 0      -> full on
      duty 65535  -> off
    """

    def __init__(self, pwm_gpio: int, dir_gpio: int, pwm_freq_hz: int = 20000):
        self._dir = Pin(dir_gpio, Pin.OUT)
        self._pwm = PWM(Pin(pwm_gpio, Pin.OUT), freq=pwm_freq_hz)
        self.stop()

    def set(self, forward: bool, duty_u16: int):
        if duty_u16 < 0:
            duty_u16 = 0
        if duty_u16 > 65535:
            duty_u16 = 65535

        self._dir.value(1 if forward else 0)

        # Inverted PWM
        self._pwm.duty_u16(65535 - duty_u16)

    def set_power(self, power: int):
        power = int(power)

        if power == 0:
            self.stop()
            return

        forward = power > 0
        mag = abs(power)
        if mag > 100:
            mag = 100

        duty_u16 = (mag * 65535) // 100
        self.set(forward, duty_u16)

    def stop(self):
        self._pwm.duty_u16(65535)

    def deinit(self):
        try:
            self._pwm.deinit()
        except Exception:
            pass