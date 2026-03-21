# services/status_monitor.py
import uasyncio as asyncio
import utime
import gc

class StatusMonitor:
    def __init__(self, reg):
        self.reg = reg
        self.period_ms = int(reg.cfg.get("status", {}).get("period_ms", 500))
        self.gc_period_ms = int(reg.cfg.get("status", {}).get("gc_collect_period_ms", 5000))
        self._last_gc = utime.ticks_ms()
        self._ci = reg.add_checkin("status", max_age_ms=self.period_ms * 4)

    async def task(self):
        # loop timing model
        next_ms = utime.ticks_add(utime.ticks_ms(), self.period_ms)

        while True:
            now = utime.ticks_ms()

            # compute lag (how late we are)
            lag = utime.ticks_diff(now, next_ms)
            if lag < 0:
                lag = 0

            # crude load estimate: lag vs period (clamp 0..100)
            load_pct = int(min(100, (lag * 100) // max(1, self.period_ms)))

            # memory
            mem_free = gc.mem_free()
            mem_alloc = gc.mem_alloc()

            self.reg.status["uptime_ms"] = self.reg.uptime_ms()
            self.reg.status["mem_free"] = mem_free
            self.reg.status["mem_alloc"] = mem_alloc
            self.reg.status["loop_lag_ms"] = lag
            self.reg.status["load_pct"] = load_pct

            # periodic gc
            if utime.ticks_diff(now, self._last_gc) >= self.gc_period_ms:
                gc.collect()
                self._last_gc = now

            self._ci.mark()
            next_ms = utime.ticks_add(next_ms, self.period_ms)
            await asyncio.sleep_ms(self.period_ms)