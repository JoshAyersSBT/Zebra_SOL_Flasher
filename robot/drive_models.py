from __future__ import annotations

from dataclasses import dataclass


def _clamp(x: int | float, lo: int | float, hi: int | float):
    return lo if x < lo else hi if x > hi else x


class BaseDriveModel:
    """
    Unified drive contract used by main.py and student-facing APIs.

    All drive models should support:
      - drive(throttle, steering)
      - forward(power)
      - backward(power)
      - stop()

    Optional compatibility helpers:
      - tank(left, right)
      - steer(angle)
    """

    def drive(self, throttle: int, steering: int = 0):
        raise NotImplementedError

    def forward(self, power: int = 50):
        return self.drive(abs(int(power)), 0)

    def backward(self, power: int = 50):
        return self.drive(-abs(int(power)), 0)

    def stop(self):
        raise NotImplementedError

    def tank(self, left: int, right: int):
        """
        Optional legacy compatibility.
        Subclasses may override for native behavior.
        """
        throttle = int((int(left) + int(right)) / 2)
        steering = int((int(left) - int(right)) / 2)
        return self.drive(throttle, steering)

    def steer(self, angle: int):
        """
        Optional for platforms with explicit steering hardware.
        """
        raise NotImplementedError


@dataclass
class DifferentialDriveModel(BaseDriveModel):
    """
    Two independently-driven motors.

    Inputs:
      throttle: -100..100
      steering: -100..100
    """

    left_motor: object
    right_motor: object
    max_duty_u16: int = 65535

    def drive(self, throttle: int, steering: int = 0):
        t = int(_clamp(int(throttle), -100, 100))
        s = int(_clamp(int(steering), -100, 100))

        left_cmd = int(_clamp(t + s, -100, 100))
        right_cmd = int(_clamp(t - s, -100, 100))

        self._apply_motor(self.left_motor, left_cmd)
        self._apply_motor(self.right_motor, right_cmd)
        return {
            "model": "differential",
            "throttle": t,
            "steering": s,
            "left": left_cmd,
            "right": right_cmd,
        }

    def tank(self, left: int, right: int):
        left = int(_clamp(int(left), -100, 100))
        right = int(_clamp(int(right), -100, 100))
        self._apply_motor(self.left_motor, left)
        self._apply_motor(self.right_motor, right)
        return {
            "model": "differential",
            "mode": "tank",
            "left": left,
            "right": right,
        }

    def steer(self, angle: int):
        # Differential platforms do not have a steering servo.
        return {
            "model": "differential",
            "mode": "virtual_steer",
            "angle": int(_clamp(int(angle), -100, 100)),
        }

    def stop(self):
        self._stop_motor(self.left_motor)
        self._stop_motor(self.right_motor)

    def _apply_motor(self, motor, command: int):
        if hasattr(motor, "set_power"):
            motor.set_power(int(command))
            return

        forward = command >= 0
        mag = abs(int(command))
        duty = (mag * int(self.max_duty_u16)) // 100

        if hasattr(motor, "set"):
            motor.set(forward, duty)
            return

        if command == 0 and hasattr(motor, "stop"):
            motor.stop()
            return

        raise AttributeError("Motor object does not support set_power(), set(), or stop()")

    def _stop_motor(self, motor):
        if hasattr(motor, "stop"):
            motor.stop()
        elif hasattr(motor, "set_power"):
            motor.set_power(0)
        elif hasattr(motor, "set"):
            motor.set(True, 0)


@dataclass
class AckermannDriveModel(BaseDriveModel):
    """
    Ackermann-style drive using propulsion motor(s) plus steering servo.

    rear_motor may be:
      - a single motor object
      - a tuple/list of motor objects (all driven with same throttle)

    steering input to drive() is interpreted as an angle offset from center,
    in degrees, clamped to [-max_steer_deg, max_steer_deg].
    """

    rear_motor: object
    steering_servo: object
    max_duty_u16: int = 65535
    center_deg: int = 90
    max_steer_deg: int = 35

    def drive(self, throttle: int, steering: int = 0):
        t = int(_clamp(int(throttle), -100, 100))
        s = int(_clamp(int(steering), -self.max_steer_deg, self.max_steer_deg))

        self._apply_drive(t)
        self._write_servo(self.center_deg + s)
        return {
            "model": "ackermann",
            "throttle": t,
            "steering": s,
            "servo_angle": self.center_deg + s,
        }

    def tank(self, left: int, right: int):
        # Legacy adapter for old tank-style calls.
        # Average becomes throttle; difference becomes steering request.
        left = int(_clamp(int(left), -100, 100))
        right = int(_clamp(int(right), -100, 100))
        throttle = int((left + right) / 2)
        steering = int(((left - right) / 2) * self.max_steer_deg / 100)
        return self.drive(throttle, steering)

    def steer(self, angle: int):
        angle = int(_clamp(int(angle), self.center_deg - self.max_steer_deg, self.center_deg + self.max_steer_deg))
        self._write_servo(angle)
        return {
            "model": "ackermann",
            "mode": "steer_only",
            "servo_angle": angle,
        }

    def stop(self):
        self._apply_drive(0)

    def _apply_drive(self, command: int):
        motors = self.rear_motor
        if isinstance(motors, (tuple, list)):
            for motor in motors:
                self._apply_motor(motor, command)
        else:
            self._apply_motor(motors, command)

    def _apply_motor(self, motor, command: int):
        if hasattr(motor, "set_power"):
            motor.set_power(int(command))
            return

        forward = command >= 0
        mag = abs(int(command))
        duty = (mag * int(self.max_duty_u16)) // 100

        if hasattr(motor, "set"):
            motor.set(forward, duty)
            return

        if command == 0 and hasattr(motor, "stop"):
            motor.stop()
            return

        raise AttributeError("Motor object does not support set_power(), set(), or stop()")

    def _write_servo(self, angle: int):
        if hasattr(self.steering_servo, "write_angle"):
            self.steering_servo.write_angle(int(angle))
            return
        if hasattr(self.steering_servo, "angle"):
            self.steering_servo.angle(int(angle))
            return
        raise AttributeError("Steering servo object does not support write_angle() or angle()")


class DriveSystem:
    """
    Thin wrapper used by main.py so runtime code can stay consistent even if
    the underlying drive model changes.
    """

    def __init__(self, model: BaseDriveModel):
        self.model = model

    def drive(self, throttle: int, steering: int = 0):
        return self.model.drive(throttle, steering)

    def forward(self, power: int = 50):
        return self.model.forward(power)

    def backward(self, power: int = 50):
        return self.model.backward(power)

    def stop(self):
        return self.model.stop()

    def tank(self, left: int, right: int):
        return self.model.tank(left, right)

    def steer(self, angle: int):
        return self.model.steer(angle)


def create_drive_system(
    drive_mode: str,
    *,
    left_motor=None,
    right_motor=None,
    rear_motor=None,
    steering_servo=None,
    max_duty_u16: int = 65535,
    center_deg: int = 90,
    max_steer_deg: int = 35,
):
    mode = str(drive_mode or "differential").strip().lower()

    if mode in ("diff", "differential", "tank"):
        if left_motor is None or right_motor is None:
            raise ValueError("Differential drive requires left_motor and right_motor")
        return DriveSystem(
            DifferentialDriveModel(
                left_motor=left_motor,
                right_motor=right_motor,
                max_duty_u16=max_duty_u16,
            )
        )

    if mode in ("ackermann", "ackerman", "car"):
        if rear_motor is None:
            if left_motor is not None and right_motor is not None:
                rear_motor = (left_motor, right_motor)
            else:
                raise ValueError("Ackermann drive requires rear_motor or left_motor/right_motor")
        if steering_servo is None:
            raise ValueError("Ackermann drive requires steering_servo")
        return DriveSystem(
            AckermannDriveModel(
                rear_motor=rear_motor,
                steering_servo=steering_servo,
                max_duty_u16=max_duty_u16,
                center_deg=center_deg,
                max_steer_deg=max_steer_deg,
            )
        )

    raise ValueError("Unsupported drive mode: {}".format(drive_mode))
