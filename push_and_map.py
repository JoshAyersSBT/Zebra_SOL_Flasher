#!/usr/bin/env python3
"""
push_and_map.py

Host-side tool (runs on your PC) to:
  - upload gpio_mapper.py and motor_gpio_mapper.py to the ESP32 (optional but included)
  - guide you through mapping servo and motor ports by driving GPIOs using mpremote exec
  - write a Markdown report with SERVO_PORT_MAP and MOTOR_PORT_MAP

Usage (Windows):
  python push_and_map.py --port COM7 --servo-ports 7 --motor-ports 4

Usage (Linux):
  python push_and_map.py --port /dev/ttyUSB0 --servo-ports 7 --motor-ports 4

Notes:
  - This script does NOT require interactive stdin on the ESP32.
  - You watch the hardware (servo twitch / motor burst) and answer prompts on the PC.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


# -----------------------------
# Default candidate GPIO lists
# -----------------------------
SAFE_OUT = [
    4, 5,
    13, 14,
    16, 17,
    18, 19,
    21, 22, 23,
    25, 26, 27,
    32, 33
]
SAFE_IN = [
    4, 5,
    12, 13, 14, 15,
    16, 17,
    18, 19,
    21, 22, 23,
    25, 26, 27,
    32, 33,
    34, 35, 36, 39
]
STRAP = [0, 2, 12, 15]


def run_mpremote(port: str, args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = ["mpremote", "connect", port] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def mp_exec(port: str, code: str, timeout: int = 30) -> None:
    # mpremote exec takes a string; pass as one arg
    cp = run_mpremote(port, ["exec", code], timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(f"mpremote exec failed:\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")


def mp_cp_to_board(port: str, src: str, dst: str) -> None:
    cp = run_mpremote(port, ["fs", "cp", src, dst], timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(f"mpremote fs cp failed:\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")


def mp_ls(port: str) -> str:
    cp = run_mpremote(port, ["fs", "ls"], timeout=30)
    if cp.returncode != 0:
        raise RuntimeError(f"mpremote fs ls failed:\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return cp.stdout


def uniq(seq: List[int]) -> List[int]:
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def prompt_int(prompt: str, valid: Optional[Tuple[int, int]] = None, allow_skip=True, allow_quit=True) -> Optional[int]:
    """
    Returns:
      - int if user entered a number
      - None if skipped
    Raises SystemExit if quit requested
    """
    while True:
        s = input(prompt).strip().lower()
        if allow_quit and s == "q":
            raise SystemExit(0)
        if allow_skip and (s == "" or s == "s"):
            return None
        try:
            v = int(s)
            if valid:
                lo, hi = valid
                if v < lo or v > hi:
                    print(f"  ! out of range ({lo}-{hi})")
                    continue
            return v
        except ValueError:
            print("  ! enter a number, 's' to skip, or 'q' to quit")


# -----------------------------
# GPIO drive primitives (board-side snippets)
# -----------------------------
SERVO_TWITCH_SNIPPET = r"""
from machine import Pin, PWM
import utime

gpio = {GPIO}
pwm = PWM(Pin(gpio, Pin.OUT), freq=50)

def set_angle(a, min_us=500, max_us=2500):
    a = int(a)
    if a < 0: a = 0
    if a > 180: a = 180
    pulse_us = min_us + (max_us - min_us) * a // 180
    period_us = 20000
    duty_u16 = int((pulse_us * 65535) // period_us)
    if hasattr(pwm, "duty_u16"):
        pwm.duty_u16(duty_u16)
    else:
        pwm.duty(int(duty_u16 * 1023 // 65535))

set_angle(60); utime.sleep_ms(300)
set_angle(120); utime.sleep_ms(300)
set_angle(90); utime.sleep_ms(300)

pwm.deinit()
"""

DIGITAL_TOGGLE_SNIPPET = r"""
from machine import Pin
import utime

gpio = {GPIO}
p = Pin(gpio, Pin.OUT)
for _ in range(10):
    p.value(1); utime.sleep_ms(150)
    p.value(0); utime.sleep_ms(150)
"""

MOTOR_BURST_SNIPPET = r"""
from machine import Pin, PWM
import utime

pwm_gpio = {PWM_GPIO}
dir_gpio = {DIR_GPIO}

d = Pin(dir_gpio, Pin.OUT)

def set_duty(pwm, duty_u16):
    duty_u16 = int(duty_u16)
    if duty_u16 < 0: duty_u16 = 0
    if duty_u16 > 65535: duty_u16 = 65535
    if hasattr(pwm, "duty_u16"):
        pwm.duty_u16(duty_u16)
    else:
        pwm.duty(int(duty_u16 * 1023 // 65535))

pwm = PWM(Pin(pwm_gpio, Pin.OUT), freq=20000)
set_duty(pwm, 0)

# forward burst
d.value(1)
set_duty(pwm, {DUTY})
utime.sleep_ms({MS})
set_duty(pwm, 0)
utime.sleep_ms(120)

# backward burst
d.value(0)
set_duty(pwm, {DUTY})
utime.sleep_ms({MS})
set_duty(pwm, 0)

pwm.deinit()
"""


def map_servos(port: str, servo_ports: int, candidates: List[int], mode: str) -> Dict[int, int]:
    """
    mode: "servo" or "toggle"
    Returns dict {port_number: gpio}
    """
    print("\n=== SERVO PORT MAPPING ===")
    print("Recommended: plug ONE servo at a time OR watch which servo/port moves.")
    print("For each GPIO test, enter which SERVO PORT moved (1..N).")
    print("Commands: 's' skip, 'q' quit.\n")

    mapping: Dict[int, int] = {}

    for gpio in candidates:
        if len(mapping) >= servo_ports:
            break
        if gpio in mapping.values():
            continue

        print(f"Testing GPIO {gpio} ...")

        try:
            if mode == "servo":
                mp_exec(port, SERVO_TWITCH_SNIPPET.format(GPIO=gpio), timeout=10)
            else:
                mp_exec(port, DIGITAL_TOGGLE_SNIPPET.format(GPIO=gpio), timeout=10)
        except Exception as e:
            print(f"  ! test failed on GPIO {gpio}: {e}")
            continue

        v = prompt_int(f"Which SERVO PORT moved/toggled? (1-{servo_ports} / s / q): ", valid=(1, servo_ports))
        if v is None:
            continue
        if v in mapping:
            print(f"  ! SERVO PORT {v} already mapped -> GPIO {mapping[v]}, skipping")
            continue

        mapping[v] = gpio
        print(f"  + mapped SERVO PORT {v} -> GPIO {gpio}")

    return mapping


def map_motors(port: str, motor_ports: int, out_candidates: List[int], duty: int, burst_ms: int) -> Dict[int, Dict[str, Optional[int]]]:
    """
    Returns dict {port_number: {"pwm": gpio, "dir": gpio, "enc": None}}
    """
    print("\n=== MOTOR PORT MAPPING (PWM + DIR) ===")
    print("Recommended: LIFT wheels / keep power low.")
    print("Best workflow: plug ONE motor into ONE physical port at a time, map it, then move to next.")
    print("For each PWM/DIR test pair, enter which MOTOR PORT moved (1..N).")
    print("Commands: 's' skip, 'q' quit.\n")

    mapping: Dict[int, Dict[str, Optional[int]]] = {}

    used: set[int] = set()

    # brute-force search pairs
    for pwm_gpio in out_candidates:
        if len(mapping) >= motor_ports:
            break
        if pwm_gpio in used:
            continue

        for dir_gpio in out_candidates:
            if len(mapping) >= motor_ports:
                break
            if dir_gpio == pwm_gpio:
                continue
            if dir_gpio in used:
                continue

            print(f"Testing PWM={pwm_gpio} DIR={dir_gpio} ...")
            try:
                mp_exec(
                    port,
                    MOTOR_BURST_SNIPPET.format(
                        PWM_GPIO=pwm_gpio,
                        DIR_GPIO=dir_gpio,
                        DUTY=duty,
                        MS=burst_ms,
                    ),
                    timeout=10,
                )
            except Exception as e:
                print(f"  ! test failed: {e}")
                continue

            v = prompt_int(f"Which MOTOR PORT moved? (1-{motor_ports} / s / q): ", valid=(1, motor_ports))
            if v is None:
                continue
            if v in mapping:
                print(f"  ! MOTOR PORT {v} already mapped -> {mapping[v]}, skipping")
                continue

            mapping[v] = {"pwm": pwm_gpio, "dir": dir_gpio, "enc": None}
            used.add(pwm_gpio)
            used.add(dir_gpio)
            print(f"  + mapped MOTOR PORT {v} -> PWM={pwm_gpio} DIR={dir_gpio}")

            # If doing one-motor-at-a-time, uncomment to stop after first success for a given port:
            # break

    # Fill missing ports with None entries for consistency
    for p in range(1, motor_ports + 1):
        mapping.setdefault(p, {"pwm": None, "dir": None, "enc": None})

    return mapping


def make_report(
    out_path: str,
    port: str,
    include_strap: bool,
    servo_mode: str,
    servo_candidates: List[int],
    motor_candidates: List[int],
    servo_map: Dict[int, int],
    motor_map: Dict[int, Dict[str, Optional[int]]],
) -> None:
    now = dt.datetime.now().astimezone()
    lines: List[str] = []
    lines.append("# ESP32 Port Mapping Report")
    lines.append("")
    lines.append(f"- Generated: {now.isoformat(timespec='seconds')}")
    lines.append(f"- mpremote port: `{port}`")
    lines.append(f"- Included strapping pins in candidates: `{include_strap}`")
    lines.append("")
    lines.append("## Candidate GPIOs")
    lines.append("")
    lines.append(f"- Servo test mode: `{servo_mode}`")
    lines.append(f"- Servo candidates: `{servo_candidates}`")
    lines.append(f"- Motor PWM/DIR candidates: `{motor_candidates}`")
    lines.append("")
    lines.append("## SERVO_PORT_MAP")
    lines.append("")
    lines.append("Paste into `SERVO_PORT_MAP`:")
    lines.append("")
    lines.append("```python")
    # print as a stable dict 1..N
    if servo_map:
        maxp = max(servo_map.keys())
    else:
        maxp = 0
    # user likely wants 1..7 even if partial
    for_range = range(1, max(maxp, 7) + 1)
    lines.append("SERVO_PORT_MAP = {")
    for p in for_range:
        v = servo_map.get(p, None)
        lines.append(f"    {p}: {v},")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("## MOTOR_PORT_MAP")
    lines.append("")
    lines.append("Paste into `MOTOR_PORT_MAP`:")
    lines.append("")
    lines.append("```python")
    lines.append("MOTOR_PORT_MAP = {")
    # assume 1..4 unless motor_map says otherwise
    maxm = max(motor_map.keys()) if motor_map else 4
    for p in range(1, max(maxm, 4) + 1):
        cfg = motor_map.get(p, {"pwm": None, "dir": None, "enc": None})
        lines.append(f"    {p}: {{'pwm': {cfg.get('pwm')}, 'dir': {cfg.get('dir')}, 'enc': {cfg.get('enc')}}},")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Servo “detection” is observational: a hobby servo does not provide a presence signal.")
    lines.append("- If a motor port never reacts, it may be mapped to GPIOs not in the candidate list; expand candidates.")
    lines.append("- If encoder mapping is needed next: we can add an encoder scan pass once PWM/DIR are known.")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="Serial port (e.g., COM7 or /dev/ttyUSB0)")
    ap.add_argument("--servo-ports", type=int, default=7, help="Number of servo logical ports (default 7)")
    ap.add_argument("--motor-ports", type=int, default=4, help="Number of motor logical ports (default 4)")
    ap.add_argument("--include-strap", action="store_true", help="Include ESP32 strapping pins in candidate lists")
    ap.add_argument("--servo-mode", choices=["servo", "toggle"], default="servo",
                    help="servo=servo twitch pulses, toggle=digital toggle for meter/LED (default servo)")
    ap.add_argument("--duty", type=int, default=12000, help="Motor burst duty_u16 (0..65535), default 12000")
    ap.add_argument("--burst-ms", type=int, default=220, help="Motor burst duration in ms, default 220")
    ap.add_argument("--push-files", action="store_true",
                    help="If set, push gpio_mapper.py and motor_gpio_mapper.py to the board (if present locally)")
    ap.add_argument("--report", default="mapping_report.md", help="Output report filename (Markdown)")
    args = ap.parse_args()

    # Candidate lists
    servo_candidates = SAFE_OUT[:]
    motor_candidates = SAFE_OUT[:]
    include_strap = bool(args.include_strap)
    if include_strap:
        servo_candidates = uniq(STRAP + servo_candidates)
        motor_candidates = uniq(STRAP + motor_candidates)
    else:
        servo_candidates = uniq(servo_candidates)
        motor_candidates = uniq(motor_candidates)

    # Optional push of the two standalone test files (if they exist)
    if args.push_files:
        for fn in ["gpio_mapper.py", "motor_gpio_mapper.py"]:
            if os.path.exists(fn):
                print(f"Uploading {fn} -> :/{fn}")
                mp_cp_to_board(args.port, fn, f":/{fn}")
            else:
                print(f"Skipping upload of {fn}: not found in current directory")

        print("\nBoard files:")
        print(mp_ls(args.port))

    print("\nStarting mapping session...")
    print("Tip: do motors one port at a time to avoid ambiguity.\n")

    servo_map = map_servos(
        port=args.port,
        servo_ports=args.servo_ports,
        candidates=servo_candidates,
        mode=args.servo_mode,
    )

    motor_map = map_motors(
        port=args.port,
        motor_ports=args.motor_ports,
        out_candidates=motor_candidates,
        duty=args.duty,
        burst_ms=args.burst_ms,
    )

    make_report(
        out_path=args.report,
        port=args.port,
        include_strap=include_strap,
        servo_mode=args.servo_mode,
        servo_candidates=servo_candidates,
        motor_candidates=motor_candidates,
        servo_map=servo_map,
        motor_map=motor_map,
    )

    print("\n✅ Done.")
    print(f"- Servo map captured: {servo_map}")
    print(f"- Motor map captured: {motor_map}")
    print(f"- Report written to: {os.path.abspath(args.report)}")


if __name__ == "__main__":
    main()