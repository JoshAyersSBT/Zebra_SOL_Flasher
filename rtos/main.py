# /rtos/main.py
import sys
import uasyncio as asyncio
import machine

# Ensure /rtos imports work even if launched differently
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

# If you have ControlPlane in your project, import it here.
# Adjust the import path if needed.
try:
    from control_plane import ControlPlane
except Exception:
    # If control_plane module isn't present yet, keep boot alive without it
    ControlPlane = None


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


async def _boot_demo_late(reg: Registry, delay_ms: int = 1200):
    """
    Print demo markers AFTER tasks have had a chance to run and populate readouts.
    This makes host-side capture reflect actual demo status (e.g. i2c readouts).
    """
    try:
        import syscli
        # yield to let scheduled tasks start
        await asyncio.sleep_ms(0)
        # give i2c scan + user app a moment to populate readouts
        await asyncio.sleep_ms(int(delay_ms))
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

    # Expose registry to REPL/tools immediately
    sysapi.set_registry(reg)

    # Basic boot metadata
    try:
        reg.status["reset_cause"] = machine.reset_cause()
    except Exception:
        reg.status["reset_cause"] = None

    # Control-plane device map (auto_bind + user_main populate this)
    reg.control_plane_devices = {}

    # Services
    status = StatusMonitor(reg)
    i2c = I2CManager(reg)
    ble = BLEManager(reg)
    wdt = WatchdogService(reg)

    # Optional control plane
    cp = None
    if ControlPlane is not None:
        try:
            cp = ControlPlane(reg, devices=reg.control_plane_devices, period_ms=20)
            # Expose CP submit to BLE handler
            reg.control_plane_submit_str = cp.submit_str
        except Exception as e:
            reg.control_plane_submit_str = None
            reg.log.warn("ControlPlane init failed: %r" % (e,))
    else:
        reg.control_plane_submit_str = None

    try:
        reg.log.info("Supervisor boot; hostname=%s" % cfg.get("hostname", "mp-rtos"))
    except Exception:
        print("Supervisor boot")

    # --------- Start non-async init FIRST ---------
    # I2C up + immediate scan + auto-bind
    try:
        i2c.start()
        try:
            reg.i2c = i2c.i2c
        except Exception:
            pass

        # one immediate scan so neofetch prints devices immediately
        try:
            if hasattr(i2c, "scan_all"):
                i2c.scan_all()
        except Exception:
            pass

        # automatic driver binding based on i2c.identification (if you added it)
        try:
            from drivers.auto_bind import bind as auto_bind
            auto_bind(reg)
        except Exception as e:
            reg.log.warn("auto_bind failed: %r" % (e,))

    except Exception as e:
        reg.log.warn("i2c.start failed: %r" % (e,))

    # BLE
    try:
        ble.start()
    except Exception as e:
        reg.log.warn("ble.start failed: %r" % (e,))

    # Watchdog
    try:
        wdt.start()
    except Exception as e:
        reg.log.warn("wdt.start failed: %r" % (e,))

    # --------- Schedule tasks ---------
    asyncio.create_task(status.task())
    asyncio.create_task(i2c.task_scan())
    asyncio.create_task(ble.task())
    asyncio.create_task(wdt.task())

    if cp is not None:
        asyncio.create_task(cp.task())

    # User app supervised (can further populate/override CP devices)
    asyncio.create_task(run_user_app(reg))

    # Demo markers for host capture (AFTER services + tasks + user app have time to run)
    await _boot_demo_late(reg, delay_ms=1200)

    # Keep alive
    while True:
        await asyncio.sleep_ms(2000)


def boot():
    # This MUST see supervisor in globals, otherwise you get your NameError.
    asyncio.run(supervisor())


# If someone runs /rtos/main.py directly
if __name__ == "__main__":
    boot()