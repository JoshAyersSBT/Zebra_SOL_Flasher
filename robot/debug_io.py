# robot/debug_io.py
import io
import sys

_ble_sink = None
_boot_log = []
_BOOT_LOG_LIMIT = 120


def set_ble_sink(obj):
    """
    obj should provide:
      - notify_info(str)
      - notify_error(tag, exc_or_str)
      - optionally notify_line(str)
    """
    global _ble_sink
    _ble_sink = obj


def _push_boot(line):
    global _boot_log
    _boot_log.append(str(line))
    if len(_boot_log) > _BOOT_LOG_LIMIT:
        _boot_log = _boot_log[-_BOOT_LOG_LIMIT:]


def _serial(msg):
    try:
        print(msg)
    except Exception:
        pass


def _ble_info(msg):
    try:
        if _ble_sink is not None:
            _ble_sink.notify_info(str(msg))
    except Exception:
        pass


def _ble_line(msg):
    try:
        if _ble_sink is not None:
            if hasattr(_ble_sink, "notify_line"):
                _ble_sink.notify_line(str(msg))
            else:
                _ble_sink.notify_info(str(msg))
    except Exception:
        pass


def info(msg):
    text = str(msg)
    line = "[INFO] " + text
    _serial(line)
    _push_boot(line)
    _ble_info(text)


def warn(msg):
    text = str(msg)
    line = "[WARN] " + text
    _serial(line)
    _push_boot(line)
    _ble_info("WARN " + text)


def error(tag, exc):
    tag = str(tag)
    line = "[ERR] " + tag
    _serial(line)
    _push_boot(line)

    try:
        sys.print_exception(exc)
    except Exception:
        _serial(str(exc))

    try:
        if _ble_sink is not None:
            _ble_sink.notify_error(tag, exc)
    except Exception:
        pass


def error_text(tag, text):
    tag = str(tag)
    text = str(text)
    line = "[ERR] {}: {}".format(tag, text)
    _serial(line)
    _push_boot(line)
    _ble_info("ERR {} {}".format(tag, text))


def diag(msg):
    """
    Structured diagnostic line intended for both serial and BLE logs.
    Example:
        diag("MUX_CH 3 0x29")
        diag("SNS 2 TCS3472")
    """
    text = "DIAG " + str(msg)
    _serial(text)
    _push_boot(text)
    _ble_line(text)


def state(name, value):
    """
    State transition / heartbeat style reporting.
    Example:
        state("BOOT", "motors initialized")
        state("SNS1", "empty")
    """
    text = "STATE {} {}".format(name, value)
    _serial(text)
    _push_boot(text)
    _ble_line(text)


def dump_boot_log():
    return list(_boot_log)


def replay_boot_log():
    """
    Replay retained boot/diag log lines over BLE after a central connects.
    Safe to call multiple times.
    """
    for line in _boot_log:
        _ble_line(line)


def exc_to_string(exc):
    buf = io.StringIO()
    sys.print_exception(exc, buf)
    return buf.getvalue()