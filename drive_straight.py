# drive_straight.py
# Simple robot drive example
# 2 planetary motors + 1 steering servo

from machine import Pin, PWM
import time


# =========================
# GPIO CONFIGURATION
# (change to match your mapping)
# =========================

LEFT_PWM = 18
LEFT_DIR = 19

RIGHT_PWM = 21
RIGHT_DIR = 22

SERVO_PIN = 23


# =========================
# MOTOR CLASS
# =========================

class Motor:

    def __init__(self, pwm_pin, dir_pin):
        self.dir = Pin(dir_pin, Pin.OUT)

        self.pwm = PWM(Pin(pwm_pin))
        self.pwm.freq(20000)
        self.stop()

    def forward(self, speed=30000):
        self.dir.value(1)
        self.pwm.duty_u16(speed)

    def reverse(self, speed=30000):
        self.dir.value(0)
        self.pwm.duty_u16(speed)

    def stop(self):
        self.pwm.duty_u16(0)


# =========================
# SERVO CLASS
# =========================

class Servo:

    def __init__(self, pin):
        self.pwm = PWM(Pin(pin), freq=50)

    def angle(self, a):
        a = max(0, min(180, a))

        min_us = 500
        max_us = 2500

        pulse = min_us + (max_us - min_us) * a // 180
        duty = (pulse * 65535) // 20000

        self.pwm.duty_u16(int(duty))


# =========================
# INITIALIZE HARDWARE
# =========================

left_motor = Motor(LEFT_PWM, LEFT_DIR)
right_motor = Motor(RIGHT_PWM, RIGHT_DIR)

steering = Servo(SERVO_PIN)


# =========================
# DRIVE PROGRAM
# =========================

print("Center steering")

steering.angle(90)

time.sleep(1)

print("Driving forward")

left_motor.forward()
right_motor.forward()

time.sleep(4)

print("Stopping")

left_motor.stop()
right_motor.stop()

print("Done")