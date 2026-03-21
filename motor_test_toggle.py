# motor_test_toggle.py
# Standalone MicroPython motor test
# Drives all motors and flips direction every 0.25s

from machine import Pin, PWM
import time

# --------------------------------------------------
# MOTOR PORT MAP
# Replace GPIO numbers once mapping is known
# --------------------------------------------------

MOTOR_PORT_MAP = {
    1: {"pwm": 18, "dir": 19},
    2: {"pwm": 21, "dir": 22},
    3: {"pwm": 23, "dir": 5},
    4: {"pwm": 17, "dir": 16},
}

PWM_FREQ = 20000
PWM_DUTY = 30000   # ~45% duty for u16 scale

# --------------------------------------------------
# Motor class
# --------------------------------------------------

class Motor:

    def __init__(self, pwm_pin, dir_pin):
        self.dir = Pin(dir_pin, Pin.OUT)

        self.pwm = PWM(Pin(pwm_pin))
        self.pwm.freq(PWM_FREQ)

        self.stop()

    def forward(self):
        self.dir.value(1)
        self.pwm.duty_u16(PWM_DUTY)

    def reverse(self):
        self.dir.value(0)
        self.pwm.duty_u16(PWM_DUTY)

    def stop(self):
        self.pwm.duty_u16(0)


# --------------------------------------------------
# Initialize motors
# --------------------------------------------------

motors = []

for port, pins in MOTOR_PORT_MAP.items():
    try:
        m = Motor(pins["pwm"], pins["dir"])
        motors.append(m)
        print("Motor", port, "initialized")
    except Exception as e:
        print("Motor", port, "failed:", e)


print("Motor toggle test starting")

direction = True

# --------------------------------------------------
# Main loop
# --------------------------------------------------

while True:

    if direction:
        for m in motors:
            m.forward()
        print("Forward")
    else:
        for m in motors:
            m.reverse()
        print("Reverse")

    direction = not direction

    time.sleep(0.25)