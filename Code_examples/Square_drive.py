import uasyncio as asyncio
import gc
from robot.ackermann import AckermannDrive

async def main(zbot):
    gc.collect()

    drive = AckermannDrive(
        zbot,
        drive_motor_port=1,
        steering_port=1,
        center_angle=90,
    )

    side_power = 45
    side_time_ms = 1800

    turn_power = 35
    turn_time_ms = 900

    left_turn_angle = 55
    straight_angle = 90

    drive.steer_center()
    zbot.display("Square", "Starting")
    await asyncio.sleep_ms(1000)

    while True:
        for i in range(4):
            # Drive straight for one side
            drive.steer(straight_angle)
            drive.forward(side_power)
            zbot.display("Square", "Side {}".format(i + 1))
            await asyncio.sleep_ms(side_time_ms)

            # Stop briefly before turning
            drive.stop()
            zbot.display("Square", "Corner {}".format(i + 1))
            await asyncio.sleep_ms(400)

            # Turn left
            drive.steer(left_turn_angle)
            drive.forward(turn_power)
            await asyncio.sleep_ms(turn_time_ms)

            # Stop briefly after turn
            drive.stop()
            drive.steer_center()
            await asyncio.sleep_ms(400)

        zbot.display("Square", "Loop done")
        await asyncio.sleep_ms(1000)