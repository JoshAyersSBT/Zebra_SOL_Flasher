from machine import Pin, PWM

class Motor:
    """
    Simple direction + PWM motor.

    Public control API:
      - set_power(power): signed percent-style power, -100..100
      - set(forward, duty_u16): low-level direct control
      - stop()
      - deinit()
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
        self._pwm.duty_u16(duty_u16)

    def set_power(self, power: int):
        """
        Signed power command expected by main.py / RobotAPI.

        power:
          -100 .. 100
             0 stops the motor
            >0 forward
            <0 reverse
        """
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
        self._pwm.duty_u16(0)

    def deinit(self):
        try:
            self._pwm.deinit()
        except Exception:
            pass