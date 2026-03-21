# robot/motor_feedback.py
from machine import Pin
from robot.debug_io import info, error


class MotorFeedback:
    def __init__(self, motor_port_map):
        """
        motor_port_map:
        {
            1: {"pwm": ..., "dir": ..., "enc": ...},
            2: {...},
            ...
        }
        """
        self.motor_port_map = motor_port_map
        self.ticks = {}
        self.pins = {}

        for port, cfg in motor_port_map.items():
            enc = cfg.get("enc")
            self.ticks[port] = 0

            if enc is None:
                continue

            try:
                pin = Pin(enc, Pin.IN)
                pin.irq(trigger=Pin.IRQ_RISING, handler=self._make_irq(port))
                self.pins[port] = pin
                info("MotorFeedback: encoder IRQ on port {}".format(port))
            except Exception as e:
                error("MOTOR_FB_INIT_{}".format(port), e)

    def _make_irq(self, port):
        def _irq(_pin):
            try:
                self.ticks[port] += 1
            except Exception:
                pass
        return _irq

    def reset(self, port=None):
        if port is None:
            for p in self.ticks:
                self.ticks[p] = 0
        else:
            self.ticks[port] = 0

    def get(self, port):
        return int(self.ticks.get(port, 0))