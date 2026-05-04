# robot/button.py
# Debounced GPIO button support for the ZebraBot runtime.
#
# Intended runtime wiring:
#   from robot.button import ButtonManager
#   manager = ButtonManager(api, BUTTON_MAP, ...)
#   api.register_handle("button_manager", manager)
#   api.register_task("buttons", asyncio.create_task(manager.task()))
#
# Student-facing usage after main.py wiring:
#   zbot.button(1).pressed()
#   zbot.button(1).was_pressed()

import time
import uasyncio as asyncio
from machine import Pin


_DEFAULT_BUTTON_MAP = {
    1: {"name": "B1", "gpio": 15, "pull": "down", "active_low": False},
    2: {"name": "B2", "gpio": 12, "pull": "down", "active_low": False},
}


def _ticks_ms():
    return time.ticks_ms()


def _ticks_diff(a, b):
    return time.ticks_diff(a, b)


def _pin_pull(value, default_pull="down"):
    pull = value
    if pull is None:
        pull = default_pull

    pull = str(pull).lower()
    if pull in ("up", "pull_up", "pullup"):
        return Pin.PULL_UP
    if pull in ("down", "pull_down", "pulldown"):
        return Pin.PULL_DOWN
    if pull in ("none", "off", "float", "floating"):
        return None
    return Pin.PULL_DOWN if str(default_pull).lower().startswith("down") else Pin.PULL_UP


class NullButton:
    def __init__(self, button_id=0):
        self.button_id = int(button_id)

    def read(self):
        return False

    def value(self):
        return 0

    def pressed(self):
        return False

    def released(self):
        return True

    def was_pressed(self):
        return False

    def was_released(self):
        return False

    def presses(self, reset=False):
        return 0

    def releases(self, reset=False):
        return 0

    def snapshot(self):
        return {
            "id": self.button_id,
            "available": False,
            "pressed": False,
            "presses": 0,
            "releases": 0,
        }


class DebouncedButton:
    def __init__(
        self,
        button_id,
        gpio,
        name=None,
        pull="down",
        active_low=False,
        debounce_ms=35,
    ):
        self.button_id = int(button_id)
        self.gpio = int(gpio)
        self.name = name or "B{}".format(self.button_id)
        self.pull = pull
        self.active_low = bool(active_low)
        self.debounce_ms = int(debounce_ms)

        pin_pull = _pin_pull(self.pull)
        if pin_pull is None:
            self.pin = Pin(self.gpio, Pin.IN)
        else:
            self.pin = Pin(self.gpio, Pin.IN, pin_pull)

        now = _ticks_ms()
        initial = self._raw_pressed()

        self._candidate_pressed = initial
        self._candidate_ms = now
        self._pressed = initial
        self._last_change_ms = now

        self._press_count = 0
        self._release_count = 0
        self._press_latch = 0
        self._release_latch = 0

    def _raw_pressed(self):
        raw = self.pin.value()
        if self.active_low:
            return raw == 0
        return raw == 1

    def update(self, now=None):
        if now is None:
            now = _ticks_ms()

        raw_pressed = self._raw_pressed()

        if raw_pressed != self._candidate_pressed:
            self._candidate_pressed = raw_pressed
            self._candidate_ms = now
            return False

        if raw_pressed != self._pressed:
            if _ticks_diff(now, self._candidate_ms) >= self.debounce_ms:
                self._pressed = raw_pressed
                self._last_change_ms = now

                if self._pressed:
                    self._press_count += 1
                    self._press_latch += 1
                else:
                    self._release_count += 1
                    self._release_latch += 1

                return True

        return False

    def read(self):
        return self._pressed

    def value(self):
        return 1 if self._pressed else 0

    def pressed(self):
        return self._pressed

    def released(self):
        return not self._pressed

    def was_pressed(self):
        if self._press_latch <= 0:
            return False
        self._press_latch -= 1
        return True

    def was_released(self):
        if self._release_latch <= 0:
            return False
        self._release_latch -= 1
        return True

    def presses(self, reset=False):
        count = self._press_count
        if reset:
            self._press_count = 0
        return count

    def releases(self, reset=False):
        count = self._release_count
        if reset:
            self._release_count = 0
        return count

    def snapshot(self):
        return {
            "id": self.button_id,
            "name": self.name,
            "gpio": self.gpio,
            "available": True,
            "pressed": self._pressed,
            "value": 1 if self._pressed else 0,
            "presses": self._press_count,
            "releases": self._release_count,
            "last_change_ms": self._last_change_ms,
            "debounce_ms": self.debounce_ms,
            "active_low": self.active_low,
            "pull": self.pull,
        }


class ButtonManager:
    def __init__(
        self,
        api=None,
        button_map=None,
        debounce_ms=35,
        scan_period_ms=10,
        default_pull="down",
        default_active_low=False,
    ):
        self.api = api
        self.button_map = button_map or _DEFAULT_BUTTON_MAP
        self.debounce_ms = int(debounce_ms)
        self.scan_period_ms = int(scan_period_ms)
        self.default_pull = default_pull
        self.default_active_low = bool(default_active_low)
        self.buttons = {}
        self.errors = 0
        self.started = False

    def _iter_config(self):
        for button_id in sorted(self.button_map.keys()):
            cfg = self.button_map[button_id]
            if isinstance(cfg, int):
                cfg = {"gpio": cfg}
            yield int(button_id), cfg

    def start(self):
        for button_id, cfg in self._iter_config():
            gpio = cfg.get("gpio", cfg.get("pin", None))
            if gpio is None:
                continue

            button = DebouncedButton(
                button_id=button_id,
                gpio=int(gpio),
                name=cfg.get("name", "B{}".format(button_id)),
                pull=cfg.get("pull", self.default_pull),
                active_low=cfg.get("active_low", self.default_active_low),
                debounce_ms=int(cfg.get("debounce_ms", self.debounce_ms)),
            )
            self.buttons[button_id] = button

        self.started = True
        self._publish_status()
        return self

    def button(self, button_id=1):
        return self.buttons.get(int(button_id), NullButton(button_id))

    def __call__(self, button_id=1):
        return self.button(button_id)

    def update(self):
        now = _ticks_ms()
        changed = False
        for button in self.buttons.values():
            try:
                if button.update(now):
                    changed = True
            except Exception:
                self.errors += 1
        self._publish_status()
        return changed

    def snapshot(self):
        data = {}
        for button_id, button in self.buttons.items():
            data[button_id] = button.snapshot()
        return data

    def _publish_status(self):
        if self.api is None:
            return

        try:
            self.api.status["buttons"] = self.snapshot()
            self.api.status["services"]["buttons"] = {
                "ready": self.started,
                "count": len(self.buttons),
                "errors": self.errors,
                "scan_period_ms": self.scan_period_ms,
                "ts_ms": _ticks_ms(),
            }
        except Exception:
            pass

    async def task(self):
        while True:
            self.update()
            await asyncio.sleep_ms(self.scan_period_ms)
