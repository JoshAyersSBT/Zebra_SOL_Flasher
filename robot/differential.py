def _clamp(value, lo=-100, hi=100):
    value = int(value)
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


class DifferentialDrive:
    """
    User-facing differential drive helper.

    Assumptions:
    - Left and right drive each map to a motor port.
    - `zbot.motor(port)` returns a motor wrapper with .on(), .off(), .stop()
    """

    def __init__(self, zbot, left_port=1, right_port=2):
        self.zbot = zbot
        self.left = zbot.motor(int(left_port))
        self.right = zbot.motor(int(right_port))
        self.left_port = int(left_port)
        self.right_port = int(right_port)
        self._last_left = 0
        self._last_right = 0

    def stop(self):
        self.left.stop()
        self.right.stop()
        self._last_left = 0
        self._last_right = 0
        return {"left": 0, "right": 0}

    def tank(self, left_power, right_power):
        left_power = _clamp(left_power)
        right_power = _clamp(right_power)

        self.left.on(left_power)
        self.right.on(right_power)

        self._last_left = left_power
        self._last_right = right_power
        return {"left": left_power, "right": right_power}

    def drive(self, throttle, turn=0):
        throttle = _clamp(throttle)
        turn = _clamp(turn)

        left = _clamp(throttle + turn)
        right = _clamp(throttle - turn)
        return self.tank(left, right)

    def forward(self, power=50):
        power = abs(_clamp(power))
        return self.tank(power, power)

    def backward(self, power=50):
        power = abs(_clamp(power))
        return self.tank(-power, -power)

    def status(self):
        return {
            "mode": "differential",
            "left_port": self.left_port,
            "right_port": self.right_port,
            "left_power": self._last_left,
            "right_power": self._last_right,
        }
