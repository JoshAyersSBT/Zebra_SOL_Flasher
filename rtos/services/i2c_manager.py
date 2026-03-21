# services/i2c_manager.py
import uasyncio as asyncio
import utime

from machine import I2C, Pin
from drivers.i2c_probe import probe_addr


class I2CManager:
    """
    Brings up machine.I2C and scans periodically.

    Status keys:
      reg.status["i2c"] = {
        "enabled": bool,
        "bus": int|None,
        "sda": int|None,
        "scl": int|None,
        "freq": int,
        "devices": [int,...],              # merged scan results
        "mux_addr": int|None,
        "mux_ports_cfg": [int,...],        # configured ports to scan
        "mux_ports": { "0": [..], ... },   # per-port scan results
        "identified": { key: info, ... },  # key: "0x3C" or "0x29@p1"
        "scan_period_ms": int,
        "last_scan_ms": int,
        "errors": int,
        "last_error": str|None,
        "readouts": {},                    # reserved for future sensor readouts
      }
    """

    def __init__(self, reg):
        self.reg = reg
        self.cfg = (reg.cfg or {}).get("i2c", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", True))

        self.bus_id = self.cfg.get("bus", 0)
        self.sda = self.cfg.get("sda", None)
        self.scl = self.cfg.get("scl", None)
        self.freq = int(self.cfg.get("freq", 400000))
        self.scan_period_ms = int(self.cfg.get("scan_period_ms", 2000))

        # Optional mux support (TCA9548A)
        self.mux_addr = self.cfg.get("mux_addr", None)          # typically 0x70
        self.mux_ports_cfg = self.cfg.get("mux_ports", None)    # e.g. [0,1,2,3]

        self.i2c = None
        self._ci = reg.add_checkin("i2c", max_age_ms=3000)

        if "i2c" not in self.reg.status:
            self.reg.status["i2c"] = {}

        self.reg.status["i2c"].update({
            "enabled": self.enabled,
            "bus": None,
            "sda": self.sda,
            "scl": self.scl,
            "freq": self.freq,
            "devices": [],
            "mux_addr": self.mux_addr,
            "mux_ports_cfg": list(self.mux_ports_cfg) if isinstance(self.mux_ports_cfg, (list, tuple)) else None,
            "mux_ports": {},
            "identified": {},
            "scan_period_ms": self.scan_period_ms,
            "last_scan_ms": 0,
            "errors": 0,
            "last_error": None,
            "readouts": {},
        })

    def _hex(self, a: int) -> str:
        try:
            return "0x%02X" % int(a)
        except Exception:
            return str(a)

    def _set_error(self, err):
        st = self.reg.status["i2c"]
        st["errors"] = int(st.get("errors", 0)) + 1
        st["last_error"] = str(err)

    def start(self):
        st = self.reg.status["i2c"]

        if not self.enabled:
            st["bus"] = None
            self.reg.log.warn("I2C disabled in cfg")
            return

        if self.sda is None or self.scl is None:
            st["bus"] = None
            self._set_error("cfg missing sda/scl")
            self.reg.log.error("I2C cfg missing sda/scl")
            return

        try:
            self.i2c = I2C(
                int(self.bus_id),
                scl=Pin(int(self.scl)),
                sda=Pin(int(self.sda)),
                freq=int(self.freq),
            )
            # export to registry for user_main convenience
            self.reg.i2c = self.i2c

            st["bus"] = int(self.bus_id)
            st["last_error"] = None
            self.reg.log.info(
                "I2C started bus=%s sda=%s scl=%s freq=%s"
                % (self.bus_id, self.sda, self.scl, self.freq)
            )
        except Exception as e:
            self.i2c = None
            st["bus"] = None
            self._set_error(repr(e))
            self.reg.log.error("I2C start failed: %r" % (e,))

    def _mux_select(self, addr: int, port: int):
        # TCA9548A select: write 1<<port to addr
        self.i2c.writeto(int(addr), bytes([1 << int(port)]))

    def _scan_once(self):
        try:
            return self.i2c.scan()
        except Exception as e:
            self._set_error("scan: %r" % (e,))
            return []

    def _probe_safe(self, addr: int):
        try:
            return probe_addr(self.i2c, int(addr))
        except Exception as e:
            return {
                "addr": int(addr),
                "type": "unknown",
                "name": "probe_error",
                "confidence": 0,
                "evidence": {"err": repr(e)},
            }

    def scan_all(self):
        """
        Performs:
          - base scan
          - if mux_addr is configured or detected, scan ports and merge results
          - identify devices (base + per-port) and store in reg.status["i2c"]["identified"]
        """
        st = self.reg.status["i2c"]
        if self.i2c is None:
            return

        identified = {}

        # Base scan (with mux in "whatever state" — still useful for mux addr + always-on devices)
        base = self._scan_once()
        merged = set(base)
        mux_ports = {}

        # Identify base-bus devices
        for d in base:
            key = self._hex(d)
            identified[key] = self._probe_safe(d)

        # Decide mux addr: cfg wins, else autodetect 0x70 if present
        mux_addr = self.mux_addr
        if mux_addr is None and 0x70 in base:
            mux_addr = 0x70

        if mux_addr is not None:
            ports = self.mux_ports_cfg
            if not isinstance(ports, (list, tuple)) or not ports:
                ports = list(range(8))

            for p in ports:
                pkey = str(int(p))
                try:
                    # Select mux port, then scan AND probe while it's selected
                    self._mux_select(mux_addr, p)
                    devs = self._scan_once()
                    mux_ports[pkey] = devs

                    for d in devs:
                        merged.add(int(d))
                        k = "%s@p%s" % (self._hex(d), pkey)
                        identified[k] = self._probe_safe(d)

                except Exception as e:
                    mux_ports[pkey] = []
                    self._set_error("mux port %s: %r" % (pkey, e))

            # Best-effort: disable all after scan
            try:
                self.i2c.writeto(int(mux_addr), b"\x00")
            except Exception:
                pass

        st["devices"] = sorted(list(merged))
        st["mux_addr"] = mux_addr
        st["mux_ports"] = mux_ports
        st["identified"] = identified
        st["last_scan_ms"] = utime.ticks_ms()

    async def task_scan(self):
        await asyncio.sleep_ms(100)

        while True:
            self._ci.mark()
            if self.enabled and self.i2c is not None:
                self.scan_all()
            await asyncio.sleep_ms(self.scan_period_ms)