# main.py
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


class RobotAPI:
    """
    Shared runtime API exposed to user programs.

    This keeps main.py focused on system bring-up while giving user_main.py
    a stable way to read live state and command actuators.
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

    # -------------------------
    # runtime registration
    # -------------------------
    def register_handle(self, name, value):
        self.handles[name] = value
        return value

    def get_handle(self, name, default=None):
        return self.handles.get(name, default)

    def register_task(self, name, task):
        self.tasks[name] = task
        return task

    # -------------------------
    # system state
    # -------------------------
    def set_ready(self, ready=True):
        self.status["system"]["ready"] = bool(ready)

    def get_status(self):
        return self.status

    def get_services(self):
        return self.status.get("services", {})

    # -------------------------
    # motor API
    # -------------------------
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

    # -------------------------
    # steering API
    # -------------------------
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

    # -------------------------
    # sensor API
    # -------------------------
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

    # -------------------------
    # display / transport helpers
    # -------------------------
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
            oled.show_lines(*lines)
            return True
        except Exception as e:
            error("API_OLED", e)
            return False


API = None


def get_api():
    return API


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


async def _run_user_program(api):
    try:
        import user_main
    except Exception as e:
        error("USER_IMPORT", e)
        api.status["user"]["last_error"] = repr(e)
        return

    user_fn = getattr(user_main, "main", None)
    if user_fn is None:
        warn("USER: user_main.main missing")
        return

    api.status["user"]["running"] = True
    api.status["user"]["last_error"] = None

    try:
        # Prefer API-first user programs, but keep backward compatibility.
        argc = None
        try:
            argc = user_fn.__code__.co_argcount
        except Exception:
            pass

        if argc == 1:
            await user_fn(api)
        else:
            await user_fn(api)
    except Exception as e:
        api.status["user"]["last_error"] = repr(e)
        error("USER_MAIN", e)
    finally:
        api.status["user"]["running"] = False


async def main():
    global API

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

    info("BOOT: starting robot init")
    state("BOOT", "start")
    api.status["boot"]["state"] = "starting"

    # -------------------------
    # Motors / drivetrain
    # -------------------------
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

    # -------------------------
    # Steering servo
    # -------------------------
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

    # -------------------------
    # Base I2C + TCA9548A mux
    # -------------------------
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

    # -------------------------
    # MPU-6050 on mux channel
    # -------------------------
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

    # -------------------------
    # OLED on mux channel
    # -------------------------
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
            oled.show_lines("ZebraBot", "Booting...")
            info("BOOT: OLED initialized")
            diag("OLED CH={} ADDR={}".format(OLED_CHANNEL, hex(OLED_ADDR)))
            state("BOOT", "oled_ok")
        else:
            info("BOOT: OLED unavailable")
            state("BOOT", "oled_unavailable")
    except Exception as e:
        error("OLED_INIT", e)
        oled = None

    # -------------------------
    # BLE interface layer
    # -------------------------
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
        raise

    # -------------------------
    # Sensor hub (mux channels 1..6)
    # -------------------------
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

    # -------------------------
    # Motor feedback + scan
    # -------------------------
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

    # -------------------------
    # Background tasks
    # -------------------------
    if sensor_hub is not None:
        try:
            api.register_task("sensor_hub", asyncio.create_task(sensor_hub.task()))
            info("BOOT: SensorHub task started")
            state("TASK", "sensorhub_started")
        except Exception as e:
            error("SENSOR_HUB_TASK", e)

    if imu is not None and teleop is not None:
        try:
            api.register_task("imu", asyncio.create_task(teleop.imu_task()))
            info("BOOT: IMU task started")
            state("TASK", "imu_started")
        except Exception as e:
            error("IMU_TASK_START", e)
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
        api.register_task("user_main", asyncio.create_task(_run_user_program(api)))
        info("BOOT: user_main task started")
        state("TASK", "user_main_started")
    except Exception as e:
        error("USER_TASK_START", e)

    # -------------------------
    # Idle loop / heartbeat
    # -------------------------
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
