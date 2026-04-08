class AckermannDrive:
    def __init__(self, zbot, drive_motor_port, steering_port, center_angle=90):
        self.zbot = zbot
        self.center_angle = int(center_angle)

        # Explicit hardware mapping
        self.motor = zbot.motor(drive_motor_port)
        self.steering = zbot.servo(steering_port)

    # ------------------------
    # Motion
    # ------------------------

    def forward(self, power=50):
        self.motor.on(abs(int(power)))

    def backward(self, power=50):
        self.motor.on(-abs(int(power)))

    def stop(self):
        self.motor.off()

    # ------------------------
    # Steering
    # ------------------------

    def steer(self, angle):
        angle = int(angle)
        self.steering.write_angle(angle)

    def steer_center(self):
        self.steer(self.center_angle)

    # ------------------------
    # Combined control
    # ------------------------

    def drive(self, throttle, steering_angle=None):
        throttle = int(throttle)

        if throttle > 0:
            self.forward(throttle)
        elif throttle < 0:
            self.backward(-throttle)
        else:
            self.stop()

        if steering_angle is not None:
            self.steer(int(steering_angle))