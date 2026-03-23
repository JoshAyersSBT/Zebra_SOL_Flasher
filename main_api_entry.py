# rtos/main.py
import sys
import uasyncio as asyncio
import machine

if "/rtos" not in sys.path:
    sys.path.insert(0, "/rtos")
if "/user" not in sys.path:
    sys.path.insert(0, "/user")

import sysapi
from syscfg import load_cfg
from sysstate import Registry

from services.logger import Logger
from services.watchdog import WatchdogService
from services.status_monitor import StatusMonitor
from services.i2c_manager import I2CManager
from services.ble_manager import BLEManager


class SystemAPI:
    """
    Stable user-facing API surface.
    User programs should interact with this object rather than reaching into
    Registry internals directly.
    """
    def __init__(self, reg: Registry):
        self.reg = reg
        self.cfg = reg.cfg

        # Ensure expected status branches exist.
        st = reg.status
        st.setdefault("api", {})
        st.setdefault("user", {})
        st.setdefault("sensors", {})
        st.setdefault("motors", {})
        st.setdefault("actuators", {})
        st.setdefault("display", {"lines": []})
        st.setdefault("project", {"name": None, "running": False, "crash_count": 0})
        st["api"]["ready"] = True

    # ---------- general ----------
    def get_status(self):
        return self.reg.status

    def get_cfg(self):
        return self.cfg

    def uptime_ms(self):
        try:
            return self.reg.uptime_ms()
        except Exception:
            return 0

    def sleep_ms(self, ms: int):
        return asyncio.sleep_ms(ms)

    # ---------- logs ----------
    def info(self, msg):
        try:
            self.reg.log.info(msg)
        except Exception:
            print(msg)

    def warn(self, msg):
        try:
            self.reg.log.warn(msg)
        except Exception:
            print(msg)

    def error(self, msg):
        try:
            self.reg.log.error(msg)
        except Exception:
            print(msg)

    # ---------- display ----------
    def show_lines(self, *lines):
        cooked = []
        for line in lines:
            cooked.append("" if line is None else str(line))
        self.reg.status["display"]["lines"] = cooked
        return cooked

    def get_display(self):
        return self.reg.status.get("display", {})

    # ---------- sensors ----------
    def publish_sensor(self, name, value=None, **fields):
        entry = {"value": value}
        entry.update(fields)
        self.reg.status["sensors"][name] = entry
        return entry

    def get_sensor(self, name, default=None):
        return self.reg.status.get("sensors", {}).get(name, default)

    def get_sensors(self):
        return self.reg.status.get("sensors", {})

    def get_sensor_snapshot(self):
        return self.get_sensors()

    # ---------- motors ----------
    def publish_motor(self, name, state=None, power=None, **fields):
        entry = {}
        if state is not None:
            entry["state"] = state
        if power is not None:
            entry["power"] = power
        entry.update(fields)
        self.reg.status["motors"][name] = entry
        return entry

    def get_motor(self, name, default=None):
        return self.reg.status.get("motors", {}).get(name, default)

    def get_motors(self):
        return self.reg.status.get("motors", {})

    def stop_all(self):
        motors = self.reg.status.get("motors", {})
        for name in motors:
            motors[name]["state"] = "stopped"
            motors[name]["power"] = 0
        return motors

    # ---------- project/app ----------
    def set_project_name(self, name):
        self.reg.status["project"]["name"] = name

    def set_user_flag(self, key, value):
        self.reg.status["user"][key] = value

    def get_user_flag(self, key, default=None):
        return self.reg.status.get("user", {}).get(key, default)


def _import_user_entry():
    """
    Import order for the user domain.
    1) /user/project.py    -> module name 'project' due to /user on sys.path
    2) /project.py         -> module name 'project'
    3) /user/user_main.py  -> module name 'user_main'
    4) /user_main.py       -> module name 'user_main'
    """
    # project.py is the primary contract.
    try:
        import project
        return "project", project
    except ImportError:
        pass

    try:
        import user_main
        return "user_main", user_main
    except ImportError:
        pass

    return None, None


async def run_user_app(reg: Registry):
    while True:
        try:
            mod_name, mod = _import_user_entry()
            if mod is None:
                reg.log.warn("No user entry found; expected /user/project.py, /project.py, /user/user_main.py, or /user_main.py")
                await asyncio.sleep_ms(1000)
                continue

            api = reg.api
            api.set_project_name(mod_name)
            reg.status["project"]["running"] = True
            reg.log.info("Starting %s.main(api)" % mod_name)

            if hasattr(mod, "main"):
                await mod.main(api)
                reg.log.warn("%s.main exited; restarting" % mod_name)
            else:
                reg.log.warn("%s has no async main(api); sleeping" % mod_name)
                await asyncio.sleep_ms(1000)
        except Exception as e:
            reg.status["project"]["running"] = False
            reg.status["project"]["crash_count"] = reg.status["project"].get("crash_count", 0) + 1
            try:
                reg.log.error("user project crashed: %r" % (e,))
            except Exception:
                print("user project crashed:", repr(e))
            await asyncio.sleep_ms(1000)


async def supervisor():
    log = Logger()

    cfg = load_cfg()
    reg = Registry(cfg)
    reg.log = log

    # Expose registry immediately to tools.
    sysapi.set_registry(reg)

    # Attach public API for user programs and REPL tools.
    reg.api = SystemAPI(reg)

    # Basic boot metadata.
    try:
        reg.status["reset_cause"] = machine.reset_cause()
    except Exception:
        reg.status["reset_cause"] = None

    try:
        reg.log.info("Supervisor boot; hostname=%s" % cfg.get("hostname", "mp-rtos"))
    except Exception:
        print("Supervisor boot")

    # Services.
    status = StatusMonitor(reg)
    i2c = I2CManager(reg)
    ble = BLEManager(reg)
    wdt = WatchdogService(reg)

    try:
        i2c.start()
    except Exception as e:
        reg.log.warn("i2c.start failed: %r" % (e,))
    try:
        ble.start()
    except Exception as e:
        reg.log.warn("ble.start failed: %r" % (e,))
    try:
        wdt.start()
    except Exception as e:
        reg.log.warn("wdt.start failed: %r" % (e,))

    # Schedule core tasks.
    asyncio.create_task(status.task())
    asyncio.create_task(i2c.task_scan())
    asyncio.create_task(ble.task())
    asyncio.create_task(wdt.task())

    # User project supervised.
    asyncio.create_task(run_user_app(reg))

    while True:
        await asyncio.sleep_ms(2000)


def boot():
    asyncio.run(supervisor())


if __name__ == "__main__":
    boot()
