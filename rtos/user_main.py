# user_main.py
import uasyncio as asyncio

from zebra.i2c_mux import TCA9548A
from zebra.screen import ZebraScreen
from zebra.tof import ZebraTOF
from zebra.colour import ZebraColour
from zebra.gyro import ZebraGyro
from zebra.servo import ZebraServo
from zebra.smotor_pair import SMotorPair


# ---- YOU MUST SET THESE FOR YOUR HARDWARE ----
MOTOR_PORT_MAP = {
    # port: pwm/dir/enc GPIOs
    1: {"pwm": 5, "dir": 6, "enc": 7, "invert": False, "ticks_per_rev": 610},
    2: {"pwm": 8, "dir": 9, "enc": 10, "invert": True, "ticks_per_rev": 610},
}

SERVO_PORT_TO_PIN = {
    1: 18,
}

# I2C mux setup
MUX_ADDR = 0x70

# mux channel assignment (example)
MUX_PORT_SCREEN = 0
MUX_PORT_TOF = 1
MUX_PORT_GYRO = 2
MUX_PORT_COLOUR = 3
# ---------------------------------------------


async def main(reg):
    if "app" not in reg.status:
        reg.status["app"] = {}

    # One-time hardware bring-up
    if not reg.status["app"].get("hw_init"):
        if getattr(reg, "i2c", None) is None:
            reg.log.warn("user_main: reg.i2c is None; I2CManager may be disabled or failed")
        else:
            i2c = reg.i2c
            mux = TCA9548A(i2c, addr=MUX_ADDR)

            # Create devices
            screen = ZebraScreen(MUX_PORT_SCREEN, i2c, mux)
            screen.begin()

            motors = SMotorPair(1, 2, port_map=MOTOR_PORT_MAP)
            motors.begin()

            servo1 = ZebraServo(1, port_to_pin=SERVO_PORT_TO_PIN)
            servo1.begin()

            tof = ZebraTOF(MUX_PORT_TOF, i2c, mux)
            tof.begin()

            gyro = ZebraGyro(MUX_PORT_GYRO, i2c, mux, driver_module="mpu6050")
            gyro.begin()

            colour = ZebraColour(MUX_PORT_COLOUR, i2c, mux)
            colour.begin()

            # Attach to control plane
            reg.control_plane_devices["screen"] = screen
            reg.control_plane_devices["motors"] = motors
            reg.control_plane_devices["servo1"] = servo1
            reg.control_plane_devices["tof"] = tof
            reg.control_plane_devices["gyro"] = gyro
            reg.control_plane_devices["colour"] = colour

            # Friendly message
            try:
                screen.writeLine(0, "CP + Zebra ready")
            except Exception:
                pass

        reg.status["app"]["hw_init"] = True

    # Main loop
    n = 0
    while True:
        n += 1
        reg.status["app"]["tick"] = n

        # Optional: periodically poll sensors through CP pipeline
        # (or just do it directly)
        try:
            fn = getattr(reg, "control_plane_submit_str", None)
            if fn is not None:
                await fn('{"op":"sensor.poll"}')
        except Exception:
            pass

        await asyncio.sleep_ms(250)