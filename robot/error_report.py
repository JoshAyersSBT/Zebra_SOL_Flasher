# robot/error_report.py
import io
import sys


def exc_to_string(exc):
    buf = io.StringIO()
    sys.print_exception(exc, buf)
    return buf.getvalue()


def split_lines(text, max_len=120):
    text = text.replace("\r", "")
    out = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        while len(line) > max_len:
            out.append(line[:max_len])
            line = line[max_len:]
        if line:
            out.append(line)
    return out


def packetize_exception(tag, exc, prefix="ERR", max_len=120):
    """
    Convert an exception into BLE/log friendly lines, e.g.
      ERR MPU_INIT
      ERR Traceback (most recent call last):
      ERR ...
    """
    out = ["{} {}".format(prefix, tag)]
    for line in split_lines(exc_to_string(exc), max_len=max_len):
        out.append("{} {}".format(prefix, line))
    return out


def packetize_text(tag, text, prefix="ERR", max_len=120):
    out = ["{} {}".format(prefix, tag)]
    for line in split_lines(str(text), max_len=max_len):
        out.append("{} {}".format(prefix, line))
    return out