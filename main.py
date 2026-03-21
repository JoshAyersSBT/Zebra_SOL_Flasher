# main.py
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


async def main():
    teleop = None
    sensor_hub = None
    imu = None
    oled = None
    mux = None
    base_i2c = None

    motors = {}
    motor_feedback = None
    motor_scanner = None

    info("BOOT: starting robot init")
    state("BOOT", "start")

    # -------------------------
    # Motors / drivetrain
    # -------------------------
    try:
        # Build all configured motors from config.py
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

        # Keep drivetrain abstraction on the configured left/right pair
        left = motors[1]
        right = motors[2]
        drive = DifferentialDrive(left, right, max_duty_u16=MOTOR_MAX_DUTY_U16)

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
        info("BOOT: TCA9548A initialized")
        diag(
            "TCA BUS sda={} scl={} addr={}".format(
                TCA_SDA_GPIO, TCA_SCL_GPIO, hex(TCA_ADDR)
            )
        )
        state("BOOT", "mux_ok")

        try:
            devices = base_i2c.scan()
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
    # BLE teleop
    # -------------------------
    try:
        teleop = BleTeleop(
            drive=drive,
            steering=steer,
            imu=imu,
            imu_period_ms=MPU_PERIOD_MS,
            oled=oled,
        )
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
        sensor_hub = SensorHub(
            i2c_id=TCA_I2C_ID,
            sda_gpio=TCA_SDA_GPIO,
            scl_gpio=TCA_SCL_GPIO,
            freq=TCA_I2C_FREQ,
            mux=mux,
            port_modes=SENSOR_PORT_MODES,
            notify_fn=teleop.notify_line,
            scan_period_ms=SENSOR_SCAN_PERIOD_MS,
        )
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
            notify_fn=teleop.notify_line,
            ports=ACTIVE_MOTOR_PORTS,
            scan_power=MOTOR_SCAN_POWER,
            pulse_ms=MOTOR_SCAN_PULSE_MS,
            period_ms=MOTOR_SCAN_PERIOD_MS,
        )

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

    # -------------------------
    # Background tasks
    # -------------------------
    if sensor_hub is not None:
        try:
            asyncio.create_task(sensor_hub.task())
            info("BOOT: SensorHub task started")
            state("TASK", "sensorhub_started")
        except Exception as e:
            error("SENSOR_HUB_TASK", e)

    if imu is not None:
        try:
            asyncio.create_task(teleop.imu_task())
            info("BOOT: IMU task started")
            state("TASK", "imu_started")
        except Exception as e:
            error("IMU_TASK_START", e)
    else:
        info("BOOT: IMU task skipped (no IMU)")
        state("TASK", "imu_skipped")

    if motor_scanner is not None:
        try:
            asyncio.create_task(motor_scanner.task())
            info("BOOT: MotorScanner task started")
            state("TASK", "motor_scan_started")
        except Exception as e:
            error("MOTOR_SCAN_TASK", e)

        try:
            asyncio.create_task(
                motor_scanner.feedback_task(period_ms=MOTOR_FEEDBACK_PERIOD_MS)
            )
            info("BOOT: Motor feedback task started")
            state("TASK", "motor_feedback_started")
        except Exception as e:
            error("MOTOR_FB_TASK", e)
    else:
        warn("BOOT: motor scan tasks skipped")

    # -------------------------
    # Idle loop / heartbeat
    # -------------------------
    while True:
        state("SYS", "heartbeat")
        await asyncio.sleep(5)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()