# robot/mpu6050.py
from machine import I2C, Pin
import struct
import time

class MPU6050:
    REG_PWR_MGMT_1   = 0x6B
    REG_SMPLRT_DIV   = 0x19
    REG_CONFIG       = 0x1A
    REG_GYRO_CONFIG  = 0x1B
    REG_ACCEL_CONFIG = 0x1C
    REG_ACCEL_XOUT_H = 0x3B

    def __init__(
        self,
        i2c_id=0,
        sda_gpio=21,
        scl_gpio=22,
        freq=400000,
        addr=0x68,
        mux=None,
        mux_channel=None,
    ):
        self.addr = addr
        self.i2c = I2C(i2c_id, sda=Pin(sda_gpio), scl=Pin(scl_gpio), freq=freq)
        self.mux = mux
        self.mux_channel = mux_channel
        self._buf14 = bytearray(14)

        self._wake()
        self._configure()

    def _select(self):
        if self.mux is not None and self.mux_channel is not None:
            self.mux.select(self.mux_channel)

    def _write8(self, reg, val):
        self._select()
        self.i2c.writeto_mem(self.addr, reg, bytes([val & 0xFF]))

    def _readinto(self, reg, buf):
        self._select()
        self.i2c.readfrom_mem_into(self.addr, reg, buf)

    def _wake(self):
        self._write8(self.REG_PWR_MGMT_1, 0x00)
        time.sleep_ms(100)

    def _configure(self):
        self._write8(self.REG_SMPLRT_DIV, 9)
        self._write8(self.REG_CONFIG, 0x03)
        self._write8(self.REG_GYRO_CONFIG, 0x00)
        self._write8(self.REG_ACCEL_CONFIG, 0x00)

    def read_scaled(self):
        self._readinto(self.REG_ACCEL_XOUT_H, self._buf14)
        ax, ay, az, temp_raw, gx, gy, gz = struct.unpack(">hhhhhhh", self._buf14)

        return {
            "ax_g": ax / 16384.0,
            "ay_g": ay / 16384.0,
            "az_g": az / 16384.0,
            "gx_dps": gx / 131.0,
            "gy_dps": gy / 131.0,
            "gz_dps": gz / 131.0,
            "temp_c": temp_raw / 340.0 + 36.53,
        }