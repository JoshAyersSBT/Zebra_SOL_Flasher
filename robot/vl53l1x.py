# robot/vl53l1x.py
import time


class VL53L1X:
    DEFAULT_ADDR = 0x29

    _REG_SOFT_RESET = 0x0000
    _REG_IDENTIFICATION__MODEL_ID = 0x010F
    _REG_GPIO__TIO_HV_STATUS = 0x0031
    _REG_SYSTEM__INTERRUPT_CLEAR = 0x0086
    _REG_SYSTEM__MODE_START = 0x0087
    _REG_RESULT__FINAL_CROSSTALK_CORRECTED_RANGE_MM_SD0 = 0x0096

    _REG_VHV_CONFIG__TIMEOUT_MACROP_LOOP_BOUND = 0x0008
    _REG_PHASECAL_CONFIG__OVERRIDE = 0x000B

    def __init__(self, i2c, address=DEFAULT_ADDR):
        self.i2c = i2c
        self.address = address

        addrs = self.i2c.scan()
        if self.address not in addrs:
            raise OSError("VL53L1X not found")

        time.sleep_ms(100)
        self.model_id = self._read_u16(self._REG_IDENTIFICATION__MODEL_ID)
        self._soft_reset()
        time.sleep_ms(50)

    def _write_reg(self, reg, data: bytes):
        reg_bytes = bytes([(reg >> 8) & 0xFF, reg & 0xFF])
        self.i2c.writeto(self.address, reg_bytes + data)

    def _read_reg(self, reg, nbytes):
        reg_bytes = bytes([(reg >> 8) & 0xFF, reg & 0xFF])
        self.i2c.writeto(self.address, reg_bytes, False)
        return self.i2c.readfrom(self.address, nbytes)

    def read_raw_block(self):
        # Read 17 bytes around the result area so we can inspect what changes.
        start_reg = 0x0089
        return self._read_reg(start_reg, 17)
    
    def read_debug(self, timeout_ms=200):
        start = time.ticks_ms()
        while not self.data_ready():
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("VL53L1X timeout waiting for data")
            time.sleep_ms(5)

        block_start = 0x0089
        raw = self._read_reg(block_start, 24)

        cand_96 = self._read_u16(0x0096)
        cand_9C = self._read_u16(0x009C)
        cand_A0 = self._read_u16(0x00A0)

        gpio_status = self._read_u8(self._REG_GPIO__TIO_HV_STATUS)

        self.clear_interrupt()

        return {
            "gpio_status": gpio_status,
            "cand_96": int(cand_96),
            "cand_9C": int(cand_9C),
            "cand_A0": int(cand_A0),
            "raw": raw,
        }

    def _write_u8(self, reg, value):
        self._write_reg(reg, bytes([value & 0xFF]))

    def _write_u16(self, reg, value):
        self._write_reg(reg, bytes([(value >> 8) & 0xFF, value & 0xFF]))

    def _read_u8(self, reg):
        return self._read_reg(reg, 1)[0]

    def _read_u16(self, reg):
        data = self._read_reg(reg, 2)
        return (data[0] << 8) | data[1]

    def _soft_reset(self):
        try:
            self._write_u8(self._REG_SOFT_RESET, 0x00)
            time.sleep_ms(5)
            self._write_u8(self._REG_SOFT_RESET, 0x01)
            time.sleep_ms(5)
        except Exception:
            pass

    def start(self):
        # Minimal attempt at continuous ranging start.
        try:
            self._write_u8(self._REG_VHV_CONFIG__TIMEOUT_MACROP_LOOP_BOUND, 0x09)
        except Exception:
            pass

        try:
            self._write_u8(self._REG_PHASECAL_CONFIG__OVERRIDE, 0x00)
        except Exception:
            pass

        self._write_u8(self._REG_SYSTEM__MODE_START, 0x40)
        time.sleep_ms(100)

    def stop(self):
        self._write_u8(self._REG_SYSTEM__MODE_START, 0x00)
        time.sleep_ms(5)

    def clear_interrupt(self):
        self._write_u8(self._REG_SYSTEM__INTERRUPT_CLEAR, 0x01)

    def data_ready(self):
        try:
            a = self._read_u8(self._REG_GPIO__TIO_HV_STATUS)
            time.sleep_ms(2)
            b = self._read_u8(self._REG_GPIO__TIO_HV_STATUS)
            return ((a & 0x01) == 0) and (a == b)
        except Exception:
            return False

    def read(self, timeout_ms=200):
        sample = self.read_debug(timeout_ms=timeout_ms)
        dist = sample["distance"]

        # Reject clearly bogus/stale values.
        if dist <= 0 or dist >= 4000 or dist == 5633 or dist == 65535:
            raise OSError("VL53L1X invalid range {}".format(dist))

        return dist

    @property
    def distance(self):
        return self.read()

    def ping(self):
        return self.read()

    def info(self):
        return {
            "address": self.address,
            "model_id": self.model_id,
        }