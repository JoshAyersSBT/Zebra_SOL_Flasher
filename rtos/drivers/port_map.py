# /rtos/drivers/port_map.py

# Servo ports (1..7)
SERVO_PORT_MAP = {
    1: None,
    2: None,
    3: None,
    4: None,
    5: None,
    6: None,
    7: None,
}

# Planetary motor ports (likely 1..4, but keep flexible)
# Each motor port needs: PWM GPIO, DIR GPIO, ENC GPIO (ENC optional -> None)
MOTOR_PORT_MAP = {
    1: {"pwm": None, "dir": None, "enc": None},
    2: {"pwm": None, "dir": None, "enc": None},
    3: {"pwm": None, "dir": None, "enc": None},
    4: {"pwm": None, "dir": None, "enc": None},
}