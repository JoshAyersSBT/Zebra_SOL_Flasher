# robot/config.py

# ============================================================
# MOTOR PORT DEFINITIONS (PHYSICAL PINOUT)
# ============================================================

# M1
M1_ENC = 17
M1_DIR = 16
M1_PWM = 23

# M2
M2_ENC = 34
M2_DIR = 14
M2_PWM = 13

# M3
M3_ENC = 27
M3_DIR = 26
M3_PWM = 25

# M4
M4_ENC = 35
M4_DIR = 32
M4_PWM = 33


# ============================================================
# PORT / ACTUATOR MAP
# ============================================================

PORT_MAP = {
    1: {
        "name": "M1",
        "pins": {"pwm": M1_PWM, "dir": M1_DIR, "enc": M1_ENC},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
    },
    2: {
        "name": "M2",
        "pins": {"pwm": M2_PWM, "dir": M2_DIR, "enc": M2_ENC},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
    },
    3: {
        "name": "M3",
        "pins": {"pwm": M3_PWM, "dir": M3_DIR, "enc": M3_ENC},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
    },
    4: {
        "name": "M4",
        "pins": {"pwm": M4_PWM, "dir": M4_DIR, "enc": M4_ENC},
        "supports": ["dc_motor", "servo"],
        "default_mode": "dc_motor",
    },
}


# ============================================================
# LEGACY MOTOR PORT MAP COMPATIBILITY
# ============================================================

# Keep this for older code paths that still expect flat pwm/dir/enc keys.
MOTOR_PORT_MAP = {
    port: {
        "name": cfg["name"],
        "pwm": cfg["pins"]["pwm"],
        "dir": cfg["pins"]["dir"],
        "enc": cfg["pins"]["enc"],
    }
    for port, cfg in PORT_MAP.items()
}


# ============================================================
# DRIVE TRAIN CONFIG
# ============================================================

# Which actuator ports are used for drive motion.
DRIVE_MOTOR_PORTS = (1, 2)
ACTIVE_MOTOR_PORTS = (1, 2, 3, 4)

LEFT_PORT = 1
RIGHT_PORT = 2

# Legacy aliases still used by current code.
LEFT_PWM = MOTOR_PORT_MAP[LEFT_PORT]["pwm"]
LEFT_DIR = MOTOR_PORT_MAP[LEFT_PORT]["dir"]
LEFT_ENC = MOTOR_PORT_MAP[LEFT_PORT]["enc"]

RIGHT_PWM = MOTOR_PORT_MAP[RIGHT_PORT]["pwm"]
RIGHT_DIR = MOTOR_PORT_MAP[RIGHT_PORT]["dir"]
RIGHT_ENC = MOTOR_PORT_MAP[RIGHT_PORT]["enc"]


# ============================================================
# SERVO CONFIG
# ============================================================

# Shared servo timing defaults.
SERVO_FREQ_HZ = 50
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
SERVO_CENTER_DEG = 90

# Steering is a ROLE bound to a port, not a dedicated pin.
STEER_SERVO_PORT = 1

# Backward-compatible servo map for generic servo registration.
SERVO_PORT_MAP = {
    port: {
        "name": "{}_SERVO".format(cfg["name"]),
        "gpio": cfg["pins"]["pwm"],
        "freq_hz": SERVO_FREQ_HZ,
        "min_us": SERVO_MIN_US,
        "max_us": SERVO_MAX_US,
        "center_deg": SERVO_CENTER_DEG,
        "role": "steering" if port == STEER_SERVO_PORT else "",
    }
    for port, cfg in PORT_MAP.items()
}

# Legacy compatibility only.
# Do not use this for new code. It points to the steering port PWM pin.
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