# robot/motor_scan.py

import uasyncio as asyncio
from robot.debug_io import info, error


class MotorScanner:
    """
    Optional motor scanner / feedback broadcaster.

    Compatible with lazy actuator claiming:
      - Motors may not exist until user code calls zbot.motor(port)
      - Active scanning is disabled by default
      - pulse_test() uses set_power() / stop() when available
      - No dependency on motor.max_duty
    """

    def __init__(
        self,
        motors,
        feedback,
        notify_fn=None,
        ports=(1, 2, 3, 4),
        scan_power=25,
        pulse_ms=250,
        period_ms=1500,
    ):
        # motors is a live dict from api.handles["motors"].
        # With lazy claiming, it may start empty and fill as user code claims ports.
        self.motors = motors
        self.feedback = feedback
        self.notify = notify_fn

        self.ports = tuple(int(p) for p in ports)
        self.scan_power = int(scan_power)
        self.pulse_ms = int(pulse_ms)
        self.period_ms = int(period_ms)

        # Safety default:
        # Do not actively pulse motors during student/user runtime.
        self.enabled = False

    def _notify(self, line):
        if self.notify is None:
            return
        try:
            self.notify(str(line))
        except Exception:
            pass

    def set_enabled(self, enabled=True):
        self.enabled = bool(enabled)
        self._notify("MTR_SCAN enabled={}".format(self.enabled))

    def enable(self):
        self.set_enabled(True)

    def disable(self):
        self.set_enabled(False)

    def is_enabled(self):
        return bool(self.enabled)

    def set_ports(self, ports):
        self.ports = tuple(int(p) for p in ports)

    def set_scan_power(self, power):
        power = int(power)
        if power < 0:
            power = 0
        if power > 100:
            power = 100
        self.scan_power = power

    async def pulse_test(self, port):
        """
        Pulse a single already-claimed motor briefly and measure encoder ticks.

        Notes:
          - This does not claim a port.
          - If the runtime uses lazy claiming, user code or diagnostics must
            create the motor first with zbot.motor(port) / api.set_motor(port, ...).
          - This routine is safe with the 3-pin Motor driver.
        """

        port = int(port)

        try:
            motor = self.motors.get(port)

            if motor is None:
                self._notify("MTR_ERR {} unsupported_or_unclaimed_port".format(port))
                return False

            try:
                self.feedback.reset(port)
            except Exception:
                pass

            # Prefer the modern signed percent API.
            if hasattr(motor, "set_power"):
                motor.set_power(self.scan_power)
            else:
                # Legacy fallback: logical duty in u16 range.
                duty = (self.scan_power * 65535) // 100
                motor.set(True, duty)

            await asyncio.sleep_ms(self.pulse_ms)

            if hasattr(motor, "stop"):
                motor.stop()
            elif hasattr(motor, "set_power"):
                motor.set_power(0)
            else:
                motor.set(True, 0)

            try:
                ticks = self.feedback.get(port)
            except Exception:
                ticks = 0

            self._notify(
                "MTR_SCAN {} power={} ticks={}".format(
                    port,
                    self.scan_power,
                    ticks,
                )
            )
            return True

        except Exception as e:
            error("MTR_SCAN_{}".format(port), e)
            self._notify("MTR_ERR {} scan_failed".format(port))
            return False

    async def task(self):
        """
        Optional active scan task.

        Keep self.enabled False during normal user runtime. If enabled by a
        diagnostic command, it only scans ports that already have motor objects.
        """

        info("MotorScanner task started")

        while True:
            try:
                if self.enabled:
                    for port in self.ports:
                        await self.pulse_test(port)
                        await asyncio.sleep_ms(200)

            except Exception as e:
                error("MTR_SCAN_TASK", e)

            await asyncio.sleep_ms(self.period_ms)

    async def feedback_task(self, period_ms=200):
        """
        Broadcast encoder feedback only.

        This does not command motor outputs and is safe to run while user code
        controls motors.
        """

        info("Motor feedback task started")

        while True:
            try:
                for port in self.ports:
                    try:
                        ticks = self.feedback.get(port)
                    except Exception:
                        ticks = 0

                    self._notify("MTR_FB {} {}".format(port, ticks))

            except Exception as e:
                error("MTR_FB", e)

            await asyncio.sleep_ms(int(period_ms))
