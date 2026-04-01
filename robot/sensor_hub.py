from machine import I2C, Pin
import uasyncio as asyncio
from robot.debug_io import info, error

from robot import vl53l1x
try:
    from robot import vl53l0x
except ImportError:
    vl53l0x = None


class TCS3472:
    ADDR = 0x29
    CMD = 0x80
    ENABLE = 0x00
    ATIME = 0x01
    CONTROL = 0x0F
    ID = 0x12
    CDATA = 0x14

    def __init__(self, i2c, addr=0x29):
        self.i2c = i2c
        self.addr = addr

        chip_id = self._read8(self.ID)
        if chip_id not in (0x44, 0x4D):
            raise RuntimeError("unexpected TCS3472 ID: {}".format(hex(chip_id)))

        self._write8(self.ATIME, 0xEB)   # ~50 ms integration
        self._write8(self.CONTROL, 0x01) # 4x gain
        self._write8(self.ENABLE, 0x01)  # PON
        self._write8(self.ENABLE, 0x03)  # PON + AEN

    def _write8(self, reg, val):
        self.i2c.writeto_mem(self.addr, self.CMD | reg, bytes([val & 0xFF]))

    def _read8(self, reg):
        return self.i2c.readfrom_mem(self.addr, self.CMD | reg, 1)[0]

    def read(self):
        data = self.i2c.readfrom_mem(self.addr, self.CMD | self.CDATA, 8)
        c = data[0] | (data[1] << 8)
        r = data[2] | (data[3] << 8)
        g = data[4] | (data[5] << 8)
        b = data[6] | (data[7] << 8)
        return {"clear": c, "r": r, "g": g, "b": b}


class SensorHub:
    def __init__(
        self,
        i2c_id,
        sda_gpio,
        scl_gpio,
        freq,
        mux,
        port_modes,
        notify_fn,
        scan_period_ms=100,
    ):
        self.i2c = I2C(i2c_id, sda=Pin(sda_gpio), scl=Pin(scl_gpio), freq=freq)
        self.mux = mux
        self.port_modes = dict(port_modes)
        self.notify = notify_fn
        self.scan_period_ms = int(scan_period_ms)

        self._cache_state = {}
        self._cache_addrs = {}
        self._tof = {}
        self._color = {}
        self._last_value = {}
        self._retry_div = {}

    def snapshot(self):
        """
        Return a simple shared sensor snapshot for user programs.

        Main student-facing keys:
            tof_port_1 = {"value": 123, "meta": {...}}
            color_port_2 = {"value": {"r":...,"g":...,"b":...,"clear":...}, "meta": {...}}
            port_1_state = {"value": "VL53L0X", "meta": {"port": 1}}
        """
        out = {}

        for port in range(1, 7):
            state_name = self._cache_state.get(port)
            addrs = self._cache_addrs.get(port, ())
            out["port_{}_state".format(port)] = {
                "value": state_name if state_name is not None else "unknown",
                "meta": {
                    "port": port,
                    "addrs": [int(a) for a in addrs] if addrs else [],
                },
            }

        for (kind, port), value in self._last_value.items():
            if kind in ("VL53L1X", "VL53L0X"):
                if isinstance(value, (int, float)):
                    out["tof_port_{}".format(port)] = {
                        "value": int(value),
                        "meta": {
                            "kind": kind,
                            "port": port,
                            "unit": "mm",
                        },
                    }

            elif kind == "TCS3472":
                if isinstance(value, tuple) and len(value) == 4:
                    r, g, b, clear = value
                    out["color_port_{}".format(port)] = {
                        "value": {
                            "r": int(r),
                            "g": int(g),
                            "b": int(b),
                            "clear": int(clear),
                        },
                        "meta": {
                            "kind": kind,
                            "port": port,
                        },
                    }

        return out

    def _select(self, port):
        if self.mux is None:
            raise RuntimeError("SensorHub has no mux")
        self.mux.select(int(port))

    def _notify(self, line):
        try:
            self.notify(str(line))
        except Exception:
            pass

    def _scan(self, port):
        self._select(port)
        return tuple(self.i2c.scan())

    def _clear_port(self, port):
        self._tof.pop(port, None)
        self._color.pop(port, None)
        self._cache_state[port] = None
        self._last_value.pop(("TCS3472", port), None)
        self._last_value.pop(("VL53L1X", port), None)
        self._last_value.pop(("VL53L0X", port), None)

    def _publish_state(self, port, state, addrs):
        self._notify("SNS {} {}".format(port, state))
        if addrs:
            self._notify("SNS_I2C {} {}".format(
                port,
                ",".join(hex(a) for a in addrs)
            ))

    def _try_tcs3472(self, port):
        try:
            self._select(port)
            addrs = self.i2c.scan()
            if 0x29 not in addrs:
                return False

            chip_id = self.i2c.readfrom_mem(0x29, 0x92, 1)[0]
            if chip_id not in (0x44, 0x4D):
                self._notify("SNS_DBG {} tcs_id {}".format(port, hex(chip_id)))
                return False

            sensor = TCS3472(self.i2c)
            sensor.read()
            self._color[port] = sensor
            info("SensorHub: TCS3472 on port {}".format(port))
            return True
        except Exception as e:
            self._notify("SNS_DBG {} tcs_probe {}".format(port, e))
            return False

    def _read_tof_distance(self, sensor):
        if hasattr(sensor, "read"):
            return int(sensor.read())
        if hasattr(sensor, "distance"):
            d = sensor.distance
            return int(d() if callable(d) else d)
        if hasattr(sensor, "get_distance"):
            return int(sensor.get_distance())
        if hasattr(sensor, "ping"):
            return int(sensor.ping())
        raise RuntimeError("unsupported ToF API")

    def _try_vl53l1x(self, port):
        if vl53l1x is None:
            self._notify("SNS_ERR {} vl53l1x driver missing".format(port))
            return False

        try:
            self._select(port)
            addrs = self.i2c.scan()
            if 0x29 not in addrs:
                return False

            sensor = vl53l1x.VL53L1X(self.i2c)
            sensor.start()

            import time
            time.sleep_ms(80)

            sample = sensor.read_debug()

            self._notify(
                "SNS_DBG {} vl53 cand96={} cand9c={} candA0={} gpio={} raw={}".format(
                    port,
                    sample["cand_96"],
                    sample["cand_9C"],
                    sample["cand_A0"],
                    sample["gpio_status"],
                    sample["raw"],
                )
            )

            self._tof[port] = ("VL53L1X", sensor)
            self._last_value[("VL53L1X", port)] = sample["cand_96"]
            return True

        except Exception as e:
            self._notify("SNS_ERR {} vl53l1x_probe {}".format(port, e))
            return False

    def _try_vl53l0x(self, port):
        if vl53l0x is None:
            return False

        try:
            self._select(port)
            addrs = self.i2c.scan()
            if 0x29 not in addrs:
                return False

            sensor = vl53l0x.VL53L0X(self.i2c)
            sensor.init()
            sensor.start_continuous()

            import time
            time.sleep_ms(80)

            dist = sensor.read_range_continuous_mm()

            if not (20 <= dist <= 4000):
                return False

            self._tof[port] = ("VL53L0X", sensor)
            self._last_value[("VL53L0X", port)] = dist
            self._notify("SNS_DBG {} vl53l0x {}".format(port, dist))
            return True

        except Exception as e:
            self._notify("SNS_DBG {} vl53l0x_probe {}".format(port, e))
            return False

    def _identify(self, port, addrs):
        if not addrs:
            return "empty"

        self._notify("SNS_PROBE {} {}".format(
            port,
            ",".join(hex(a) for a in addrs)
        ))

        if self._try_vl53l0x(port):
            return "VL53L0X"

        if self._try_tcs3472(port):
            return "TCS3472"

        return "unidentified"

    def _poll_tcs3472(self, port):
        sensor = self._color.get(port)
        if sensor is None:
            return

        try:
            self._select(port)
            d = sensor.read()
            value = (d["r"], d["g"], d["b"], d["clear"])

            if self._last_value.get(("TCS3472", port)) != value:
                self._last_value[("TCS3472", port)] = value
                self._notify("SNS_COLOR {} {} {} {} {}".format(
                    port, d["r"], d["g"], d["b"], d["clear"]
                ))
        except Exception as e:
            error("TCS3472_POLL_{}".format(port), e)
            self._notify("SNS_ERR {} TCS3472 poll failed".format(port))
            self._clear_port(port)

    def _poll_tof(self, port, state_name):
        item = self._tof.get(port)
        if item is None:
            return

        kind, sensor = item
        try:
            self._select(port)

            if kind == "VL53L1X" and hasattr(sensor, "read_debug"):
                sample = sensor.read_debug()

                self._notify(
                    "SNS_TOF_DBG {} cand96={} cand9c={} candA0={} gpio={} raw={}".format(
                        port,
                        sample["cand_96"],
                        sample["cand_9C"],
                        sample["cand_A0"],
                        sample["gpio_status"],
                        sample["raw"],
                    )
                )

                dist = sample["cand_96"]

                if dist <= 0 or dist >= 4000 or dist == 65535:
                    self._notify("SNS_ERR {} {} invalid {}".format(port, kind, dist))
                    return
            else:
                dist = self._read_tof_distance(sensor)

            if self._last_value.get((kind, port)) != dist:
                self._last_value[(kind, port)] = dist
                self._notify("SNS_TOF {} {}".format(port, dist))
                self._notify("SNS {} {}".format(port, kind))

        except Exception as e:
            error("{}_POLL_{}".format(kind, port), e)
            self._notify("SNS_ERR {} {} poll failed".format(port, kind))
            self._clear_port(port)

    def _poll_port(self, port):
        addrs = self._scan(port)
        last_addrs = self._cache_addrs.get(port)

        if addrs != last_addrs:
            self._cache_addrs[port] = addrs
            self._clear_port(port)
            self._retry_div[port] = 0

        state_name = self._cache_state.get(port)
        if state_name is None:
            state_name = self._identify(port, addrs)
            self._cache_state[port] = state_name
            self._publish_state(port, state_name, addrs)

        if state_name == "empty":
            return

        if state_name == "unidentified":
            self._retry_div[port] = self._retry_div.get(port, 0) + 1
            if addrs:
                self._notify("SNS_I2C {} {}".format(
                    port,
                    ",".join(hex(a) for a in addrs)
                ))
            if self._retry_div[port] >= 10:
                self._retry_div[port] = 0
                self._clear_port(port)
            return

        if state_name == "TCS3472":
            self._poll_tcs3472(port)
            return

        if state_name in ("VL53L1X", "VL53L0X"):
            self._poll_tof(port, state_name)
            return

        self._notify("SNS_ERR {} bad_state {}".format(port, state_name))
        self._clear_port(port)

    async def task(self):
        info("SensorHub task started")
        while True:
            for port in range(1, 7):
                mode = self.port_modes.get(port, "none")
                if mode == "none":
                    continue

                try:
                    if mode == "auto":
                        self._poll_port(port)
                    else:
                        self._notify("SNS_ERR {} bad_mode {}".format(port, mode))
                except Exception as e:
                    error("SNS_PORT_{}".format(port), e)
                    self._notify("SNS_ERR {} exception".format(port))

            await asyncio.sleep_ms(self.scan_period_ms)