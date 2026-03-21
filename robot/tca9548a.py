class TCA9548A:
    def __init__(self, i2c, addr=0x70):
        self.i2c = i2c
        self.addr = addr
        self.current = None

    def select(self, channel: int):
        if channel < 0 or channel > 7:
            raise ValueError("TCA9548A channel must be 0..7")
        self.i2c.writeto(self.addr, bytes([1 << channel]))
        self.current = channel

    def disable_all(self):
        self.i2c.writeto(self.addr, b"\\x00")
        self.current = None