import time
import uasyncio as asyncio
from machine import I2C, Pin

import robot.config as robot_config

from robot.motors import Motor
from robot.servo import Servo
from robot.ble_teleop import BleTeleop
from robot.mpu6050 import MPU6050
from robot.oled_status import OledStatus
from robot.tca9548a import TCA9548A
from robot.sensor_hub import SensorHub
from robot.motor_feedback import MotorFeedback
from robot.motor_scan import MotorScanner
from robot.debug_io import (
    info,
    warn,
    error,
    diag,
    state,
    set_ble_sink,
    replay_boot_log,
)

SAFE_MODE_PIN = 0
BOOT_GRACE_SECONDS = 3
DEFAULT_STEER_CENTER_DEG = 90
DEFAULT_STEER_RANGE_DEG = 45

API = None
zbot = None


def _cfg(name, default=None):
    return getattr(robot_config, name, default)


LEFT_PWM = _cfg("LEFT_PWM")
LEFT_DIR = _cfg("LEFT_DIR")
LEFT_ENC = _cfg("LEFT_ENC")

RIGHT_PWM = _cfg("RIGHT_PWM")
RIGHT_DIR = _cfg("RIGHT_DIR")
RIGHT_ENC = _cfg("RIGHT_ENC")

MOTOR_PWM_FREQ_HZ = _cfg("MOTOR_PWM_FREQ_HZ", 20000)
MOTOR_MAX_DUTY_U16 = _cfg("MOTOR_MAX_DUTY_U16", 40000)

STEER_SERVO_GPIO = _cfg("STEER_SERVO_GPIO", 18)
SERVO_FREQ_HZ = _cfg("SERVO_FREQ_HZ", 50)
SERVO_MIN_US = _cfg("SERVO_MIN_US", 500)
SERVO_MAX_US = _cfg("SERVO_MAX_US", 2500)
SERVO_CENTER_DEG = _cfg("SERVO_CENTER_DEG", 90)

TCA_I2C_ID = _cfg("TCA_I2C_ID", 0)
TCA_SDA_GPIO = _cfg("TCA_SDA_GPIO", 21)
TCA_SCL_GPIO = _cfg("TCA_SCL_GPIO", 22)
TCA_I2C_FREQ = _cfg("TCA_I2C_FREQ", 400000)
TCA_ADDR = _cfg("TCA_ADDR", 0x70)

MPU_ADDR = _cfg("MPU_ADDR", 0x68)
MPU_CHANNEL = _cfg("MPU_CHANNEL", 7)
MPU_PERIOD_MS = _cfg("MPU_PERIOD_MS", 10)

OLED_ADDR = _cfg("OLED_ADDR", 0x3C)
OLED_CHANNEL = _cfg("OLED_CHANNEL", 0)
OLED_WIDTH = _cfg("OLED_WIDTH", 128)
OLED_HEIGHT = _cfg("OLED_HEIGHT", 64)

SENSOR_SCAN_PERIOD_MS = _cfg("SENSOR_SCAN_PERIOD_MS", 100)
SENSOR_PORT_MODES = _cfg("SENSOR_PORT_MODES", {})

MOTOR_PORT_MAP = _cfg("MOTOR_PORT_MAP", {})
ACTIVE_MOTOR_PORTS = _cfg("ACTIVE_MOTOR_PORTS", tuple(sorted(MOTOR_PORT_MAP.keys())))

MOTOR_SCAN_POWER = _cfg("MOTOR_SCAN_POWER", 25)
MOTOR_SCAN_PULSE_MS = _cfg("MOTOR_SCAN_PULSE_MS", 250)
MOTOR_SCAN_PERIOD_MS = _cfg("MOTOR_SCAN_PERIOD_MS", 1500)
MOTOR_FEEDBACK_PERIOD_MS = _cfg("MOTOR_FEEDBACK_PERIOD_MS", 200)

# Optional future-facing config. Falls back cleanly to the legacy dedicated steer servo.
SERVO_PORT_MAP = _cfg("SERVO_PORT_MAP", None)
STEER_SERVO_PORT = _cfg("STEER_SERVO_PORT", 1)


def _build_default_servo_port_map():
    return {
        int(STEER_SERVO_PORT): {
            "name": "STEER",
            "gpio": int(STEER_SERVO_GPIO),
            "freq_hz": int(SERVO_FREQ_HZ),
            "min_us": int(SERVO_MIN_US),
            "max_us": int(SERVO_MAX_US),
            "center_deg": int(SERVO_CENTER_DEG),
            "role": "steering",
        }
    }


if SERVO_PORT_MAP is None:
    SERVO_PORT_MAP = _build_default_servo_port_map()


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class RuntimeDriveBridge:
    """
    Generic boot/runtime drive bridge used by BLE teleop and legacy calls.

    This intentionally does not define the student's drive model.
    It only provides a minimal motion bridge so:
      - BLE teleop can command the robot
      - legacy helpers still have a neutral fallback

    Semantics:
      throttle: -100..100
      turn:     -100..100

    Behavior:
      - all propulsion motors receive the same signed throttle
      - steering servo is mapped around center using turn
    """
    def __init__(self, api, propulsion_ports=None, steer_center_deg=DEFAULT_STEER_CENTER_DEG, steer_range_deg=DEFAULT_STEER_RANGE_DEG):
        self.api = api
        ports = propulsion_ports if propulsion_ports is not None else ACTIVE_MOTOR_PORTS
        self.propulsion_ports = tuple(int(p) for p in ports)
        self.steer_center_deg = int(steer_center_deg)
        self.steer_range_deg = int(steer_range_deg)

    def drive(self, throttle: int, turn: int):
        throttle = _clamp(int(throttle), -100, 100)
        turn = _clamp(int(turn), -100, 100)

        for port in self.propulsion_ports:
            try:
                self.api.set_motor(port, throttle)
            except Exception as e:
                error("RUNTIME_DRIVE_PORT_{}".format(port), e)

        steer_angle = self.steer_center_deg + ((turn * self.steer_range_deg) // 100)
        try:
            self.api.set_steering(steer_angle)
        except Exception as e:
            error("RUNTIME_DRIVE_STEER", e)

    def stop(self):
        for port in self.propulsion_ports:
            try:
                self.api.stop_motor(port)
            except Exception as e:
                error("RUNTIME_DRIVE_STOP_{}".format(port), e)


class RobotAPI:
    """
    Shared low-level runtime API exposed to user programs and internal services.

    This API intentionally avoids hard-coding a student-facing drive model.
    User code should compose motion behavior in robot modules such as:
      - robot.ackermann
      - robot.differential
    """

    def __init__(self):
        self.status = {
            "boot": {"state": "init", "safe_mode": False},
            "system": {"heartbeat": 0, "ready": False},
            "motors": {},
            "servos": {},
            "steering": {},
            "imu": {},
            "sensors": {},
            "services": {},
            "user": {"running": False, "last_error": None},
        }
        self.handles = {}
        self.tasks = {}
        self._oled_user_hold_until = 0

    def register_handle(self, name, value):
        self.handles[name] = value
        return value

    def get_handle(self, name, default=None):
        return self.handles.get(name, default)

    def register_task(self, name, task):
        self.tasks[name] = task
        return task

    def set_ready(self, ready=True):
        self.status["system"]["ready"] = bool(ready)

    def get_status(self):
        return self.status

    def get_services(self):
        return self.status.get("services", {})

    def mark_user_display(self, hold_ms=2500):
        try:
            self._oled_user_hold_until = time.ticks_add(time.ticks_ms(), int(hold_ms))
        except Exception:
            self._oled_user_hold_until = 0

    def user_display_active(self):
        try:
            return time.ticks_diff(self._oled_user_hold_until, time.ticks_ms()) > 0
        except Exception:
            return False

    def list_motor_ports(self):
        return sorted(self.handles.get("motors", {}).keys())

    def get_motor_ports(self):
        return self.list_motor_ports()

    def get_motor_map(self):
        return self.handles.get("motor_port_map", {})

    def get_motor_status(self):
        return self.status.get("motors", {})

    def get_motor_feedback(self):
        return self.status.get("motor_feedback", {})

    def get_servo_ports(self):
        return sorted(self.handles.get("servos", {}).keys())

    def get_servo_map(self):
        return self.handles.get("servo_port_map", {})

    def get_servo_status(self):
        return self.status.get("servos", {})

    def _power_to_duty(self, power):
        mag = abs(int(power))
        if mag > 100:
            mag = 100
        return (mag * MOTOR_MAX_DUTY_U16) // 100

    def set_motor(self, port, power):
        motors = self.handles.get("motors", {})
        motor = motors.get(port)
        if motor is None:
            raise ValueError("unknown motor port {}".format(port))

        power = int(power)

        if hasattr(motor, "set_power"):
            motor.set_power(power)
        else:
            if power == 0:
                if hasattr(motor, "stop"):
                    motor.stop()
                else:
                    motor.set(True, 0)
            else:
                forward = power > 0
                duty_u16 = self._power_to_duty(power)
                motor.set(forward, duty_u16)

        self.status["motors"][port] = {
            "power": power,
            "duty_u16": self._power_to_duty(power),
            "ts_ms": time.ticks_ms(),
            "name": self.get_motor_map().get(port, {}).get("name", "M{}".format(port)),
        }
        return self.status["motors"][port]

    def stop_motor(self, port):
        motors = self.handles.get("motors", {})
        motor = motors.get(port)
        if motor is None:
            raise ValueError("unknown motor port {}".format(port))

        if hasattr(motor, "stop"):
            motor.stop()
        else:
            motor.set(True, 0)

        self.status["motors"][port] = {
            "power": 0,
            "duty_u16": 0,
            "ts_ms": time.ticks_ms(),
            "name": self.get_motor_map().get(port, {}).get("name", "M{}".format(port)),
        }
        return self.status["motors"][port]

    def stop_all(self):
        motors = self.handles.get("motors", {})
        for port in motors:
            try:
                self.stop_motor(port)
            except Exception as e:
                error("STOP_MOTOR_{}".format(port), e)

    def set_servo(self, port, angle):
        servos = self.handles.get("servos", {})
        servo = servos.get(int(port))
        if servo is None:
            raise ValueError("unknown servo port {}".format(port))

        angle = int(angle)

        if hasattr(servo, "write_angle"):
            servo.write_angle(angle)
        elif hasattr(servo, "angle"):
            servo.angle(angle)
        else:
            raise AttributeError("servo object has no write_angle/angle")

        cfg = self.get_servo_map().get(int(port), {})
        item = {
            "angle": angle,
            "ts_ms": time.ticks_ms(),
            "name": cfg.get("name", "S{}".format(port)),
        }
        self.status["servos"][int(port)] = item
        return item

    def center_servo(self, port):
        cfg = self.get_servo_map().get(int(port), {})
        center_deg = int(cfg.get("center_deg", 90))
        return self.set_servo(int(port), center_deg)

    def set_steering(self, angle):
        steer = self.handles.get("steer")
        if steer is None:
            raise RuntimeError("steering unavailable")

        angle = int(angle)
        if hasattr(steer, "write_angle"):
            steer.write_angle(angle)
        elif hasattr(steer, "angle"):
            steer.angle(angle)
        else:
            raise AttributeError("steering object has no write_angle/angle")

        self.status["steering"] = {
            "angle": angle,
            "ts_ms": time.ticks_ms(),
        }

        steer_port = self.handles.get("steer_port", None)
        if steer_port is not None:
            try:
                self.status["servos"][int(steer_port)] = {
                    "angle": angle,
                    "ts_ms": time.ticks_ms(),
                    "name": self.get_servo_map().get(int(steer_port), {}).get("name", "STEER"),
                }
            except Exception:
                pass

        return self.status["steering"]

    def publish_sensor(self, name, value, meta=None):
        item = {
            "value": value,
            "ts_ms": time.ticks_ms(),
        }
        if meta is not None:
            item["meta"] = meta
        self.status["sensors"][name] = item
        return item

    def get_sensor(self, name, default=None):
        return self.status.get("sensors", {}).get(name, default)

    def get_sensor_snapshot(self):
        return self.status.get("sensors", {})

    def get_imu(self):
        return self.status.get("imu", {})

    def refresh_imu_snapshot(self):
        imu = self.handles.get("imu")
        if imu is None:
            return None

        try:
            if hasattr(imu, "read_scaled"):
                reading = imu.read_scaled()
            elif hasattr(imu, "read"):
                reading = imu.read()
            else:
                raise AttributeError("imu object has no read_scaled/read")

            self.status["imu"] = {
                "value": reading,
                "ts_ms": time.ticks_ms(),
            }
            return self.status["imu"]
        except Exception as e:
            self.status["imu"] = {
                "error": repr(e),
                "ts_ms": time.ticks_ms(),
            }
            return None

    def notify(self, msg):
        teleop = self.handles.get("teleop")
        if teleop is not None:
            try:
                teleop.notify_line(msg)
                return True
            except Exception as e:
                error("API_NOTIFY", e)
        return False

    def show_lines(self, *lines):
        oled = self.handles.get("oled")
        if oled is None:
            return False
        try:
            self.mark_user_display(hold_ms=5000)
            oled.show_lines(*lines)
            return True
        except Exception as e:
            error("API_OLED", e)
            return False

    # Compatibility shims so old student code fails less harshly.
    def drive(self, throttle, turn=0):
        bridge = self.handles.get("runtime_drive")
        if bridge is None:
            raise RuntimeError("runtime drive bridge unavailable")
        bridge.drive(int(throttle), int(turn))

    def stop(self):
        self.stop_all()

    def display(self, line1="", line2="", line3="", line4=""):
        lines = [str(x) for x in (line1, line2, line3, line4) if str(x) != ""]
        return self.show_lines(*lines)

    def sensor(self, port):
        return _ZBotSensor(self, port)

    def motor(self, port, motor_type="DC"):
        return _ZBotMotor(self, port, motor_type)

    def servo(self, port=1):
        return _ZBotServo(self, port)


class _ZBotSensor:
    def __init__(self, api, port):
        self.api = api
        self.port = int(port)

    def _find_snapshot_value(self):
        if self.api is None:
            return None

        sensors = self.api.get_sensor_snapshot()

        key = "tof_port_{}".format(self.port)
        item = sensors.get(key)
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, (int, float)):
                return int(value)

        fallback_keys = (
            "port{}_tof".format(self.port),
            "tof_{}".format(self.port),
            "sensor_port_{}".format(self.port),
        )

        for key in fallback_keys:
            item = sensors.get(key)
            if isinstance(item, dict):
                value = item.get("value")
                if isinstance(value, (int, float)):
                    return int(value)

        for key, item in sensors.items():
            if not isinstance(item, dict):
                continue

            value = item.get("value")
            if not isinstance(value, (int, float)):
                continue

            meta = item.get("meta", {})
            key_s = str(key).lower()
            meta_s = str(meta).lower()

            if "tof" in key_s and str(self.port) in key_s:
                return int(value)

            if "tof" in meta_s and str(self.port) in meta_s:
                return int(value)

        return None

    def read(self):
        return self._find_snapshot_value()


class _ZBotServo:
    def __init__(self, api, port=1):
        self.api = api
        self.port = int(port)

    def angle(self, deg):
        if self.api is None:
            return False
        self.api.set_servo(self.port, int(deg))
        return True

    def write_angle(self, deg):
        return self.angle(deg)

    def center(self, center_angle=None):
        if self.api is None:
            return False
        if center_angle is None:
            self.api.center_servo(self.port)
        else:
            self.api.set_servo(self.port, int(center_angle))
        return True


class _ZBotMotor:
    def __init__(self, api, port, motor_type="DC"):
        self.api = api
        self.port = int(port)
        self.motor_type = str(motor_type)
        self._publish_meta()

    def _publish_meta(self):
        if self.api is None:
            return
        try:
            if "student_motors" not in self.api.status:
                self.api.status["student_motors"] = {}
            self.api.status["student_motors"][self.port] = {
                "type": self.motor_type,
                "ts_ms": time.ticks_ms(),
            }
        except Exception:
            pass

    def on(self, power=50):
        if self.api is None:
            return False
        self._publish_meta()
        self.api.set_motor(self.port, int(power))
        return True

    def off(self):
        if self.api is None:
            return False
        self.api.stop_motor(self.port)
        return True

    def stop(self):
        return self.off()

    def speed(self, power):
        return self.on(power)

    def set(self, power):
        return self.on(power)

    def value(self):
        if self.api is None:
            return None
        try:
            return self.api.get_motor_status().get(self.port, {})
        except Exception:
            return None


class ZBot:
    """
    Student-facing neutral wrapper.

    This wrapper exposes primitives only. Drive-model decisions belong in
    user modules (robot.ackermann, robot.differential, etc).
    """
    def __init__(self, api=None):
        self.api = api
        self._motor_wrappers = {}
        self._servo_wrappers = {}

    def bind(self, api):
        self.api = api
        return self

    def ready(self):
        return self.api is not None and bool(self.api.status["system"].get("ready", False))

    def stop(self):
        if self.api is None:
            return False
        self.api.stop_all()
        return True

    def steer(self, angle):
        if self.api is None:
            return False
        self.api.set_steering(int(angle))
        return True

    def display(self, line1="", line2="", line3="", line4=""):
        if self.api is None:
            return False
        return self.api.display(line1, line2, line3, line4)

    def say(self, line1="", line2="", line3="", line4=""):
        return self.display(line1, line2, line3, line4)

    def notify(self, text):
        if self.api is None:
            return False
        return self.api.notify(str(text))

    def servo(self, port=1):
        key = int(port)
        if self.api is None:
            return _ZBotServo(None, port)

        if key not in self._servo_wrappers:
            self._servo_wrappers[key] = _ZBotServo(self.api, port)

        return self._servo_wrappers[key]

    def motor(self, port, motor_type="DC"):
        key = (int(port), str(motor_type))
        if self.api is None:
            return _ZBotMotor(None, port, motor_type)

        if key not in self._motor_wrappers:
            self._motor_wrappers[key] = _ZBotMotor(self.api, port, motor_type)

        return self._motor_wrappers[key]

    def motors(self, port, motor_type="DC"):
        return self.motor(port, motor_type)

    def sensor(self, port):
        if self.api is None:
            return _ZBotSensor(None, port)
        return _ZBotSensor(self.api, port)

    def tof(self, port):
        s = self.sensor(port)
        return s.read()

    def status(self):
        if self.api is None:
            return {}
        return self.api.get_status()

    def sensors(self):
        if self.api is None:
            return {}
        return self.api.get_sensor_snapshot()

    def imu(self):
        if self.api is None:
            return {}
        return self.api.get_imu()

    def motor_status(self):
        if self.api is None:
            return {}
        return self.api.get_motor_status()

    def motor_feedback(self):
        if self.api is None:
            return {}
        return self.api.get_motor_feedback()

    def servo_status(self):
        if self.api is None:
            return {}
        return self.api.get_servo_status()

    # Backward-compatible motion shims. They use the neutral runtime bridge.
    def drive(self, throttle, turn=0):
        if self.api is None:
            return False
        self.api.drive(int(throttle), int(turn))
        return True

    def forward(self, power=50):
        return self.drive(abs(int(power)), 0)

    def backward(self, power=50):
        return self.drive(-abs(int(power)), 0)

    def tank(self, left_power, right_power):
        left_power = int(left_power)
        right_power = int(right_power)
        throttle = (left_power + right_power) // 2
        turn = (left_power - right_power) // 2
        return self.drive(throttle, turn)


def get_api():
    return API


def get_zbot():
    return zbot


def _boot_oled(api, line1, line2="", line3=""):
    try:
        if api is None:
            return

        if api.status.get("boot", {}).get("state") == "complete":
            return

        if api.user_display_active():
            return

        oled = api.get_handle("oled")
        if oled is not None and getattr(oled, "available", False):
            oled.show_lines(line1, line2, line3)
    except Exception as e:
        error("BOOT_OLED", e)


def _format_tof_line(api):
    sensors = api.status.get("sensors", {})
    for port in range(1, 7):
        key = "tof_port_{}".format(port)
        item = sensors.get(key)
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, (int, float)):
                return "TOF{}: {}mm".format(port, int(value))
    return "TOF: --"


def _format_user_line(api):
    user = api.status.get("user", {})
    if user.get("last_error"):
        return "User ERR"
    if user.get("running"):
        return "User: running"
    return "User: idle"


def _format_ble_line(api):
    teleop = api.get_handle("teleop")
    if teleop is None:
        return "BLE: off"
    try:
        if teleop._conn_handle is None:
            return "BLE: waiting"
        return "BLE: connected"
    except Exception:
        return "BLE: ?"


def _sensor_port_line(api, port):
    sensors = api.status.get("sensors", {})

    candidates = [
        "tof_port_{}".format(port),
        "port{}_tof".format(port),
        "tof_{}".format(port),
        "sensor_port_{}".format(port),
        "color_port_{}".format(port),
        "port{}_color".format(port),
    ]

    for key in candidates:
        item = sensors.get(key)
        if not isinstance(item, dict):
            continue

        value = item.get("value")
        meta = item.get("meta", {})
        key_l = key.lower()
        meta_l = str(meta).lower()

        if isinstance(value, (int, float)) and ("tof" in key_l or "tof" in meta_l):
            return "P{} TOF {}mm".format(port, int(value))

        if isinstance(value, dict):
            if "r" in value and "g" in value and "b" in value:
                return "P{} RGB".format(port)

        if isinstance(value, (int, float)):
            return "P{} {}".format(port, int(value))

        if value is not None:
            return "P{} {}".format(port, str(value)[:10])

    for key, item in sensors.items():
        if not isinstance(item, dict):
            continue
        key_l = str(key).lower()
        meta_l = str(item.get("meta", {})).lower()
        if str(port) not in key_l and str(port) not in meta_l:
            continue

        value = item.get("value")
        if isinstance(value, (int, float)) and ("tof" in key_l or "tof" in meta_l):
            return "P{} TOF {}mm".format(port, int(value))
        if value is not None:
            return "P{} {}".format(port, str(value)[:10])

    mode = None
    try:
        mode = SENSOR_PORT_MODES.get(port)
    except Exception:
        mode = None

    if mode:
        return "P{} {}".format(port, str(mode))
    return "P{} empty".format(port)


def _sensor_overview_pages(api):
    pages = []

    user = api.status.get("user", {})
    if user.get("last_error"):
        err_name = str(user.get("last_error"))[:18]
        pages.append(("ZebraBot", "User Error", err_name))
    else:
        pages.append(("ZebraBot", "No user code", "Sensor monitor"))

    ports = [1, 2, 3, 4, 5, 6]
    for i in range(0, len(ports), 3):
        chunk = ports[i:i + 3]
        lines = ["Sensors"]
        for port in chunk:
            lines.append(_sensor_port_line(api, port))
        pages.append(tuple(lines))

    imu = api.status.get("imu", {})
    if imu:
        if "error" in imu:
            pages.append(("IMU", "error", str(imu.get("error"))[:18]))
        else:
            value = imu.get("value")
            pages.append(("IMU", str(value)[:20], ""))

    return pages


async def _api_housekeeping_task(api):
    while True:
        try:
            motor_feedback = api.get_handle("motor_feedback")
            if motor_feedback is not None and hasattr(motor_feedback, "snapshot"):
                api.status["motor_feedback"] = motor_feedback.snapshot()
        except Exception:
            pass

        try:
            sensor_hub = api.get_handle("sensor_hub")
            if sensor_hub is not None and hasattr(sensor_hub, "snapshot"):
                api.status["sensors"] = sensor_hub.snapshot()
        except Exception:
            pass

        try:
            api.refresh_imu_snapshot()
        except Exception:
            pass

        await asyncio.sleep_ms(100)


async def _oled_status_task(api):
    last_lines = None
    page_idx = 0
    last_page_ms = 0
    page_period_ms = 1400

    while True:
        try:
            oled = api.get_handle("oled")
            if oled is None or not getattr(oled, "available", False):
                await asyncio.sleep_ms(500)
                continue

            if api.user_display_active():
                await asyncio.sleep_ms(200)
                continue

            user = api.status.get("user", {})
            fallback_mode = (not user.get("running")) or bool(user.get("last_error"))

            if user.get("running") and not user.get("last_error"):
                await asyncio.sleep_ms(250)
                continue

            if fallback_mode:
                pages = _sensor_overview_pages(api)
                now = time.ticks_ms()
                if not pages:
                    lines = ("ZebraBot", "No sensors", "")
                else:
                    if (
                        last_page_ms == 0
                        or time.ticks_diff(now, last_page_ms) >= page_period_ms
                    ):
                        page_idx = (page_idx + 1) % len(pages)
                        last_page_ms = now
                    lines = pages[page_idx]
            else:
                line1 = "ZebraBot Ready" if api.status["system"].get("ready") else "ZebraBot Boot"
                line2 = _format_ble_line(api)
                line3 = _format_user_line(api)
                line4 = _format_tof_line(api)
                lines = (line1, line2, line3, line4)
                page_idx = 0
                last_page_ms = 0

            if lines != last_lines:
                oled.show_lines(*lines)
                last_lines = lines

        except Exception as e:
            error("OLED_STATUS_TASK", e)

        await asyncio.sleep_ms(250)


async def _boot_complete_message(api):
    _boot_oled(api, "ZebraBot", "Boot Complete", "Starting tasks...")
    await asyncio.sleep_ms(1200)


async def _run_user_program(api):
    try:
        import user_main
    except Exception as e:
        error("USER_IMPORT", e)
        api.status["user"]["last_error"] = repr(e)

        teleop = api.get_handle("teleop")
        if teleop is not None:
            try:
                teleop.notify_error("USER_IMPORT", e)
            except Exception:
                pass
        return

    user_fn = getattr(user_main, "main", None)
    if user_fn is None:
        warn("USER: user_main.main missing")
        api.status["user"]["last_error"] = "user_main.main missing"

        teleop = api.get_handle("teleop")
        if teleop is not None:
            try:
                teleop.notify_line("ERR USER main() missing")
            except Exception:
                pass
        return

    api.status["user"]["running"] = True
    api.status["user"]["last_error"] = None

    teleop = api.get_handle("teleop")
    if teleop is not None:
        try:
            teleop.notify_line("INFO USER main starting")
        except Exception:
            pass

    try:
        argc = None
        try:
            argc = user_fn.__code__.co_argcount
        except Exception:
            pass

        if argc == 0:
            await user_fn()
        else:
            await user_fn(zbot)

    except Exception as e:
        api.status["user"]["last_error"] = repr(e)
        error("USER_MAIN", e)

        if teleop is not None:
            try:
                teleop.notify_error("USER_MAIN", e)
            except Exception:
                pass

    finally:
        api.status["user"]["running"] = False

        if teleop is not None:
            try:
                teleop.notify_line("INFO USER main stopped")
            except Exception:
                pass


async def main():
    global API
    global zbot

    teleop = None
    sensor_hub = None
    imu = None
    oled = None
    mux = None
    base_i2c = None
    runtime_drive = None
    steer = None

    motors = {}
    servos = {}
    motor_feedback = None
    motor_scanner = None

    api = RobotAPI()
    API = api
    zbot = ZBot(api)

    info("BOOT: starting robot init")
    state("BOOT", "start")
    api.status["boot"]["state"] = "starting"

    try:
        for port in sorted(MOTOR_PORT_MAP.keys()):
            cfg = MOTOR_PORT_MAP[port]
            motors[port] = Motor(
                cfg["pwm"],
                cfg["dir"],
                pwm_freq_hz=MOTOR_PWM_FREQ_HZ,
            )
            diag(
                "MOTOR_PORT {} {} pwm={} dir={} enc={}".format(
                    port,
                    cfg.get("name", "M{}".format(port)),
                    cfg.get("pwm"),
                    cfg.get("dir"),
                    cfg.get("enc"),
                )
            )
            api.status["motors"][port] = {
                "power": 0,
                "duty_u16": 0,
                "name": cfg.get("name", "M{}".format(port)),
                "enc": cfg.get("enc"),
                "ts_ms": time.ticks_ms(),
            }

        api.register_handle("motors", motors)
        api.register_handle("motor_port_map", dict(MOTOR_PORT_MAP))

        info("BOOT: motors initialized")
        diag("DRIVE LEFT PWM={} DIR={} ENC={}".format(LEFT_PWM, LEFT_DIR, LEFT_ENC))
        diag("DRIVE RIGHT PWM={} DIR={} ENC={}".format(RIGHT_PWM, RIGHT_DIR, RIGHT_ENC))
        state("BOOT", "motors_ok")

    except Exception as e:
        error("MOTOR_INIT", e)
        raise

    try:
        for port in sorted(SERVO_PORT_MAP.keys()):
            cfg = SERVO_PORT_MAP[port]
            servos[port] = Servo(
                int(cfg.get("gpio", STEER_SERVO_GPIO)),
                freq_hz=int(cfg.get("freq_hz", SERVO_FREQ_HZ)),
                min_us=int(cfg.get("min_us", SERVO_MIN_US)),
                max_us=int(cfg.get("max_us", SERVO_MAX_US)),
            )
            api.status["servos"][port] = {
                "angle": None,
                "ts_ms": time.ticks_ms(),
                "name": cfg.get("name", "S{}".format(port)),
            }
            diag(
                "SERVO_PORT {} {} gpio={} freq={} min_us={} max_us={}".format(
                    port,
                    cfg.get("name", "S{}".format(port)),
                    cfg.get("gpio", STEER_SERVO_GPIO),
                    cfg.get("freq_hz", SERVO_FREQ_HZ),
                    cfg.get("min_us", SERVO_MIN_US),
                    cfg.get("max_us", SERVO_MAX_US),
                )
            )

        api.register_handle("servos", servos)
        api.register_handle("servo_port_map", dict(SERVO_PORT_MAP))

        steer_port = None
        for port, cfg in SERVO_PORT_MAP.items():
            if str(cfg.get("role", "")).lower() == "steering":
                steer_port = int(port)
                break
        if steer_port is None and servos:
            steer_port = sorted(servos.keys())[0]

        if steer_port is None:
            raise RuntimeError("no servo ports available")

        steer = servos[steer_port]
        api.register_handle("steer", steer)
        api.register_handle("steer_port", int(steer_port))
        api.status["steering"] = {"angle": None, "ts_ms": time.ticks_ms()}
        info("BOOT: servo(s) initialized")
        state("BOOT", "servo_ok")

        try:
            api.center_servo(steer_port)
            api.status["steering"] = {
                "angle": int(SERVO_PORT_MAP.get(steer_port, {}).get("center_deg", SERVO_CENTER_DEG)),
                "ts_ms": time.ticks_ms(),
            }
        except Exception as center_err:
            error("SERVO_CENTER_INIT", center_err)

    except Exception as e:
        error("SERVO_INIT", e)
        raise

    try:
        runtime_drive = RuntimeDriveBridge(api, propulsion_ports=ACTIVE_MOTOR_PORTS)
        api.register_handle("runtime_drive", runtime_drive)
    except Exception as e:
        error("RUNTIME_DRIVE_INIT", e)

    try:
        base_i2c = I2C(
            TCA_I2C_ID,
            sda=Pin(TCA_SDA_GPIO),
            scl=Pin(TCA_SCL_GPIO),
            freq=TCA_I2C_FREQ,
        )
        mux = TCA9548A(base_i2c, addr=TCA_ADDR)
        api.register_handle("base_i2c", base_i2c)
        api.register_handle("mux", mux)
        info("BOOT: TCA9548A initialized")
        diag(
            "TCA BUS sda={} scl={} addr={}".format(
                TCA_SDA_GPIO, TCA_SCL_GPIO, hex(TCA_ADDR)
            )
        )
        state("BOOT", "mux_ok")

        try:
            devices = base_i2c.scan()
            api.status["services"]["i2c"] = {
                "bus": TCA_I2C_ID,
                "devices": devices,
                "ts_ms": time.ticks_ms(),
            }
            diag("I2C_BASE {}".format(",".join(hex(d) for d in devices) if devices else "none"))
        except Exception as scan_err:
            error("I2C_SCAN", scan_err)

    except Exception as e:
        error("TCA_INIT", e)

    try:
        imu = MPU6050(
            i2c_id=TCA_I2C_ID,
            sda_gpio=TCA_SDA_GPIO,
            scl_gpio=TCA_SCL_GPIO,
            freq=TCA_I2C_FREQ,
            addr=MPU_ADDR,
            mux=mux,
            mux_channel=MPU_CHANNEL,
        )
        api.register_handle("imu", imu)
        info("BOOT: MPU-6050 initialized")
        diag("MPU CH={} ADDR={}".format(MPU_CHANNEL, hex(MPU_ADDR)))
        state("BOOT", "mpu_ok")
    except Exception as e:
        error("MPU_INIT", e)
        imu = None
        warn("BOOT: MPU unavailable")

    try:
        oled = OledStatus(
            i2c_id=TCA_I2C_ID,
            sda_gpio=TCA_SDA_GPIO,
            scl_gpio=TCA_SCL_GPIO,
            width=OLED_WIDTH,
            height=OLED_HEIGHT,
            addr=OLED_ADDR,
            mux=mux,
            mux_channel=OLED_CHANNEL,
        )
        if oled and oled.available:
            api.register_handle("oled", oled)
            oled.show_lines("ZebraBot", "Booting...", "OLED online")
            info("BOOT: OLED initialized")
            diag("OLED CH={} ADDR={}".format(OLED_CHANNEL, hex(OLED_ADDR)))
            state("BOOT", "oled_ok")
        else:
            info("BOOT: OLED unavailable")
            state("BOOT", "oled_unavailable")
    except Exception as e:
        error("OLED_INIT", e)
        oled = None

    _boot_oled(api, "ZebraBot", "Starting BLE", "")
    try:
        teleop = BleTeleop(
            drive=runtime_drive,
            steering=steer,
            imu=imu,
            imu_period_ms=MPU_PERIOD_MS,
            oled=oled,
        )
        api.register_handle("teleop", teleop)
        set_ble_sink(teleop)
        replay_boot_log()

        info("BOOT: BLE teleop initialized")
        state("BOOT", "ble_ok")
    except Exception as e:
        teleop = None
        error("BLE_INIT", e)
        _boot_oled(api, "ZebraBot", "BLE init fail", str(type(e).__name__))
        warn("BOOT: continuing without BLE")
        state("BOOT", "ble_failed")

    try:
        notify_fn = teleop.notify_line if teleop is not None else None
        sensor_hub = SensorHub(
            i2c_id=TCA_I2C_ID,
            sda_gpio=TCA_SDA_GPIO,
            scl_gpio=TCA_SCL_GPIO,
            freq=TCA_I2C_FREQ,
            mux=mux,
            port_modes=SENSOR_PORT_MODES,
            notify_fn=notify_fn,
            scan_period_ms=SENSOR_SCAN_PERIOD_MS,
        )
        api.register_handle("sensor_hub", sensor_hub)
        info("BOOT: SensorHub initialized")
        state("BOOT", "sensorhub_ok")
    except Exception as e:
        error("SENSOR_HUB_INIT", e)
        sensor_hub = None

    _boot_oled(api, "ZebraBot", "Starting motors", "")

    try:
        motor_port_map = dict(MOTOR_PORT_MAP)
        motor_feedback = MotorFeedback(motor_port_map)
        motor_scanner = MotorScanner(
            motors=motors,
            feedback=motor_feedback,
            notify_fn=teleop.notify_line if teleop is not None else None,
            ports=ACTIVE_MOTOR_PORTS,
            scan_power=MOTOR_SCAN_POWER,
            pulse_ms=MOTOR_SCAN_PULSE_MS,
            period_ms=MOTOR_SCAN_PERIOD_MS,
        )

        api.register_handle("motor_feedback", motor_feedback)
        api.register_handle("motor_scanner", motor_scanner)

        if teleop is not None:
            teleop.motor_feedback = motor_feedback
            teleop.motor_scanner = motor_scanner
            teleop.motor_ports = ACTIVE_MOTOR_PORTS
            teleop.motor_port_map = motor_port_map

        info("BOOT: motor feedback/scanner initialized")
        state("BOOT", "motor_scan_ok")

    except Exception as e:
        error("MOTOR_SCAN_INIT", e)
        motor_feedback = None
        motor_scanner = None

    info("BOOT: robot boot complete")
    state("BOOT", "complete")
    api.status["boot"]["state"] = "complete"
    api.set_ready(True)

    await _boot_complete_message(api)

    if sensor_hub is not None:
        try:
            api.register_task("sensor_hub", asyncio.create_task(sensor_hub.task()))
            info("BOOT: SensorHub task started")
            state("TASK", "sensorhub_started")
        except Exception as e:
            error("SENSOR_HUB_TASK", e)

    if imu is not None and teleop is not None:
        imu_task_fn = getattr(teleop, "imu_task", None)
        if imu_task_fn is not None:
            try:
                api.register_task("imu", asyncio.create_task(imu_task_fn()))
                info("BOOT: IMU task started")
                state("TASK", "imu_started")
            except Exception as e:
                error("IMU_TASK_START", e)
        else:
            warn("BOOT: teleop.imu_task missing")
            state("TASK", "imu_missing")
    else:
        info("BOOT: IMU task skipped (no IMU)")
        state("TASK", "imu_skipped")

    if motor_scanner is not None:
        try:
            api.register_task("motor_scan", asyncio.create_task(motor_scanner.task()))
            info("BOOT: MotorScanner task started")
            state("TASK", "motor_scan_started")
        except Exception as e:
            error("MOTOR_SCAN_TASK", e)

        try:
            api.register_task(
                "motor_feedback",
                asyncio.create_task(
                    motor_scanner.feedback_task(period_ms=MOTOR_FEEDBACK_PERIOD_MS)
                ),
            )
            info("BOOT: Motor feedback task started")
            state("TASK", "motor_feedback_started")
        except Exception as e:
            error("MOTOR_FB_TASK", e)
    else:
        warn("BOOT: motor scan tasks skipped")

    try:
        api.register_task("api_housekeeping", asyncio.create_task(_api_housekeeping_task(api)))
    except Exception as e:
        error("API_HOUSEKEEPING", e)

    try:
        api.register_task("oled_status", asyncio.create_task(_oled_status_task(api)))
        info("BOOT: OLED status task started")
        state("TASK", "oled_status_started")
    except Exception as e:
        error("OLED_STATUS_START", e)

    try:
        api.register_task("user_main", asyncio.create_task(_run_user_program(api)))
        info("BOOT: user_main task started")
        state("TASK", "user_main_started")
    except Exception as e:
        error("USER_TASK_START", e)

    while True:
        api.status["system"]["heartbeat"] += 1
        state("SYS", "heartbeat")
        await asyncio.sleep(5)


def _safe_mode_requested():
    try:
        pin = Pin(SAFE_MODE_PIN, Pin.IN, Pin.PULL_UP)
        return pin.value() == 0
    except Exception as e:
        warn("SAFE_MODE_PIN unavailable: {}".format(e))
        return False


def boot():
    info("BOOT: main.py entry")

    if _safe_mode_requested():
        warn("BOOT: safe mode requested on GPIO{}; staying in REPL".format(SAFE_MODE_PIN))
        print("SAFE MODE: GPIO{} held low, normal boot skipped.".format(SAFE_MODE_PIN))
        print("SAFE MODE: release the pin and soft reset to boot normally.")
        state("BOOT", "safe_mode")
        if API is not None:
            API.status["boot"]["safe_mode"] = True
        return

    print("BOOT: starting in {} second(s); press Ctrl-C for REPL.".format(BOOT_GRACE_SECONDS))
    for remaining in range(BOOT_GRACE_SECONDS, 0, -1):
        state("BOOT", "grace_{}".format(remaining))
        print("BOOT: launch in {}...".format(remaining))
        time.sleep(1)

    asyncio.run(main())


try:
    boot()
finally:
    asyncio.new_event_loop()