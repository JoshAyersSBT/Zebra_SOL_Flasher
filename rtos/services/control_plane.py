# services/control_plane.py
import uasyncio as asyncio
import utime
import ujson as json


class _AsyncMailbox:
    """
    Minimal async queue compatible with MicroPython uasyncio variants
    that don't provide asyncio.Queue.

    - put(msg): non-blocking, drops oldest if full
    - get_nowait(): raises IndexError if empty
    - wait(): await until something is available
    """
    def __init__(self, maxlen=32):
        self._buf = []
        self._maxlen = int(maxlen)
        self._ev = asyncio.Event()

    def put(self, msg) -> None:
        if len(self._buf) >= self._maxlen:
            # drop oldest
            self._buf.pop(0)
        self._buf.append(msg)
        self._ev.set()

    def get_nowait(self):
        if not self._buf:
            raise IndexError("empty")
        msg = self._buf.pop(0)
        if not self._buf:
            self._ev.clear()
        return msg

    async def wait(self):
        await self._ev.wait()


class ControlPlane:
    """
    Command router + device façade.

    BLE forwarding sends JSON strings like:
      {"op":"motors.run","steer":0,"speed":40}

    Publishes:
      reg.status["cp"] = {"rx","tx","last_op","last_ms","last_err","last_resp","sensors"}
    """

    def __init__(self, reg, *, devices: dict, period_ms: int = 20, q_max=32):
        self.reg = reg
        self.devices = devices
        self.period_ms = int(period_ms)

        self._mb = _AsyncMailbox(maxlen=q_max)
        self._ci = reg.add_checkin("cp", max_age_ms=2000)

        if "cp" not in reg.status:
            reg.status["cp"] = {}
        reg.status["cp"].update({
            "rx": 0,
            "tx": 0,
            "last_op": None,
            "last_ms": 0,
            "last_err": None,
            "last_resp": None,
            "sensors": {},
        })

    async def submit(self, msg):
        self._mb.put(msg)
        self.reg.status["cp"]["rx"] = int(self.reg.status["cp"].get("rx", 0)) + 1

    async def submit_str(self, cmd_str: str):
        s = (cmd_str or "").strip()
        if not s:
            return
        if s[:1] == "{":
            try:
                await self.submit(json.loads(s))
                return
            except Exception as e:
                self.reg.status["cp"]["last_err"] = "bad_json: %r" % (e,)
                return
        await self.submit(s)

    def _poll_sensors(self):
        out = {}

        if "tof" in self.devices:
            try:
                out["tof_mm"] = int(self.devices["tof"].readDistanceMean(5))
            except Exception as e:
                out["tof_err"] = repr(e)

        if "gyro" in self.devices:
            try:
                self.devices["gyro"].update()
                out["yaw_deg"] = float(self.devices["gyro"].getYaw())
            except Exception as e:
                out["gyro_err"] = repr(e)

        if "colour" in self.devices:
            try:
                out["colour"] = self.devices["colour"].readRGB()
            except Exception as e:
                out["colour_err"] = repr(e)

        if "husky" in self.devices:
            try:
                ok = bool(self.devices["husky"].update())
                out["husky_ok"] = ok
                out["husky_count"] = int(self.devices["husky"].getObjectCount())
            except Exception as e:
                out["husky_err"] = repr(e)

        self.reg.status["cp"]["sensors"] = out
        return {"ok": True, "sensors": out}

    async def _handle(self, msg):
        st = self.reg.status["cp"]
        st["last_ms"] = utime.ticks_ms()
        st["last_err"] = None

        try:
            if isinstance(msg, str):
                op = msg.strip()
                st["last_op"] = op
                if op.lower() == "cp_poll":
                    return self._poll_sensors()
                return {"ok": False, "err": "unknown string cmd"}

            op = str(msg.get("op", "")).strip()
            st["last_op"] = op

            # motors
            if op == "motors.run":
                m = self.devices["motors"]
                m.run(int(msg.get("steer", 0)), int(msg.get("speed", 0)))
                return {"ok": True}

            if op == "motors.stop":
                self.devices["motors"].stop_motors()
                return {"ok": True}

            if op == "motors.move_time":
                m = self.devices["motors"]
                m.move_time(int(msg.get("steer", 0)), int(msg.get("speed", 0)), float(msg.get("t", 0.0)))
                return {"ok": True}

            if op == "motors.move_rotations":
                m = self.devices["motors"]
                m.move_rotations(int(msg.get("steer", 0)), int(msg.get("speed", 0)), float(msg.get("rot", 0.0)))
                return {"ok": True}

            if op == "motors.move_degrees":
                m = self.devices["motors"]
                m.move_degrees(int(msg.get("steer", 0)), int(msg.get("speed", 0)), float(msg.get("deg", 0.0)))
                return {"ok": True}

            # servo
            if op == "servo.set":
                name = str(msg.get("name", "servo1"))
                self.devices[name].run_angles(int(msg.get("angle", 90)))
                return {"ok": True}

            # screen
            if op == "screen.clear":
                self.devices["screen"].clear()
                return {"ok": True}

            if op == "screen.line":
                self.devices["screen"].writeLine(int(msg.get("line", 0)), str(msg.get("text", "")))
                return {"ok": True}

            # sensors
            if op == "sensor.poll":
                return self._poll_sensors()

            return {"ok": False, "err": "unknown op"}

        except Exception as e:
            st["last_err"] = repr(e)
            return {"ok": False, "err": repr(e)}

    async def task(self):
        # event-driven + periodic tick
        while True:
            self._ci.mark()

            handled = False
            try:
                msg = self._mb.get_nowait()
                handled = True
            except Exception:
                msg = None

            if msg is not None:
                resp = await self._handle(msg)
                st = self.reg.status["cp"]
                st["tx"] = int(st.get("tx", 0)) + 1
                st["last_resp"] = resp

            if not handled:
                # sleep lightly to avoid busy loop
                await asyncio.sleep_ms(self.period_ms)