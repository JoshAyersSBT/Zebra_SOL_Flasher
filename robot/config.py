# robot/config.py

# ============================================================
# MOTOR PORT DEFINITIONS
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
# DRIVE TRAIN (choose which motors drive the robot)
# ============================================================

LEFT_PWM = M1_PWM
LEFT_DIR = M1_DIR
LEFT_ENC = M1_ENC

RIGHT_PWM = M2_PWM
RIGHT_DIR = M2_DIR
RIGHT_ENC = M2_ENC


# ============================================================
# FULL MOTOR PORT MAP
# ============================================================

MOTOR_PORT_MAP = {
    1: {"name": "M1", "pwm": M1_PWM, "dir": M1_DIR, "enc": M1_ENC},
    2: {"name": "M2", "pwm": M2_PWM, "dir": M2_DIR, "enc": M2_ENC},
    3: {"name": "M3", "pwm": M3_PWM, "dir": M3_DIR, "enc": M3_ENC},
    4: {"name": "M4", "pwm": M4_PWM, "dir": M4_DIR, "enc": M4_ENC},
}


# Ports actively used by the drivetrain / BLE motor scan
DRIVE_MOTOR_PORTS = (1, 2)
# ============================================================
# FULL MOTOR PORT MAP
# ============================================================

MOTOR_PORT_MAP = {
    1: {"name": "M1", "pwm": M1_PWM, "dir": M1_DIR, "enc": M1_ENC},
    2: {"name": "M2", "pwm": M2_PWM, "dir": M2_DIR, "enc": M2_ENC},
    3: {"name": "M3", "pwm": M3_PWM, "dir": M3_DIR, "enc": M3_ENC},
    4: {"name": "M4", "pwm": M4_PWM, "dir": M4_DIR, "enc": M4_ENC},
}

# Which ports are used by the drivetrain abstraction
DRIVE_MOTOR_PORTS = (1, 2)

# Which ports should be exposed to BLE motor scan / state
ACTIVE_MOTOR_PORTS = (1, 2, 3, 4)

# Motor scan settings
MOTOR_SCAN_POWER = 25
MOTOR_SCAN_PULSE_MS = 250
MOTOR_SCAN_PERIOD_MS = 1500
MOTOR_FEEDBACK_PERIOD_MS = 200

# ============================================================
# MOTOR SETTINGS
# ============================================================

MOTOR_PWM_FREQ_HZ = 20000
MOTOR_MAX_DUTY_U16 = 40000

# Used by motor scanner if not overridden in main.py
MOTOR_SCAN_POWER = 25
MOTOR_SCAN_PULSE_MS = 250
MOTOR_SCAN_PERIOD_MS = 1500
MOTOR_FEEDBACK_PERIOD_MS = 200


# ============================================================
# SERVO
# ============================================================

STEER_SERVO_GPIO = 18

SERVO_FREQ_HZ = 50
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
SERVO_CENTER_DEG = 90


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