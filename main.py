import time
import uasyncio as asyncio
from machine import I2C, Pin

from robot.config import (
    LEFT_PWM, LEFT_DIR, LEFT_ENC,
    RIGHT_PWM, RIGHT_DIR, RIGHT_ENC,
    MOTOR_PWM_FREQ_HZ, MOTOR_MAX_DUTY_U16,
    STEER_SERVO_GPIO, SERVO_FREQ_HZ, SERVO_MIN_US, SERVO_MAX_US,
    TCA_I2C_ID, TCA_SDA_GPIO, TCA_SCL_GPIO, TCA_I2C_FREQ, TCA_ADDR,
    MPU_ADDR, MPU_CHANNEL, MPU_PERIOD_MS,
    OLED_ADDR, OLED_CHANNEL, OLED_WIDTH, OLED_HEIGHT,
    SENSOR_SCAN_PERIOD_MS, SENSOR_PORT_MODES,
    MOTOR_PORT_MAP, ACTIVE_MOTOR_PORTS,
    MOTOR_SCAN_POWER, MOTOR_SCAN_PULSE_MS, MOTOR_SCAN_PERIOD_MS,
    MOTOR_FEEDBACK_PERIOD_MS,
)
from robot.motors import Motor
from robot.servo import Servo
from robot.drivetrain import DifferentialDrive
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

API = None
zbot = None


class RobotAPI:
    """
    Shared runtime API exposed to user programs and internal services.
    """

    def __init__(self):
        self.status = {
            "boot": {"state": "init", "safe_mode": False},
            "system": {"heartbeat": 0, "ready": False},
            "motors": {},
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

    def set_motor(self, port, power):
        motors = self.handles.get("motors", {})
        motor = motors.get(port)
        if motor is None:
            raise ValueError("unknown motor port {}".format(port))

        power = int(power)
        motor.set_power(power)

        self.status["motors"][port] = {
            "power": power,
            "ts_ms": time.ticks_ms(),
            "name": self.get_motor_map().get(port, {}).get("name", "M{}".format(port)),
        }
        return self.status["motors"][port]

    def stop_motor(self, port):
        return self.set_motor(port, 0)

    def stop_all(self):
        motors = self.handles.get("motors", {})
        for port in motors:
            try:
                self.set_motor(port, 0)
            except Exception as e:
                error("STOP_MOTOR_{}".format(port), e)

        drive = self.handles.get("drive")
        if drive is not None:
            try:
                drive.stop()
            except Exception:
                pass

    def drive_tank(self, left_power, right_power):
        drive = self.handles.get("drive")
        if drive is None:
            raise RuntimeError("drive unavailable")

        left_power = int(left_power)
        right_power = int(right_power)
        drive.tank(left_power, right_power)
        self.status["drive"] = {
            "mode": "tank",
            "left": left_power,
            "right": right_power,
            "ts_ms": time.ticks_ms(),
        }
        return self.status["drive"]

    def set_steering(self, angle):
        steer = self.handles.get("steer")
        if steer is None:
            raise RuntimeError("steering unavailable")

        angle = int(angle)
        steer.write_angle(angle)
        self.status["steering"] = {
            "angle": angle,
            "ts_ms": time.ticks_ms(),
        }
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
            reading = imu.read()
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
            self.mark_user_display()
            oled.show_lines(*lines)
            return True
        except Exception as e:
            error("API_OLED", e)
            return False


class _ZBotSensor:
    def __init__(self, api, port):
        self.api = api
        self.port = int(port)

    def _find_snapshot_value(self):
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
    def __init__(self, api=None):
        self.api = api
        self._motor_wrappers = {}

    def bind(self, api):
        self.api = api
        return self

    def ready(self):
        return self.api is not None and bool(self.api.status["system"].get("ready", False))

    def forward(self, power=50):
        if self.api is None:
            return False
        self.api.drive_tank(int(power), int(power))
        return True

    def backward(self, power=50):
        if self.api is None:
            return False
        p = int(power)
        self.api.drive_tank(-p, -p)
        return True

    def tank(self, left_power, right_power):
        if self.api is None:
            return False
        self.api.drive_tank(int(left_power), int(right_power))
        return True

    def stop(self):
        if self.api is None:
            return False
        self.api.stop_all()
        return True

    def motors(self, port, motor_type="DC"):
        key = (int(port), str(motor_type))
        if self.api is None:
            return _ZBotMotor(None, port, motor_type)

        if key not in self._motor_wrappers:
            self._motor_wrappers[key] = _ZBotMotor(self.api, port, motor_type)

        return self._motor_wrappers[key]

    def motor(self, port, motor_type="DC"):
        return self.motors(port, motor_type)

    def display(self, line1="", line2=""):
        if self.api is None:
            return False
        return self.api.show_lines(str(line1), str(line2))

    def say(self, line1="", line2=""):
        return self.display(line1, line2)

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

    def motor_status(self):
        if self.api is None:
            return {}
        return self.api.get_motor_status()

    def motor_feedback(self):
        if self.api is None:
            return {}
        return self.api.get_motor_feedback()


def get_api():
    return API


def get_zbot():
    return zbot


def _boot_oled(api, line1, line2="", line3=""):
    try:
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


async def _api_housekeeping_task(api):
    while True:
        try:
            motor_feedback = api.get_handle("motor_feedback")
            if motor_feedback is not None:
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

    while True:
        try:
            oled = api.get_handle("oled")
            if oled is None or not getattr(oled, "available", False):
                await asyncio.sleep_ms(500)
                continue

            if api.user_display_active():
                await asyncio.sleep_ms(200)
                continue

            line1 = "ZebraBot Ready" if api.status["system"].get("ready") else "ZebraBot Boot"
            line2 = _format_ble_line(api)
            line3 = _format_user_line(api)
            line4 = _format_tof_line(api)

            lines = (line1, line2, line3, line4)
            if lines != last_lines:
                oled.show_lines(*lines)
                last_lines = lines

        except Exception as e:
            error("OLED_STATUS_TASK", e)

        await asyncio.sleep_ms(300)


async def _boot_complete_message(api):
    _boot_oled(api, "ZebraBot", "Boot Complete", "Starting tasks...")
    await asyncio.sleep_ms(1200)


async def _run_user_program(api):
    teleop = api.get_handle("teleop")

    try:
        import user_main
    except Exception as e:
        error("USER_IMPORT", e)
        api.status["user"]["last_error"] = repr(e)

        if teleop is not None:
            try:
                teleop.notify_error("USER_IMPORT", e)
            except Exception:
                pass

        try:
            api.show_lines("User Import Err", "See BLE log")
        except Exception:
            pass
        return

    user_fn = getattr(user_main, "main", None)
    if user_fn is None:
        warn("USER: user_main.main missing")

        if teleop is not None:
            try:
                teleop.notify_line("ERR USER main() missing")
            except Exception:
                pass

        try:
            api.show_lines("User Error", "main() missing")
        except Exception:
            pass
        return

    api.status["user"]["running"] = True
    api.status["user"]["last_error"] = None

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
            await user_fn(api)

    except Exception as e:
        api.status["user"]["last_error"] = repr(e)
        error("USER_MAIN", e)

        if teleop is not None:
            try:
                teleop.notify_error("USER_MAIN", e)
            except Exception:
                pass

        try:
            api.show_lines("User Error", "See BLE log", str(type(e).__name__))
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
    drive = None
    steer = None

    motors = {}
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
                "name": cfg.get("name", "M{}".format(port)),
                "enc": cfg.get("enc"),
                "ts_ms": time.ticks_ms(),
            }

        left = motors[1]
        right = motors[2]
        drive = DifferentialDrive(left, right, max_duty_u16=MOTOR_MAX_DUTY_U16)

        api.register_handle("motors", motors)
        api.register_handle("drive", drive)
        api.register_handle("motor_port_map", dict(MOTOR_PORT_MAP))

        info("BOOT: motors initialized")
        diag("DRIVE LEFT PWM={} DIR={} ENC={}".format(LEFT_PWM, LEFT_DIR, LEFT_ENC))
        diag("DRIVE RIGHT PWM={} DIR={} ENC={}".format(RIGHT_PWM, RIGHT_DIR, RIGHT_ENC))
        state("BOOT", "motors_ok")

    except Exception as e:
        error("MOTOR_INIT", e)
        raise

    try:
        steer = Servo(
            STEER_SERVO_GPIO,
            freq_hz=SERVO_FREQ_HZ,
            min_us=SERVO_MIN_US,
            max_us=SERVO_MAX_US,
        )
        api.register_handle("steer", steer)
        api.status["steering"] = {"angle": None, "ts_ms": time.ticks_ms()}
        info("BOOT: servo initialized")
        diag("SERVO GPIO={}".format(STEER_SERVO_GPIO))
        state("BOOT", "servo_ok")
    except Exception as e:
        error("SERVO_INIT", e)
        raise

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
            drive=drive,
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
        error("BLE_INIT", e)
        _boot_oled(api, "ZebraBot", "BLE init fail", str(type(e).__name__))
        raise

    _boot_oled(api, "ZebraBot", "Starting sensors", "")

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