import utime
from drivers.device_manager import DeviceManager
from drivers.zebra_servo import ZebraServo
from drivers.smotor2 import SMotor2

print("Initializing motor and servo...")

# Match your example:
# ZebraServo myServo(3);
# SMotor2 motor(1);
servo = ZebraServo(3)
motor = SMotor2(1)

motor.begin()
servo.begin()

print("Initialization complete!")

while True:
    print("Servo -> 60°")
    servo.run_angles(60)

    print("Motor -> forward power 50 (approx)")
    motor.run_motor(50)
    utime.sleep_ms(800)
    motor.stop_motor()
    utime.sleep_ms(200)
    print("Done!")

    print("Servo -> 120°")
    servo.run_angles(120)

    print("Motor -> backward power -50 (approx)")
    motor.run_motor(-50)
    utime.sleep_ms(800)
    motor.stop_motor()
    utime.sleep_ms(200)
    print("Done Again!")