# sysstate.py
import utime

class CheckIn:
    """
    Cooperative watchdog gate:
    critical tasks call mark() periodically.
    Watchdog task only feeds if all critical checkins are fresh.
    """
    def __init__(self, name: str, max_age_ms: int):
        self.name = name
        self.max_age_ms = max_age_ms
        self.last_ms = 0

    def mark(self):
        self.last_ms = utime.ticks_ms()

    def ok(self):
        if self.last_ms == 0:
            return False
        age = utime.ticks_diff(utime.ticks_ms(), self.last_ms)
        return age <= self.max_age_ms


class Registry:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.start_ms = utime.ticks_ms()

        # shared live status
        self.status = {
            "uptime_ms": 0,
            "mem_free": 0,
            "mem_alloc": 0,
            "loop_lag_ms": 0,   # event loop jitter
            "load_pct": 0,      # computed estimate
            "i2c": {"bus": None, "devices": [], "errors": 0, "last_scan_ms": 0},
            "ble": {"connected": False, "conn_count": 0},
            "reset_cause": None,
        }

        # checkins
        self.checkins = {}
        self.log = None  # logger service set later

    def add_checkin(self, key: str, max_age_ms: int):
        ci = CheckIn(key, max_age_ms)
        self.checkins[key] = ci
        return ci

    def all_critical_ok(self, keys):
        for k in keys:
            ci = self.checkins.get(k)
            if (ci is None) or (not ci.ok()):
                return False
        return True

    def uptime_ms(self):
        import utime
        return utime.ticks_diff(utime.ticks_ms(), self.start_ms)