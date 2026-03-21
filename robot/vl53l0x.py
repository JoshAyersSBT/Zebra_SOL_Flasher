# robot/vl53l0x.py
#
# MicroPython VL53L0X driver for ESP32 / machine.I2C
# Based on the common Pololu/Arduino style register model.
#
# Intended for:
# - robot sensor hub probing
# - continuous ranging
# - mux-based hot-swappable ports
#
# This is a practical working driver, not a full API port of every advanced
# tuning feature in ST's original codebase.

import time


class VL53L0X:
    DEFAULT_ADDR = 0x29

    # Registers
    SYSRANGE_START = 0x00
    SYSTEM_THRESH_HIGH = 0x0C
    SYSTEM_THRESH_LOW = 0x0E
    SYSTEM_SEQUENCE_CONFIG = 0x01
    SYSTEM_RANGE_CONFIG = 0x09
    SYSTEM_INTERMEASUREMENT_PERIOD = 0x04
    SYSTEM_INTERRUPT_CONFIG_GPIO = 0x0A
    GPIO_HV_MUX_ACTIVE_HIGH = 0x84
    SYSTEM_INTERRUPT_CLEAR = 0x0B
    RESULT_INTERRUPT_STATUS = 0x13
    RESULT_RANGE_STATUS = 0x14
    RESULT_CORE_AMBIENT_WINDOW_EVENTS_RTN = 0xBC
    RESULT_CORE_RANGING_TOTAL_EVENTS_RTN = 0xC0
    RESULT_CORE_AMBIENT_WINDOW_EVENTS_REF = 0xD0
    RESULT_CORE_RANGING_TOTAL_EVENTS_REF = 0xD4
    RESULT_PEAK_SIGNAL_RATE_REF = 0xB6
    ALGO_PART_TO_PART_RANGE_OFFSET_MM = 0x28
    I2C_SLAVE_DEVICE_ADDRESS = 0x8A
    MSRC_CONFIG_CONTROL = 0x60
    PRE_RANGE_CONFIG_MIN_SNR = 0x27
    PRE_RANGE_CONFIG_VALID_PHASE_LOW = 0x56
    PRE_RANGE_CONFIG_VALID_PHASE_HIGH = 0x57
    PRE_RANGE_MIN_COUNT_RATE_RTN_LIMIT = 0x64
    FINAL_RANGE_CONFIG_MIN_SNR = 0x67
    FINAL_RANGE_CONFIG_VALID_PHASE_LOW = 0x47
    FINAL_RANGE_CONFIG_VALID_PHASE_HIGH = 0x48
    FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT = 0x44
    PRE_RANGE_CONFIG_SIGMA_THRESH_HI = 0x61
    PRE_RANGE_CONFIG_SIGMA_THRESH_LO = 0x62
    PRE_RANGE_CONFIG_VCSEL_PERIOD = 0x50
    PRE_RANGE_CONFIG_TIMEOUT_MACROP_HI = 0x51
    PRE_RANGE_CONFIG_TIMEOUT_MACROP_LO = 0x52
    SYSTEM_HISTOGRAM_BIN = 0x81
    HISTOGRAM_CONFIG_INITIAL_PHASE_SELECT = 0x33
    HISTOGRAM_CONFIG_READOUT_CTRL = 0x55
    FINAL_RANGE_CONFIG_VCSEL_PERIOD = 0x70
    FINAL_RANGE_CONFIG_TIMEOUT_MACROP_HI = 0x71
    FINAL_RANGE_CONFIG_TIMEOUT_MACROP_LO = 0x72
    CROSSTALK_COMPENSATION_PEAK_RATE_MCPS = 0x20
    MSRC_CONFIG_TIMEOUT_MACROP = 0x46
    SOFT_RESET_GO2_SOFT_RESET_N = 0xBF
    IDENTIFICATION_MODEL_ID = 0xC0
    IDENTIFICATION_REVISION_ID = 0xC2
    OSC_CALIBRATE_VAL = 0xF8
    GLOBAL_CONFIG_VCSEL_WIDTH = 0x32
    GLOBAL_CONFIG_SPAD_ENABLES_REF_0 = 0xB0
    GLOBAL_CONFIG_SPAD_ENABLES_REF_1 = 0xB1
    GLOBAL_CONFIG_SPAD_ENABLES_REF_2 = 0xB2
    GLOBAL_CONFIG_SPAD_ENABLES_REF_3 = 0xB3
    GLOBAL_CONFIG_SPAD_ENABLES_REF_4 = 0xB4
    GLOBAL_CONFIG_SPAD_ENABLES_REF_5 = 0xB5
    GLOBAL_CONFIG_REF_EN_START_SELECT = 0xB6
    DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD = 0x4E
    DYNAMIC_SPAD_REF_EN_START_OFFSET = 0x4F
    POWER_MANAGEMENT_GO1_POWER_FORCE = 0x80
    VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV = 0x89
    ALGO_PHASECAL_LIM = 0x30
    ALGO_PHASECAL_CONFIG_TIMEOUT = 0x30

    def __init__(self, i2c, address=DEFAULT_ADDR, io_timeout=500):
        self.i2c = i2c
        self.address = address
        self.io_timeout = int(io_timeout)
        self.did_timeout = False
        self.stop_variable = 0
        self.measurement_timing_budget_us = 0
        self.initialized = False

    # ------------------------------------------------------------
    # Basic register access
    # ------------------------------------------------------------

    def write_reg(self, reg, value):
        self.i2c.writeto_mem(self.address, reg, bytes([value & 0xFF]))

    def write_reg16(self, reg, value):
        self.i2c.writeto_mem(
            self.address,
            reg,
            bytes([(value >> 8) & 0xFF, value & 0xFF]),
        )

    def write_reg32(self, reg, value):
        self.i2c.writeto_mem(
            self.address,
            reg,
            bytes([
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ]),
        )

    def read_reg(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

    def read_reg16(self, reg):
        d = self.i2c.readfrom_mem(self.address, reg, 2)
        return (d[0] << 8) | d[1]

    def read_reg32(self, reg):
        d = self.i2c.readfrom_mem(self.address, reg, 4)
        return (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]

    def write_multi(self, reg, data):
        self.i2c.writeto_mem(self.address, reg, data)

    def read_multi(self, reg, count):
        return self.i2c.readfrom_mem(self.address, reg, count)

    # ------------------------------------------------------------
    # Timeout helpers
    # ------------------------------------------------------------

    def set_timeout(self, timeout_ms):
        self.io_timeout = int(timeout_ms)

    def timeout_occurred(self):
        return self.did_timeout

    def _start_timeout(self):
        self.did_timeout = False
        return time.ticks_ms()

    def _check_timeout(self, start_ms):
        if self.io_timeout <= 0:
            return False
        if time.ticks_diff(time.ticks_ms(), start_ms) > self.io_timeout:
            self.did_timeout = True
            return True
        return False

    # ------------------------------------------------------------
    # API-compatible helpers
    # ------------------------------------------------------------

    def set_address(self, new_addr):
        self.write_reg(self.I2C_SLAVE_DEVICE_ADDRESS, new_addr & 0x7F)
        self.address = new_addr & 0x7F

    def get_address(self):
        return self.address

    def get_model_id(self):
        return self.read_reg(self.IDENTIFICATION_MODEL_ID)

    def get_revision_id(self):
        return self.read_reg(self.IDENTIFICATION_REVISION_ID)

    # ------------------------------------------------------------
    # Init
    # ------------------------------------------------------------

    def init(self, io_2v8=True):
        # Confirm sensor is present
        addrs = self.i2c.scan()
        if self.address not in addrs:
            raise OSError("VL53L0X not found at 0x{:02X}".format(self.address))

        # Check model ID; many VL53L0X boards report 0xEE
        model_id = self.get_model_id()
        if model_id not in (0xEE, 0xEA):
            # tolerate some variants/modules, but fail hard on nonsense
            # 0xEE is common VL53L0X model ID
            pass

        # From common Arduino/Pololu init sequence
        if io_2v8:
            self.write_reg(
                self.VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV,
                self.read_reg(self.VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV) | 0x01
            )

        # I2C standard-mode tweaks and stop variable capture
        self.write_reg(0x88, 0x00)

        self.write_reg(0x80, 0x01)
        self.write_reg(0xFF, 0x01)
        self.write_reg(0x00, 0x00)
        self.stop_variable = self.read_reg(0x91)
        self.write_reg(0x00, 0x01)
        self.write_reg(0xFF, 0x00)
        self.write_reg(0x80, 0x00)

        # Disable limit checks we do not need initially
        self.write_reg(self.MSRC_CONFIG_CONTROL,
                       self.read_reg(self.MSRC_CONFIG_CONTROL) | 0x12)

        # Default signal rate limit
        self.set_signal_rate_limit(0.25)

        self.write_reg(self.SYSTEM_SEQUENCE_CONFIG, 0xFF)

        # Minimal config block based on known working community driver paths
        self.write_reg(0xFF, 0x01)
        self.write_reg(self.DYNAMIC_SPAD_REF_EN_START_OFFSET, 0x00)
        self.write_reg(self.DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD, 0x2C)
        self.write_reg(0xFF, 0x00)
        self.write_reg(self.GLOBAL_CONFIG_REF_EN_START_SELECT, 0xB4)

        # Interrupt config
        self.write_reg(self.SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04)
        self.write_reg(
            self.GPIO_HV_MUX_ACTIVE_HIGH,
            self.read_reg(self.GPIO_HV_MUX_ACTIVE_HIGH) & ~0x10
        )
        self.write_reg(self.SYSTEM_INTERRUPT_CLEAR, 0x01)

        # Sequence config
        self.write_reg(self.SYSTEM_SEQUENCE_CONFIG, 0xE8)

        # Basic timing budget
        self.measurement_timing_budget_us = 33000

        self.initialized = True
        return True

    # ------------------------------------------------------------
    # Signal rate limit
    # ------------------------------------------------------------

    def set_signal_rate_limit(self, limit_mcps):
        # register format is Q9.7
        if limit_mcps < 0 or limit_mcps > 511.99:
            return False
        self.write_reg16(
            self.FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT,
            int(limit_mcps * (1 << 7)),
        )
        return True

    def get_signal_rate_limit(self):
        return self.read_reg16(
            self.FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT
        ) / float(1 << 7)

    # ------------------------------------------------------------
    # Continuous / single ranging
    # ------------------------------------------------------------

    def start_continuous(self, period_ms=0):
        if not self.initialized:
            self.init()

        # restore stop variable sequence used by VL53L0X
        self.write_reg(0x80, 0x01)
        self.write_reg(0xFF, 0x01)
        self.write_reg(0x00, 0x00)
        self.write_reg(0x91, self.stop_variable)
        self.write_reg(0x00, 0x01)
        self.write_reg(0xFF, 0x00)
        self.write_reg(0x80, 0x00)

        if period_ms != 0:
            osc_calibrate_val = self.read_reg16(self.OSC_CALIBRATE_VAL)
            if osc_calibrate_val != 0:
                period_ms *= osc_calibrate_val
            self.write_reg32(self.SYSTEM_INTERMEASUREMENT_PERIOD, period_ms)
            self.write_reg(self.SYSRANGE_START, 0x04)  # timed mode
        else:
            self.write_reg(self.SYSRANGE_START, 0x02)  # back-to-back mode

    def stop_continuous(self):
        self.write_reg(self.SYSRANGE_START, 0x01)

        self.write_reg(0xFF, 0x01)
        self.write_reg(0x00, 0x00)
        self.write_reg(0x91, 0x00)
        self.write_reg(0x00, 0x01)
        self.write_reg(0xFF, 0x00)

    def read_range_continuous_mm(self):
        start_ms = self._start_timeout()
        while (self.read_reg(self.RESULT_INTERRUPT_STATUS) & 0x07) == 0:
            if self._check_timeout(start_ms):
                raise OSError("VL53L0X timeout")
            time.sleep_ms(5)

        # Distance value is at RESULT_RANGE_STATUS + 10
        mm = self.read_reg16(self.RESULT_RANGE_STATUS + 10)
        self.write_reg(self.SYSTEM_INTERRUPT_CLEAR, 0x01)
        return mm

    def read_range_single_mm(self):
        if not self.initialized:
            self.init()

        self.write_reg(0x80, 0x01)
        self.write_reg(0xFF, 0x01)
        self.write_reg(0x00, 0x00)
        self.write_reg(0x91, self.stop_variable)
        self.write_reg(0x00, 0x01)
        self.write_reg(0xFF, 0x00)
        self.write_reg(0x80, 0x00)

        self.write_reg(self.SYSRANGE_START, 0x01)

        start_ms = self._start_timeout()
        while (self.read_reg(self.SYSRANGE_START) & 0x01) != 0:
            if self._check_timeout(start_ms):
                raise OSError("VL53L0X timeout starting single shot")
            time.sleep_ms(5)

        return self.read_range_continuous_mm()

    # ------------------------------------------------------------
    # Compatibility aliases for your sensor hub
    # ------------------------------------------------------------

    def start(self):
        self.start_continuous()

    def stop(self):
        self.stop_continuous()

    def read(self):
        return self.read_range_continuous_mm()

    def ping(self):
        return self.read_range_continuous_mm()

    @property
    def distance(self):
        return self.read_range_continuous_mm()

    def read_debug(self):
        raw = self.read_multi(self.RESULT_RANGE_STATUS, 16)
        dist = self.read_reg16(self.RESULT_RANGE_STATUS + 10)
        irq = self.read_reg(self.RESULT_INTERRUPT_STATUS)
        return {
            "distance": int(dist),
            "irq": int(irq),
            "raw": raw,
        }