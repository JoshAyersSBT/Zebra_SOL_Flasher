# robot/oled_status.py
from machine import I2C, Pin
import uasyncio as asyncio

try:
    import ssd1306
except ImportError:
    ssd1306 = None


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
        mux_channel=None,
    ):
        self.available = False
        self.width = width
        self.height = height
        self.addr = addr
        self.mux = mux
        self.mux_channel = mux_channel
        self._flash_task = None

        if ssd1306 is None:
            print("OLED: ssd1306 module not found")
            return

        try:
            self.i2c = I2C(i2c_id, sda=Pin(sda_gpio), scl=Pin(scl_gpio), freq=400000)
            self._select()
            self.oled = ssd1306.SSD1306_I2C(width, height, self.i2c, addr=addr)
            self.available = True
            self.clear()
        except Exception as e:
            print("OLED init failed:", e)

    def _select(self):
        if self.mux is not None and self.mux_channel is not None:
            self.mux.select(self.mux_channel)

    def clear(self):
        if not self.available:
            return
        self._select()
        self.oled.fill(0)
        self.oled.show()

    def show_lines(self, *lines):
        if not self.available:
            return
        self._select()
        self.oled.fill(0)
        y = 0
        for line in lines[:6]:
            self.oled.text(str(line), 0, y)
            y += 10
        self.oled.show()

    async def flash(self, times=4, on_ms=120, off_ms=120):
        if not self.available:
            return
        for _ in range(times):
            self._select()
            self.oled.fill(1)
            self.oled.show()
            await asyncio.sleep_ms(on_ms)

            self._select()
            self.oled.fill(0)
            self.oled.show()
            await asyncio.sleep_ms(off_ms)

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