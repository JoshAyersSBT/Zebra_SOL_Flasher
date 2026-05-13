# robot/config.py
CONFIG_BUILD = "pin-table-2pin-motor-shared-actuator-v1"

# ============================================================
# ACTUATOR / MOTOR PORT DEFINITIONS
# ============================================================
#
# Pin table meaning:
#   F = encoder/tick flag input
#   D = motor direction output
#   P = motor PWM output / servo signal output
#
# Motor ports and servo ports are the same physical actuator ports.
# User code decides whether a port is used as a motor or as a servo.

# M1 header
M1_ENC = 17
M1_DIR = 16
M1_PWM = 23

# M2 header
M2_ENC = 34
M2_DIR = 14
M2_PWM = 13

# M3 header
M3_ENC = 27
M3_DIR = 26
M3_PWM = 25

# M4 header
M4_ENC = 35
M4_DIR = 32
M4_PWM = 33


# ============================================================
# PORT / ACTUATOR MAP
# ============================================================

PORT_MAP = {
    1: {
        "name": "M1",
        "pins": {"enc": M1_ENC, "dir": M1_DIR, "pwm": M1_PWM},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
        "invert_pwm": False,
    },
    2: {
        "name": "M2",
        "pins": {"enc": M2_ENC, "dir": M2_DIR, "pwm": M2_PWM},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
        "invert_pwm": False,
    },
    3: {
        "name": "M3",
        "pins": {"enc": M3_ENC, "dir": M3_DIR, "pwm": M3_PWM},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
        "invert_pwm": False,
    },
    4: {
        "name": "M4",
        "pins": {"enc": M4_ENC, "dir": M4_DIR, "pwm": M4_PWM},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
        "invert_pwm": False,
    },
}


# ============================================================
# MOTOR PORT MAP
# ============================================================
#
# This board uses the 2-control-line motor model:
#   pwm = speed/control signal
#   dir = direction signal
#   enc = optional encoder/tick flag input
#
# The old fwd/rev keys are intentionally not included here because F in the
# table is not "forward"; it is the encoder/tick flag.

MOTOR_PORT_MAP = {
    port: {
        "name": cfg["name"],
        "pwm": cfg["pins"]["pwm"],
        "dir": cfg["pins"]["dir"],
        "enc": cfg["pins"]["enc"],
        "invert_pwm": bool(cfg.get("invert_pwm", False)),
    }
    for port, cfg in PORT_MAP.items()
}


# ============================================================
# DRIVE TRAIN LEGACY DEFAULTS
# ============================================================
#
# These are compatibility defaults only. User robot behavior belongs in
# user_main.py, not here.

DRIVE_MOTOR_PORTS = tuple(sorted(MOTOR_PORT_MAP.keys()))
ACTIVE_MOTOR_PORTS = DRIVE_MOTOR_PORTS

LEFT_PORT = 1
RIGHT_PORT = 2

LEFT_PWM = MOTOR_PORT_MAP[LEFT_PORT]["pwm"]
LEFT_DIR = MOTOR_PORT_MAP[LEFT_PORT]["dir"]
LEFT_ENC = MOTOR_PORT_MAP[LEFT_PORT]["enc"]

RIGHT_PWM = MOTOR_PORT_MAP[RIGHT_PORT]["pwm"]
RIGHT_DIR = MOTOR_PORT_MAP[RIGHT_PORT]["dir"]
RIGHT_ENC = MOTOR_PORT_MAP[RIGHT_PORT]["enc"]


# ============================================================
# SERVO CONFIG
# ============================================================

SERVO_FREQ_HZ = 50
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
SERVO_CENTER_DEG = 90

# Motor ports and servo ports are the same physical actuator ports.
# Servo signal uses the same P/PWM pin for each actuator port.
SERVO_PORT_MAP = {
    port: {
        "name": "{}_SERVO".format(cfg["name"]),
        "gpio": cfg["pwm"],
        "freq_hz": SERVO_FREQ_HZ,
        "min_us": SERVO_MIN_US,
        "max_us": SERVO_MAX_US,
        "center_deg": SERVO_CENTER_DEG,
        "role": "",
    }
    for port, cfg in MOTOR_PORT_MAP.items()
}

# Legacy fallback only. User code should call zbot.servo(port) directly.
STEER_SERVO_PORT = 1
STEER_SERVO_GPIO = SERVO_PORT_MAP[STEER_SERVO_PORT]["gpio"]


# ============================================================
# BLE
# ============================================================

BLE_NAME = "ZebraBot"


# ============================================================
# I2C / TCA9548A MUX
# ============================================================

TCA_I2C_ID = 0
TCA_SDA_GPIO = 21
TCA_SCL_GPIO = 22
TCA_I2C_FREQ = 400000
TCA_ADDR = 0x70


# ============================================================
# OLED (MUX CHANNEL 0)
# ============================================================

OLED_ADDR = 0x3C
OLED_CHANNEL = 0
OLED_WIDTH = 128
OLED_HEIGHT = 64


# ============================================================
# IMU (MUX CHANNEL 7)
# ============================================================

MPU_ADDR = 0x68
MPU_CHANNEL = 7
MPU_PERIOD_MS = 10


# ============================================================
# SENSOR PORT DATA PINS
# ============================================================

SENSOR_DATA_PINS = {
    1: 18,
    2: 19,
    3: 5,
    4: 36,
    5: 39,
    6: 4,
}


# ============================================================
# SENSOR HUB CONFIG
# ============================================================

SENSOR_SCAN_PERIOD_MS = 100

SENSOR_PORT_MODES = {
    1: "auto",
    2: "auto",
    3: "auto",
    4: "auto",
    5: "auto",
    6: "auto",
}


# ============================================================
# MOTOR / TELEMETRY SETTINGS
# ============================================================

MOTOR_PWM_FREQ_HZ = 20000
MOTOR_MAX_DUTY_U16 = 40000

MOTOR_SCAN_POWER = 25
MOTOR_SCAN_PULSE_MS = 250
MOTOR_SCAN_PERIOD_MS = 1500
MOTOR_FEEDBACK_PERIOD_MS = 200


# ============================================================
# BUTTON IO SETTINGS
# ============================================================

BUTTON0_IO = 15
BUTTON1_IO = 12
PULLDOWN = False

BUTTON_MAP = {
    1: {"name": "B1", "gpio": BUTTON0_IO, "pull": "down", "active_low": False},
    2: {"name": "B2", "gpio": BUTTON1_IO, "pull": "down", "active_low": False},
}
