
import uasyncio as asyncio
import gc
from robot.ackermann import AckermannDrive

async def main(zbot):
    gc.collect()

    # Explicit hardware definition
    drive = AckermannDrive(
        zbot,
        drive_motor_port=1,
        steering_port=2,
        center_angle=90
    )

    tof = zbot.sensor(1)

    drive.steer_center()

    while True:
        d = tof.read()

        if d is None:
            drive.stop()
            drive.steer_center()
            zbot.display("NO SENSOR", "")
        elif d < 100:
            drive.stop()
            drive.steer_center()
            zbot.display("STOP", str(d))
        else:
            drive.forward(100)
            drive.steer_center()
            zbot.display("GO", str(d))

        await asyncio.sleep_ms(50)
            