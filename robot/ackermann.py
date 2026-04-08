class AckermannDrive:
    """
    User-facing Ackermann steering helper.

    Assumptions:
    - Propulsion comes from one motor port.
    - Steering is handled by the robot steering servo.
    - `zbot.motor(port)` returns a motor wrapper with .on(), .off(), .stop()
    - `zbot.api.set_steering(angle)` is available from the runtime
    """

    def __init__(self, zbot, drive_motor_port=1, center_angle=90):
        self.zbot = zbot
        self.drive_motor = zbot.motor(int(drive_motor_port))
        self.center_angle = int(center_angle)
        self._last_throttle = 0
        self._last_angle = self.center_angle

    def _clamp_power(self, power):
        power = int(power)
        if power > 100:
            return 100
        if power < -100:
            return -100
        return power

    def forward(self, power=50):
        power = abs(self._clamp_power(power))
        self._last_throttle = power
        self.drive_motor.on(power)
        return power

    def backward(self, power=50):
        power = abs(self._clamp_power(power))
        self._last_throttle = -power
        self.drive_motor.on(-power)
        return -power

    def stop(self):
        self._last_throttle = 0
        self.drive_motor.stop()
        return 0

    def steer(self, angle):
        angle = int(angle)
        self._last_angle = angle
        self.zbot.api.set_steering(angle)
        return angle

    def steer_center(self):
        return self.steer(self.center_angle)

    def drive(self, throttle, steering_angle=None):
        throttle = self._clamp_power(throttle)
        if steering_angle is None:
            steering_angle = self._last_angle
        else:
            steering_angle = int(steering_angle)

        if throttle > 0:
            self.forward(throttle)
        elif throttle < 0:
            self.backward(-throttle)
        else:
            self.stop()

        self.steer(steering_angle)
        return {
            "mode": "ackermann",
            "throttle": throttle,
            "steering_angle": steering_angle,
        }

    def status(self):
        return {
            "mode": "ackermann",
            "throttle": self._last_throttle,
            "steering_angle": self._last_angle,
            "center_angle": self.center_angle,
        }
