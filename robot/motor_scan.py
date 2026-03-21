# robot/motor_scan.py

import uasyncio as asyncio
from robot.debug_io import info, error


class MotorScanner:
    def __init__(self, motors, feedback, notify_fn,
                 ports=(1, 2, 3, 4),
                 scan_power=25,
                 pulse_ms=250,
                 period_ms=1500):

        # motors should be a dict {1:m1,2:m2,3:m3,4:m4}
        self.motors = motors
        self.feedback = feedback
        self.notify = notify_fn

        self.ports = tuple(ports)
        self.scan_power = int(scan_power)
        self.pulse_ms = int(pulse_ms)
        self.period_ms = int(period_ms)

        self.enabled = False

    def _notify(self, line):
        try:
            self.notify(str(line))
        except Exception:
            pass

    async def pulse_test(self, port):
        """
        Pulse a motor briefly and measure encoder ticks
        """

        try:

            motor = self.motors.get(port)

            if motor is None:
                self._notify("MTR_ERR {} unsupported_port".format(port))
                return

            self.feedback.reset(port)

            duty = (self.scan_power * motor.max_duty) // 100

            motor.set(True, duty)

            await asyncio.sleep_ms(self.pulse_ms)

            motor.set(True, 0)

            ticks = self.feedback.get(port)

            self._notify(
                "MTR_SCAN {} power={} ticks={}".format(
                    port,
                    self.scan_power,
                    ticks
                )
            )

        except Exception as e:
            error("MTR_SCAN_{}".format(port), e)
            self._notify("MTR_ERR {} scan_failed".format(port))

    async def task(self):

        info("MotorScanner task started")

        while True:

            if self.enabled:

                for port in self.ports:

                    await self.pulse_test(port)

                    await asyncio.sleep_ms(200)

            await asyncio.sleep_ms(self.period_ms)

    async def feedback_task(self, period_ms=200):

        info("Motor feedback task started")

        while True:

            try:

                for port in self.ports:

                    ticks = self.feedback.get(port)

                    self._notify(
                        "MTR_FB {} {}".format(port, ticks)
                    )

            except Exception as e:

                error("MTR_FB", e)

            await asyncio.sleep_ms(period_ms)