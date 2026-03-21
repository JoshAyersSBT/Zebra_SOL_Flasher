# /rtos/drivers/smotor2.py
from machine import Pin, PWM
from .port_map import MOTOR_PORT_MAP

class SMotor2:
    def __init__(self, port: int, pwm_freq_hz: int = 20000):
        self.port = int(port)
        self.pwm_freq_hz = int(pwm_freq_hz)

        cfg = MOTOR_PORT_MAP.get(self.port)
        if not cfg:
            raise ValueError(f"Motor port {self.port} not present in MOTOR_PORT_MAP")

        self.gpio_pwm = cfg.get("pwm")
        self.gpio_dir = cfg.get("dir")
        self.gpio_enc = cfg.get("enc")

        self._pwm = None
        self._dir = None
        self._enc = None

        self.tick_count = 0

    def begin(self):
        if self.gpio_pwm is None or self.gpio_dir is None:
            raise ValueError(f"Motor port {self.port} missing pwm/dir mapping")

        self._dir = Pin(self.gpio_dir, Pin.OUT)
        self._pwm = PWM(Pin(self.gpio_pwm, Pin.OUT), freq=self.pwm_freq_hz)
        self.stop_motor()

        # Optional encoder
        if self.gpio_enc is not None:
            self._enc = Pin(self.gpio_enc, Pin.IN)
            # rising edge tick
            self._enc.irq(trigger=Pin.IRQ_RISING, handler=self._on_tick)

    def _on_tick(self, _pin):
        # keep it tiny; ISR safe
        self.tick_count += 1

    def run_motor(self, power: int):
        """
        power: -100..100 (matches your header comment) :contentReference[oaicite:6]{index=6}
        """
        if self._pwm is None or self._dir is None:
            raise RuntimeError("Motor not initialized; call begin()")

        p = int(power)
        if p < -100: p = -100
        if p > 100: p = 100

        forward = (p >= 0)
        mag = abs(p)

        # Direction pin: you may need to invert depending on wiring
        self._dir.value(1 if forward else 0)

        duty_u16 = int(mag * 65535 // 100)
        if hasattr(self._pwm, "duty_u16"):
            self._pwm.duty_u16(duty_u16)
        else:
            self._pwm.duty(int(duty_u16 * 1023 // 65535))

    def stop_motor(self):
        if self._pwm:
            if hasattr(self._pwm, "duty_u16"):
                self._pwm.duty_u16(0)
            else:
                self._pwm.duty(0)

    def deinit(self):
        try:
            if self._enc:
                self._enc.irq(handler=None)
        except Exception:
            pass
        try:
            if self._pwm:
                self._pwm.deinit()
        except Exception:
            pass
        self._pwm = None
        self._dir = None
        self._enc = None