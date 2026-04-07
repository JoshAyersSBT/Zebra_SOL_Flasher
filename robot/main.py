"""
Integration notes for main.py
============================

1) Add a config choice, for example in robot/config.py:

    DRIVE_MODE = "ackermann"   # or "differential"
    STEER_CENTER_DEG = 90
    STEER_MAX_DEG = 35

2) Replace:

    from robot.drivetrain import DifferentialDrive

   with:

    from robot.drivetrain import create_drive_system

3) Replace drive construction block with something like:

    drive = create_drive_system(
        DRIVE_MODE,
        left_motor=left,
        right_motor=right,
        steering_servo=steer,
        max_duty_u16=MOTOR_MAX_DUTY_U16,
        center_deg=STEER_CENTER_DEG,
        max_steer_deg=STEER_MAX_DEG,
    )

4) Keep runtime API methods unified:

    drive.drive(throttle, steering)
    drive.forward(power)
    drive.backward(power)
    drive.stop()
    drive.tank(left, right)   # legacy compatibility
    drive.steer(angle)

5) In RobotAPI / ZBot:

   - forward() should call drive.forward()
   - backward() should call drive.backward()
   - drive(power, steering=0) should call drive.drive(power, steering)
   - steer(angle) should call drive.steer(angle)
   - tank(left, right) can remain as a compatibility helper

This gives you one student API and two swappable chassis models.
"""
