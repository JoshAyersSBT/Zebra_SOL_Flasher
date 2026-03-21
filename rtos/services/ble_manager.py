# services/ble_manager.py
import uasyncio as asyncio
import utime
import ujson as json

try:
    import bluetooth
except ImportError:
    bluetooth = None

from syscfg import save_cfg

# Guard UUID creation if bluetooth is missing
def _uuid(s: str):
    if bluetooth is None:
        return None
    return bluetooth.UUID(s)

_UUID_SVC_SYS = _uuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f01")
_UUID_CH_HOST = _uuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f02")
_UUID_CH_STATUS = _uuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f03")
_UUID_CH_CMD = _uuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f04")

_FLAG_READ = 0x0002
_FLAG_WRITE = 0x0008
_FLAG_NOTIFY = 0x0010


class BLEManager:
    """
    BLE GATT service for:
      - hostname provisioning (RW)
      - status JSON (R/Notify)
      - commands (W)

    Publishes reg.status["ble"] consistently so neofetch is accurate even before start().
    """

    def __init__(self, reg):
        self.reg = reg
        self.cfg = (reg.cfg or {}).get("ble", {}) or {}

        self.enabled_cfg = bool(self.cfg.get("enabled", True))
        self.device_name = self.cfg.get("device_name", (reg.cfg or {}).get("hostname", "mp-rtos"))
        self.adv_interval_ms = int(self.cfg.get("adv_interval_ms", 250))

        self.ble = None
        self.conn_handle = None
        self._handles = {}
        self._adv_payload = None
        self._ci = reg.add_checkin("ble", max_age_ms=3000)

        self._notify_period_ms = 500

        # Ensure ble status always exists (so neofetch isn't lying)
        if "ble" not in self.reg.status:
            self.reg.status["ble"] = {}
        self.reg.status["ble"].update({
            # reflects CONFIG intent, not runtime state
            "enabled": self.enabled_cfg,
            "active": False,            # runtime: ble.active(True) succeeded
            "connected": False,
            "conn_count": int(self.reg.status["ble"].get("conn_count", 0) or 0),
            "device_name": self.device_name,
            "mac": None,
            "last_error": None,
        })

    def start(self):
        st = self.reg.status["ble"]

        # If cfg disables BLE, reflect it and exit
        if not self.enabled_cfg:
            st["enabled"] = False
            st["active"] = False
            self.reg.log.warn("BLE disabled (cfg)")
            return

        # bluetooth module missing on firmware
        if bluetooth is None:
            st["enabled"] = True
            st["active"] = False
            st["last_error"] = "bluetooth module not available"
            self.reg.log.error("bluetooth module not available")
            return

        try:
            self.ble = bluetooth.BLE()
            self.ble.active(True)
            st["active"] = True
            st["enabled"] = True
            st["last_error"] = None

            # Best-effort MAC
            try:
                mac = self.ble.config("mac")
                st["mac"] = mac
            except Exception:
                pass

            self.ble.irq(self._irq)

            svc = (
                _UUID_SVC_SYS,
                (
                    (_UUID_CH_HOST, _FLAG_READ | _FLAG_WRITE),
                    (_UUID_CH_STATUS, _FLAG_READ | _FLAG_NOTIFY),
                    (_UUID_CH_CMD, _FLAG_WRITE),
                ),
            )
            ((h_host, h_status, h_cmd),) = self.ble.gatts_register_services((svc,))
            self._handles = {"host": h_host, "status": h_status, "cmd": h_cmd}

            self._write_hostname((self.reg.cfg or {}).get("hostname", "mp-rtos"))
            self._write_status()

            self._adv_payload = self._build_adv_payload(name=self.device_name)
            self._advertise()

            self.reg.log.info("BLE started name=%s" % self.device_name)

        except Exception as e:
            # Keep enabled=True (cfg) but record runtime failure
            st["enabled"] = True
            st["active"] = False
            st["last_error"] = repr(e)
            self.reg.log.warn("BLE start failed: %r" % (e,))
            self.ble = None
            self.conn_handle = None
            self._handles = {}
            self._adv_payload = None

    def _build_adv_payload(self, name="mp-rtos"):
        name_bytes = (name or "mp-rtos").encode()
        payload = bytearray()
        payload += bytes((2, 0x01, 0x06))  # flags
        payload += bytes((len(name_bytes) + 1, 0x09)) + name_bytes  # complete name
        return payload

    def _advertise(self):
        if self.ble is None:
            return
        try:
            # Some ports take ms
            self.ble.gap_advertise(self.adv_interval_ms, adv_data=self._adv_payload)
        except TypeError:
            # Others take us
            self.ble.gap_advertise(self.adv_interval_ms * 1000, adv_data=self._adv_payload)
        except Exception as e:
            self.reg.status["ble"]["last_error"] = "advertise: %r" % (e,)

    def _irq(self, event, data):
        _IRQ_CENTRAL_CONNECT = 1
        _IRQ_CENTRAL_DISCONNECT = 2
        _IRQ_GATTS_WRITE = 3

        st = self.reg.status["ble"]

        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self.conn_handle = conn_handle
            st["connected"] = True
            st["conn_count"] = int(st.get("conn_count", 0)) + 1
            st["last_error"] = None
            self.reg.log.info("BLE connected")

        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            if self.conn_handle == conn_handle:
                self.conn_handle = None
            st["connected"] = False
            self.reg.log.info("BLE disconnected; advertising")
            self._advertise()

        elif event == _IRQ_GATTS_WRITE:
            _, value_handle = data
            if value_handle == self._handles.get("host"):
                raw = self.ble.gatts_read(value_handle)
                name = raw.decode(errors="ignore").strip()
                if name:
                    self._set_hostname(name)
            elif value_handle == self._handles.get("cmd"):
                raw = self.ble.gatts_read(value_handle)
                cmd = raw.decode(errors="ignore").strip()
                if cmd:
                    self._handle_cmd(cmd)

    def _write_hostname(self, name: str):
        if self.ble is None:
            return
        try:
            self.ble.gatts_write(self._handles["host"], (name or "").encode())
        except Exception:
            pass

    def _write_status(self):
        if self.ble is None:
            return
        try:
            s = json.dumps(self.reg.status)
            if len(s) > 350:
                slim = {
                    "uptime_ms": self.reg.status.get("uptime_ms", 0),
                    "mem_free": self.reg.status.get("mem_free", 0),
                    "mem_alloc": self.reg.status.get("mem_alloc", 0),
                    "load_pct": self.reg.status.get("load_pct", 0),
                    "loop_lag_ms": self.reg.status.get("loop_lag_ms", 0),
                    "i2c": self.reg.status.get("i2c", {}),
                    "ble": self.reg.status.get("ble", {}),
                    "cp": self.reg.status.get("cp", {}),
                }
                s = json.dumps(slim)

            self.ble.gatts_write(self._handles["status"], s.encode())
        except Exception:
            pass

    def _notify_status(self):
        if self.ble is None or self.conn_handle is None:
            return
        try:
            self._write_status()
            self.ble.gatts_notify(self.conn_handle, self._handles["status"])
        except Exception:
            pass

    def _set_hostname(self, name: str):
        name = (name or "").strip()
        if not name:
            return

        self.reg.cfg["hostname"] = name
        if "ble" in self.reg.cfg and isinstance(self.reg.cfg["ble"], dict):
            self.reg.cfg["ble"]["device_name"] = name
        save_cfg(self.reg.cfg)

        # update runtime + status
        self.device_name = name
        self.reg.status["ble"]["device_name"] = name

        self._write_hostname(name)

        # restart advertising with new name if possible
        try:
            self._adv_payload = self._build_adv_payload(name=self.device_name)
            self._advertise()
        except Exception:
            pass

        self.reg.log.info("Hostname set to %s" % name)

    def _handle_cmd(self, cmd: str):
        self.reg.log.info("BLE cmd: %s" % cmd)
        s = (cmd or "").strip()
        if not s:
            return

        # JSON control-plane forwarding
        if s.startswith("{"):
            fn = getattr(self.reg, "control_plane_submit_str", None)
            if fn is not None:
                try:
                    asyncio.create_task(fn(s))
                except Exception:
                    pass
            return

        c = s.lower()

        if c == "reboot":
            import machine
            machine.reset()

        elif c == "i2c_scan_now":
            # Hint to i2c service; your i2c manager should treat this as "scan asap"
            try:
                self.reg.status["i2c"]["last_scan_ms"] = utime.ticks_ms()
            except Exception:
                pass

        elif c.startswith("set_notify_ms "):
            try:
                ms = int(c.split()[-1])
                self._notify_period_ms = max(100, min(5000, ms))
            except Exception:
                pass

    async def task(self):
        # Always tick checkin even if disabled, so watchdog doesn't blame BLE task absence
        while True:
            self._ci.mark()
            if self.ble is not None and self.conn_handle is not None:
                self._notify_status()
            await asyncio.sleep_ms(self._notify_period_ms)