# syscli.py
# REPL usage:
#   >>> import syscli
#   >>> syscli.neofetch()

import sys
if "/rtos" not in sys.path:
    sys.path.append("/rtos")

import gc
import utime

try:
    import sysapi  # provides get_registry()
except Exception:
    sysapi = None


def _fmt_mac(mac):
    if mac is None:
        return "unknown"
    try:
        b = bytes(mac)
        return ":".join("%02x" % x for x in b)
    except Exception:
        return str(mac)


def _hex_addr(a):
    try:
        return "0x%02X" % int(a)
    except Exception:
        return str(a)


def _get_reg():
    if sysapi is None:
        return None
    try:
        return sysapi.get_registry()
    except Exception:
        return None


def _safe_type_name(x):
    try:
        return x.__class__.__name__
    except Exception:
        return "?"


def _get_screen(reg):
    # Preferred: new control plane attachment
    try:
        devs = getattr(reg, "control_plane_devices", None)
        if isinstance(devs, dict) and "screen" in devs:
            return devs.get("screen")
    except Exception:
        pass

    # Optional fallback: some users also store it in status or reg.screen
    try:
        sc = getattr(reg, "screen", None)
        if sc is not None:
            return sc
    except Exception:
        pass

    return None


def _screen_write_lines(screen, lines):
    """
    Best-effort render on OLED. Never raises.
    Expects screen.clear(), screen.writeLine(i, text)
    """
    try:
        screen.clear()
    except Exception:
        pass

    for i, txt in enumerate(lines):
        try:
            screen.writeLine(i, txt)
        except Exception:
            # don't break if line overflows / screen missing methods
            pass


def _build_screen_neofetch_lines(reg, st):
    """
    Build a compact OLED summary:
      line0: hostname
      line1: I2C bus + device count
      line2: I2C addrs (trimmed)
      line3: BLE enabled/connected
      line4: BLE name (trimmed)
      line5: optional CP sensor snippet
    """
    hostname = ""
    try:
        hostname = str(reg.cfg.get("hostname", "mp-rtos"))
    except Exception:
        hostname = "mp-rtos"

    i2c = st.get("i2c", {}) or {}
    devs = i2c.get("devices", []) or []
    bus = i2c.get("bus", None)

    ble = st.get("ble", {}) or {}
    ble_en = bool(ble.get("enabled", False))
    ble_conn = bool(ble.get("connected", False))
    ble_name = str(ble.get("device_name", "")) if ble.get("device_name", None) is not None else ""

    # Format addr list compactly (fit on one OLED line)
    addr_str = ""
    if devs:
        addr_str = ",".join(_hex_addr(a).replace("0x", "") for a in devs)  # e.g. "3C,29,68"
        # Trim aggressively for 128px width: keep start + ellipsis
        if len(addr_str) > 16:
            addr_str = addr_str[:14] + ".."
        addr_str = "I2C:" + addr_str
    else:
        addr_str = "I2C:(none)"

    l0 = hostname[:16]
    l1 = ("I2C b=%s n=%d" % (bus, len(devs)))[:16]
    l2 = addr_str[:16]
    l3 = ("BLE %s %s" % ("ON" if ble_en else "OFF", "CON" if ble_conn else "DISC"))[:16]
    l4 = ("name:" + ble_name)[:16] if ble_name else "name:(none)"

    # Optional: show one CP sensor value if present (nice for quick sanity)
    cp = st.get("cp", {}) or {}
    sens = cp.get("sensors", {}) or {}
    l5 = ""
    if "tof_mm" in sens:
        l5 = ("tof:%smm" % sens.get("tof_mm"))[:16]
    elif "yaw_deg" in sens:
        try:
            l5 = ("yaw:%.1f" % float(sens.get("yaw_deg")))[:16]
        except Exception:
            l5 = ("yaw:%s" % sens.get("yaw_deg"))[:16]
    else:
        l5 = ("cp:%s" % (cp.get("last_op", "") or ""))[:16] if cp else ""

    lines = [l0, l1, l2, l3, l4]
    if l5:
        lines.append(l5)
    return lines


def neofetch(*, to_screen: bool = True):
    """
    Prints a 'neofetch-like' system summary.

    If to_screen=True and a screen device is attached (reg.control_plane_devices["screen"]),
    it also writes a compact status summary to the OLED.
    """
    now = utime.ticks_ms()

    # RAM
    try:
        mf = gc.mem_free()
        ma = gc.mem_alloc()
    except Exception:
        mf, ma = 0, 0
    total = mf + ma

    print("")
    print("mp-rtos :: neofetch")
    print("-" * 44)
    print("RAM      : free=%d  alloc=%d  total=%d" % (mf, ma, total))

    reg = _get_reg()
    if reg is None:
        print("NOTE     : registry not available (supervisor not running or sysapi missing)")
        print("")
        return

    st = getattr(reg, "status", {}) or {}

    # Write to OLED if available
    if to_screen:
        screen = _get_screen(reg)
        if screen is not None:
            lines = _build_screen_neofetch_lines(reg, st)
            _screen_write_lines(screen, lines)

    # Uptime / loop health
    print("Uptime   : %d ms" % st.get("uptime_ms", 0))
    print("LoopLag  : %d ms   Load~%d%%" % (st.get("loop_lag_ms", 0), st.get("load_pct", 0)))

    # I2C
    i2c = st.get("i2c", {}) or {}
    devs = i2c.get("devices", []) or []
    print("")
    print("I2C      : bus=%s  devices=%d  errors=%d" % (i2c.get("bus", None), len(devs), i2c.get("errors", 0)))
    if devs:
        print("  addrs  :", ", ".join(_hex_addr(a) for a in devs))
    else:
        print("  addrs  : (none)")

    readouts = i2c.get("readouts", {}) or {}
    if readouts:
        print("  readouts:")
        for k in sorted(readouts.keys()):
            v = readouts[k]
            if isinstance(v, dict):
                name = v.get("name", "sensor")
                val = v.get("value", None)
                unit = v.get("unit", "")
                ts = v.get("ts_ms", None)
                age = ""
                if ts is not None:
                    try:
                        age = " (%d ms ago)" % utime.ticks_diff(now, int(ts))
                    except Exception:
                        age = ""
                if val is None:
                    err = v.get("err", "")
                    if err:
                        print("    %s: %s ERROR %s%s" % (k, name, err, age))
                    else:
                        print("    %s: %s%s" % (k, name, age))
                else:
                    print("    %s: %s = %s%s%s" % (k, name, val, unit, age))
            else:
                print("    %s: %r" % (k, v))
    else:
        print("  readouts: (none)")

    # CONTROL PLANE
    cp = st.get("cp", {}) or {}
    print("")
    print("CP       : rx=%d tx=%d last_op=%s" % (
        int(cp.get("rx", 0)),
        int(cp.get("tx", 0)),
        cp.get("last_op", None),
    ))
    if cp.get("last_err", None):
        print("  err    :", cp.get("last_err"))

    sensors = cp.get("sensors", {}) or {}
    if sensors:
        tof = sensors.get("tof_mm", None)
        yaw = sensors.get("yaw_deg", None)
        parts = []
        if tof is not None:
            parts.append("tof=%smm" % tof)
        if yaw is not None:
            try:
                parts.append("yaw=%.1fdeg" % float(yaw))
            except Exception:
                parts.append("yaw=%s" % yaw)
        if parts:
            print("  sens   :", "  ".join(parts))
        else:
            print("  sens   : keys=%s" % ",".join(sorted(sensors.keys())))

    # Attached CP devices
    try:
        devmap = getattr(reg, "control_plane_devices", None) or {}
    except Exception:
        devmap = {}
    if devmap:
        names = sorted(devmap.keys())
        print("  devs   : %d -> %s" % (len(names), ", ".join(names)))
        try:
            type_bits = []
            for n in names:
                type_bits.append("%s:%s" % (n, _safe_type_name(devmap.get(n))))
            print("  types  :", "  ".join(type_bits))
        except Exception:
            pass
    else:
        print("  devs   : (none attached)")

    # BLE
    ble = st.get("ble", {}) or {}
    print("")
    print("BLE      : enabled=%s connected=%s conn_count=%d" % (
        ble.get("enabled", False),
        ble.get("connected", False),
        ble.get("conn_count", 0),
    ))
    print("  name   :", ble.get("device_name", ""))
    print("  mac    :", _fmt_mac(ble.get("mac", None)))

    # Wi-Fi
    wifi = st.get("wifi", {}) or {}
    print("")
    print("WiFi     : enabled=%s active=%s connected=%s" % (
        wifi.get("enabled", False),
        wifi.get("active", False),
        wifi.get("connected", False),
    ))
    print("  host   :", wifi.get("hostname", ""))
    print("  ssid   :", wifi.get("ssid", ""))
    print("  ip     :", wifi.get("ip", ""))
    print("  rssi   :", wifi.get("rssi", None))

    print("")