# rtos/main.py
import sys
import uasyncio as asyncio
import machine

if "/rtos" not in sys.path:
    sys.path.insert(0, "/rtos")

import sysapi
from syscfg import load_cfg
from sysstate import Registry

from services.logger import Logger
from services.watchdog import WatchdogService
from services.status_monitor import StatusMonitor
from services.i2c_manager import I2CManager
from services.ble_manager import BLEManager


async def run_user_app(reg: Registry):
    while True:
        try:
            import user_main
            if hasattr(user_main, "main"):
                reg.log.info("Starting user_main.main(reg)")
                await user_main.main(reg)
            else:
                reg.log.warn("user_main has no async main(reg); sleeping")
                await asyncio.sleep_ms(1000)
        except Exception as e:
            # Never let user code kill the supervisor
            try:
                reg.log.error("user_main crashed: %r" % (e,))
            except Exception:
                print("user_main crashed:", repr(e))
            await asyncio.sleep_ms(1000)


def _boot_demo():
    # IMPORTANT: no reg.log usage here; prints only.
    try:
        import syscli
        print("=== Demo Start ===")
        syscli.neofetch()
        print("=== Demo End ===")
    except Exception as e:
        print("BOOT DEMO ERROR:", repr(e))


async def supervisor():
    # Make logging available ASAP
    log = Logger()

    cfg = load_cfg()
    reg = Registry(cfg)

    # Attach logger immediately so reg.log is never None
    reg.log = log

    # Expose registry to REPL tools immediately
    sysapi.set_registry(reg)

    # Basic boot metadata
    try:
        reg.status["reset_cause"] = machine.reset_cause()
    except Exception:
        reg.status["reset_cause"] = None

    try:
        reg.log.info("Supervisor boot; hostname=%s" % cfg.get("hostname", "mp-rtos"))
    except Exception:
        print("Supervisor boot")

    # Boot demo markers for host capture
    _boot_demo()

    # Services
    status = StatusMonitor(reg)
    i2c = I2CManager(reg)
    ble = BLEManager(reg)
    wdt = WatchdogService(reg)

    # Start non-async init
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

    # Schedule tasks
    asyncio.create_task(status.task())
    asyncio.create_task(i2c.task_scan())
    asyncio.create_task(ble.task())
    asyncio.create_task(wdt.task())

    # User app supervised
    asyncio.create_task(run_user_app(reg))

    # Keep alive
    while True:
        await asyncio.sleep_ms(2000)


def boot():
    asyncio.run(supervisor())


# If someone runs /rtos/main.py directly
if __name__ == "__main__":
    boot()