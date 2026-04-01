# robot/oled_status.py
from machine import I2C, Pin
import uasyncio as asyncio
import framebuf
import time


class SH1106_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.external_vcc = external_vcc

        self.pages = self.height // 8
        self.buf = bytearray(self.width * self.pages)
        super().__init__(self.buf, self.width, self.height, framebuf.MONO_VLSB)

        # Many 128x64 SH1106 displays need a +2 column offset
        self.column_offset = 2

        self.poweron()
        self.init_display()
        self.fill(0)
        self.show()

    def write_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytearray((0x80, cmd)))

    def write_data(self, buf):
        self.i2c.writeto(self.addr, b"\x40" + buf)

    def poweron(self):
        pass

    def poweroff(self):
        self.write_cmd(0xAE)

    def contrast(self, contrast):
        self.write_cmd(0x81)
        self.write_cmd(contrast)

    def invert(self, invert):
        self.write_cmd(0xA7 if invert else 0xA6)

    def init_display(self):
        # SH1106 128x64 init
        for cmd in (
            0xAE,       # display off
            0xD5, 0x80, # clock divide
            0xA8, 0x3F, # multiplex = 64-1
            0xD3, 0x00, # display offset
            0x40,       # start line = 0
            0xAD, 0x8B, # DC-DC on
            0xA1,       # segment remap
            0xC8,       # COM scan dec
            0xDA, 0x12, # COM pins
            0x81, 0x7F, # contrast
            0xD9, 0x22, # pre-charge
            0xDB, 0x35, # VCOM detect
            0xA4,       # display follows RAM
            0xA6,       # normal display
            0xAF,       # display on
        ):
            self.write_cmd(cmd)

    def show(self):
        # Write one page at a time
        for page in range(self.pages):
            self.write_cmd(0xB0 | page)  # page address
            self.write_cmd(0x02)         # lower column address (offset = 2)
            self.write_cmd(0x10)         # higher column address
            start = self.width * page
            end = start + self.width
            self.write_data(self.buf[start:end])


class OledStatus:
    def __init__(
        self,
        i2c_id=0,
        sda_gpio=21,
        scl_gpio=22,
        width=128,
        height=64,
        addr=0x3C,
        mux=None,
        mux_channel=0,   # C++ code uses ZebraScreen screen(0)
        freq=400000,
    ):
        self.available = False
        self.width = width
        self.height = height
        self.addr = addr
        self.mux = mux
        self.mux_channel = mux_channel
        self.freq = freq
        self._flash_task = None

        self.i2c = None
        self.oled = None

        try:
            self.i2c = I2C(i2c_id, sda=Pin(sda_gpio), scl=Pin(scl_gpio), freq=freq)

            self._select()
            time.sleep_ms(20)

            found = self.i2c.scan()
            if addr not in found:
                print("OLED not found on selected bus/channel. scan =", [hex(x) for x in found])
                return

            self.oled = SH1106_I2C(width, height, self.i2c, addr=addr)
            self.available = True
            self.clear()
            print("OLED ready: SH1106 addr=%s mux_channel=%s" % (hex(addr), str(mux_channel)))
        except Exception as e:
            print("OLED init failed:", e)

    def _select(self):
        if self.mux is not None and self.mux_channel is not None:
            self.mux.select(self.mux_channel)

    def clear(self):
        if not self.available:
            return
        try:
            self._select()
            self.oled.fill(0)
            self.oled.show()
        except Exception as e:
            print("OLED clear failed:", e)
            self.available = False

    def show_lines(self, *lines):
        if not self.available:
            return
        try:
            self._select()
            self.oled.fill(0)

            y = 0
            for line in lines[:6]:
                self.oled.text(str(line), 0, y, 1)
                y += 10

            self.oled.show()
        except Exception as e:
            print("OLED show_lines failed:", e)
            self.available = False

    async def flash(self, times=4, on_ms=120, off_ms=120):
        if not self.available:
            return
        try:
            for _ in range(times):
                self._select()
                self.oled.fill(1)
                self.oled.show()
                await asyncio.sleep_ms(on_ms)

                self._select()
                self.oled.fill(0)
                self.oled.show()
                await asyncio.sleep_ms(off_ms)
        except Exception as e:
            print("OLED flash failed:", e)
            self.available = False

    def flash_connected(self):
        if not self.available:
            return
        try:
            if self._flash_task is not None:
                self._flash_task.cancel()
        except Exception:
            pass
        self._flash_task = asyncio.create_task(self._flash_connected_task())

    async def _flash_connected_task(self):
        await self.flash(times=4, on_ms=100, off_ms=100)
        self.show_lines("ZebraBot", "BLE Connected")