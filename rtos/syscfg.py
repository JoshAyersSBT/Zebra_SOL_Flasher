# syscfg.py
import ujson as json

CFG_PATH = "config.json"
CFG_VERSION = 1

DEFAULT_CFG = {
    "version": CFG_VERSION,
    "hostname": "mp-rtos",
    "ble": {
        "enabled": True,
        "device_name": "mp-rtos",
        "adv_interval_ms": 250,
    },
    # Wi-Fi later; keep fields now for BLE provisioning
    "wifi": {
        "enabled": False,
        "ssid": "",
        "password": "",
        "dhcp": True,
        "static": {"ip": "", "mask": "", "gw": "", "dns": ""},
    },
    "i2c": {
        "enabled": True,
        "bus": 0,
        "scl": 22,
        "sda": 21,
        "freq": 400000,
        "scan_period_ms": 2000,
    },
    "watchdog": {
        "enabled": True,
        "timeout_ms": 6000,
    },
    "status": {
        "period_ms": 500,
        "gc_collect_period_ms": 5000,
    },
}

def load_cfg():
    try:
        with open(CFG_PATH, "r") as f:
            cfg = json.loads(f.read())
    except OSError:
        cfg = {}

    # merge defaults
    merged = {}
    merged.update(DEFAULT_CFG)
    for k, v in cfg.items():
        merged[k] = v

    # migrate if needed
    if merged.get("version") != CFG_VERSION:
        merged["version"] = CFG_VERSION
        save_cfg(merged)

    return merged

def save_cfg(cfg):
    # write atomically-ish: write temp then rename
    tmp = CFG_PATH + ".tmp"
    s = json.dumps(cfg)
    with open(tmp, "w") as f:
        f.write(s)
    try:
        import os
        try:
            os.remove(CFG_PATH)
        except OSError:
            pass
        os.rename(tmp, CFG_PATH)
    except Exception:
        # fallback: leave tmp
        pass