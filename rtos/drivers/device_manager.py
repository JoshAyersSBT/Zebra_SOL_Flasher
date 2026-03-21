# /rtos/drivers/device_manager.py
from .zebra_servo import ZebraServo
from .smotor2 import SMotor2
from .port_map import SERVO_PORT_MAP, MOTOR_PORT_MAP

class DeviceManager:
    def __init__(self):
        self.servos = {}  # port -> ZebraServo
        self.motors = {}  # port -> SMotor2

    def detect_servos(self):
        found = []
        for port, gpio in SERVO_PORT_MAP.items():
            if gpio is None:
                continue
            try:
                s = ZebraServo(port)
                s.begin()
                self.servos[port] = s
                found.append(port)
            except Exception:
                pass
        return found

    def detect_motors(self):
        found = []
        for port, cfg in MOTOR_PORT_MAP.items():
            if not cfg or cfg.get("pwm") is None or cfg.get("dir") is None:
                continue
            try:
                m = SMotor2(port)
                m.begin()
                self.motors[port] = m
                found.append(port)
            except Exception:
                pass
        return found

    def deinit_all(self):
        for s in self.servos.values():
            try: s.deinit()
            except Exception: pass
        for m in self.motors.values():
            try: m.deinit()
            except Exception: pass
        self.servos = {}
        self.motors = {}