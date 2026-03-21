# services/logger.py
import utime

class Logger:
    def __init__(self, ring_size=200):
        self._ring = []
        self._ring_size = ring_size

    def _push(self, level, msg):
        ts = utime.ticks_ms()
        line = (ts, level, msg)
        self._ring.append(line)
        if len(self._ring) > self._ring_size:
            self._ring.pop(0)

    def info(self, msg): self._push("I", msg)
    def warn(self, msg): self._push("W", msg)
    def error(self, msg): self._push("E", msg)

    def dump(self, n=50):
        return self._ring[-n:]