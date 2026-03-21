# services/watchdog.py
import uasyncio as asyncio

class WatchdogService:
    def __init__(self, reg):
        self.reg = reg
        self.wdt = None
        self.enabled = bool(reg.cfg.get("watchdog", {}).get("enabled", True))
        self.timeout_ms = int(reg.cfg.get("watchdog", {}).get("timeout_ms", 6000))

        # NEW: include cp
        self.critical_keys = ["status", "i2c", "ble", "cp"]

    def start(self):
        if not self.enabled:
            self.reg.log.warn("WDT disabled")
            return
        try:
            import machine
            self.wdt = machine.WDT(timeout=self.timeout_ms)
            self.reg.log.info("WDT started timeout_ms=%d" % self.timeout_ms)
        except Exception as e:
            self.reg.log.error("WDT start failed: %r" % (e,))
            self.enabled = False

    async def task(self):
        if not self.enabled or (self.wdt is None):
            while True:
                await asyncio.sleep_ms(1000)

        while True:
            ok = self.reg.all_critical_ok(self.critical_keys)
            if ok:
                self.wdt.feed()
            await asyncio.sleep_ms(max(250, self.timeout_ms // 4))