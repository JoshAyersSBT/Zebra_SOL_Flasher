import os
import sys
import subprocess
import argparse
import asyncio
import base64
import html
import time
import platform
import glob
import json
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Iterable

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSignalBlocker, QObject, pyqtSlot, QUrl, QProcess
import tempfile
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QLineEdit, QTextEdit, QGroupBox, QFormLayout, QMessageBox,
    QSpinBox, QTabWidget, QListWidget, QListWidgetItem, QSlider, QGridLayout,
    QComboBox, QCheckBox, QSplitter, QInputDialog, QDialog, QProgressBar,
    QTreeWidget, QTreeWidgetItem
)

from PyQt6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothDeviceInfo,
    QBluetoothLocalDevice,
    QLowEnergyController,
    QLowEnergyService,
    QBluetoothUuid,
)

import os
import sys


POST_JOB_SERIAL_CAPTURE_SECONDS = 90.0
DEFAULT_SERIAL_MONITOR_BAUD = 115200

LOG_LEVEL_STYLES = {
    "error": "color:#ff7b72; font-weight:700;",
    "warning": "color:#f2cc60; font-weight:700;",
    "success": "color:#7ee787; font-weight:700;",
    "command": "color:#79c0ff;",
    "serial": "color:#c9d1d9;",
    "ble": "color:#d2a8ff;",
    "info": "color:#dce7f3;",
}


def classify_log_line(line: str) -> str:
    """Return a display category for a teleop/serial log line."""
    text = str(line or "")
    low = text.lower()
    stripped = text.strip()

    error_terms = (
        "❌", "traceback", "error", " err ", "[err]", " err_", "exception",
        "failed", "failure", "critical", "oserror", "typeerror", "attributeerror",
        "syntaxerror", "memoryerror", "runtimeerror", "controller error", "service error",
        "read failed", "scan error", "operation failed",
    )
    warning_terms = (
        "warn", "warning", "timed out", "timeout", "skipped", "unavailable",
        "not found", "exists or not supported", "continuing", "canceled",
    )
    success_terms = (
        "✅", "complete", "completed", "success", "connected", "found ",
        "selected", "uploaded", "ready", "done", "started", "created",
    )

    if any(term in low for term in error_terms) or stripped.startswith(("ERR", "BOOT DEMO ERROR")):
        return "error"
    if any(term in low for term in warning_terms) or stripped.startswith(("WARN", "W ", "[WARN]")):
        return "warning"
    if stripped.startswith((">>", "->", "TX:", "TX->")):
        return "command"
    if "ble" in low or stripped.startswith(("RX<-", "[ROBOT", "NUS")):
        return "ble"
    if stripped.startswith("[SERIAL]"):
        return "serial"
    if any(term in low for term in success_terms):
        return "success"
    return "info"


def rich_log_html(line: str) -> str:
    """Convert a single plain-text log line into safe, colorized rich text."""
    category = classify_log_line(line)
    style = LOG_LEVEL_STYLES.get(category, LOG_LEVEL_STYLES["info"])
    escaped = html.escape(str(line)).replace(" ", "&nbsp;")
    return f"<div style='white-space:pre-wrap; font-family:Consolas, monospace; {style}'>{escaped}</div>"


def append_rich_log(widget: QTextEdit, text: str) -> None:
    """Append text to a QTextEdit while preserving spacing and coloring by category."""
    if widget is None:
        return
    raw = str(text or "")
    if raw == "":
        widget.append("")
        return
    for line in raw.splitlines() or [raw]:
        widget.append(rich_log_html(line))
    try:
        bar = widget.verticalScrollBar()
        bar.setValue(bar.maximum())
    except Exception:
        pass


def app_root_dir() -> Path:
    return Path(__file__).resolve().parent


def ensure_robot_package_importable() -> None:
    """
    Make desktop teleop imports behave like the runtime layout where a
    robot/ directory lives beside main.py.

    This allows imports such as:
        from robot.oled_status import OledStatus
    even when the app is launched from an unexpected working directory.
    """
    base = app_root_dir()
    base_str = str(base)
    if base_str not in sys.path:
        sys.path.insert(0, base_str)

    robot_dir = base / "robot"
    if robot_dir.is_dir():
        init_py = robot_dir / "__init__.py"
        if not init_py.exists():
            try:
                init_py.write_text(
                    '# Auto-created so desktop tools can import the robot package.\n',
                    encoding='utf-8',
                )
            except Exception:
                pass


ensure_robot_package_importable()

def user_data_root_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ZebraTeleopFlasher"
    return Path.home() / ".zebra_teleop"


def projects_root_dir() -> Path:
    root = user_data_root_dir() / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_editor_project_root(path: str | Path) -> bool:
    try:
        p = Path(path).resolve()
        p.relative_to(projects_root_dir().resolve())
        return p.is_dir()
    except Exception:
        return False


def _copy_python_tree(src_root: Path, dst_root: Path, *, rename_main_to: str | None = None):
    """
    Copy only deploy-relevant Python files into a staged tree.

    Rules:
    - Only .py and .mpy files are copied.
    - Hidden folders, cache folders, venvs, build outputs, and node_modules are skipped.
    - If rename_main_to is provided, src_root/main.py is copied to that filename in dst_root.
    """
    skip_dir_names = {
        "__pycache__", ".git", ".idea", ".vscode",
        ".mypy_cache", ".pytest_cache", "node_modules",
        ".venv", "venv", "dist", "build"
    }
    allowed_suffixes = {".py", ".mpy"}

    src_root = Path(src_root).resolve()
    dst_root = Path(dst_root).resolve()

    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue

        rel = path.relative_to(src_root)
        rel_parts = rel.parts
        if any(part in skip_dir_names for part in rel_parts[:-1]):
            continue
        if any(part.startswith('.') for part in rel_parts[:-1]):
            continue
        if path.name.startswith('.'):
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        if should_skip_upload_file(path, rel.as_posix()):
            continue

        dest_rel = rel
        if rename_main_to and rel.as_posix() == 'main.py':
            dest_rel = Path(rename_main_to)

        dest = dst_root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def build_staged_runtime_project(source_root: str | Path):
    """
    Build a clean deploy tree that contains only deploy-relevant Python files.

    Editor project input:
        staged/
            main.py       <- runtime main.py from teleop app dir
            user_main.py  <- selected project main.py or user_main.py
            robot/        <- runtime robot package (.py/.mpy only)
            ...extra project python files...

    External runtime project input:
        staged/
            main.py       <- source project main.py
            robot/        <- source project robot/ (.py/.mpy only)
            ...extra python files from source root except teleop launcher files...

    Returns:
        (TemporaryDirectory | None, Path)
    """
    src = Path(source_root).resolve()
    if not src.exists() or not src.is_dir():
        raise RuntimeError(f"Project root not found: {src}")

    tmp = tempfile.TemporaryDirectory(prefix="zbot_stage_")
    stage = Path(tmp.name)

    if is_editor_project_root(src):
        runtime_root = app_root_dir()
        runtime_main = runtime_root / 'main.py'
        runtime_robot = runtime_root / 'robot'

        if not runtime_main.is_file():
            raise RuntimeError(f"Runtime main.py not found: {runtime_main}")
        if not runtime_robot.is_dir():
            raise RuntimeError(f"Runtime robot/ folder not found: {runtime_robot}")

        student_entry = src / 'user_main.py'
        if not student_entry.is_file():
            student_entry = src / 'main.py'
        if not student_entry.is_file():
            raise RuntimeError(f"Student project must contain main.py or user_main.py: {src}")

        shutil.copy2(runtime_main, stage / 'main.py')
        _copy_python_tree(runtime_robot, stage / 'robot')
        staged_robot_init = stage / 'robot' / '__init__.py'
        if not staged_robot_init.exists():
            staged_robot_init.write_text(
                '# Auto-created so desktop tools and staged deploys can import robot.*',
                encoding='utf-8',
            )
        shutil.copy2(student_entry, stage / 'user_main.py')

        # Copy additional student Python modules, but do not duplicate the entrypoint.
        for path in sorted(src.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            if any(part in {"__pycache__", ".git", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"} for part in rel.parts[:-1]):
                continue
            if any(part.startswith('.') for part in rel.parts[:-1]):
                continue
            if path.name.startswith('.'):
                continue
            if path.suffix.lower() not in {'.py', '.mpy'}:
                continue
            if rel.as_posix() in {'main.py', 'user_main.py'}:
                continue
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

        return tmp, stage

    # External runtime project: keep only runtime files that belong on-device.
    # This intentionally excludes the desktop app's local projects/ library and
    # unrelated helper scripts in the app root.
    main_src = src / 'main.py'
    robot_src = src / 'robot'
    if not main_src.is_file():
        raise RuntimeError(f"main.py not found: {main_src}")
    if not robot_src.is_dir():
        raise RuntimeError(f"robot folder not found: {robot_src}")

    shutil.copy2(main_src, stage / 'main.py')
    _copy_python_tree(robot_src, stage / 'robot')

    user_main_src = src / 'user_main.py'
    if user_main_src.is_file():
        shutil.copy2(user_main_src, stage / 'user_main.py')
    elif src == app_root_dir().resolve() and (src / 'projects').is_dir():
        raise RuntimeError(
            "Deploy source is the teleop application root. Select a specific student project folder from projects/ "
            "or use the Project Editor's Configure + Deploy flow so the selected project's main.py can be staged as user_main.py."
        )

    # Copy only additional package/module folders that are explicitly part of the
    # runtime project. Skip the editor's projects/ library entirely.
    skip_top_level = {
        'robot', 'projects', '__pycache__', '.git', '.idea', '.vscode',
        '.mypy_cache', '.pytest_cache', 'node_modules', '.venv', 'venv',
        'dist', 'build'
    }
    allowed_suffixes = {'.py', '.mpy'}

    for path in sorted(src.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        rel_posix = rel.as_posix()
        parts = rel.parts

        if rel_posix in {'main.py', 'user_main.py'} or rel_posix.startswith('robot/'):
            continue
        if parts and parts[0] in skip_top_level:
            continue
        if any(part in skip_top_level for part in parts[:-1]):
            continue
        if any(part.startswith('.') for part in parts[:-1]):
            continue
        if path.name.startswith('.'):
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        if should_skip_upload_file(path, rel_posix):
            continue

        # Only keep Python files that live inside additional package folders.
        # Ignore stray top-level helper scripts such as drive_straight.py.
        if len(parts) < 2:
            continue

        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    return tmp, stage

def is_editor_project_root(path: str | Path) -> bool:
    """
    Determine whether a folder is one of the desktop editor student projects.

    Only folders inside the managed projects/ root count as editor projects.
    This prevents the teleop application root itself from being mistaken for a
    student project just because it also contains a main.py file.
    """
    try:
        p = Path(path).resolve()
        projects_root = projects_root_dir().resolve()
    except Exception:
        return False

    if not p.is_dir():
        return False

    try:
        p.relative_to(projects_root)
    except Exception:
        return False

    return (p / 'main.py').is_file() or (p / 'user_main.py').is_file()
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebChannel import QWebChannel
except Exception:
    QWebEngineView = None
    QWebChannel = None

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.formatters import HtmlFormatter
except Exception:
    highlight = None
    get_lexer_by_name = None
    TextLexer = None
    HtmlFormatter = None


# ============================================================
# Serial port discovery helpers
# ============================================================
ESP32_USB_KEYWORDS = (
    "esp32",
    "cp210",
    "ch340",
    "ch910",
    "usb serial",
    "wchusbserial",
    "silicon labs",
    "uart",
    "jtag",
)

ESP32_USB_HWIDS = {
    (0x10C4, 0xEA60),  # CP210x
    (0x1A86, 0x7523),  # CH340
    (0x1A86, 0x55D4),  # CH9102
    (0x303A, 0x1001),  # ESP32-Sx USB JTAG/serial
    (0x303A, 0x4001),
    (0x303A, 0x8001),
    (0x0403, 0x6001),  # FTDI common fallback
}


def _port_score(info: dict) -> int:
    score = 0
    vid = info.get("vid")
    pid = info.get("pid")
    desc = (info.get("description") or "").lower()
    hwid = (info.get("hwid") or "").lower()
    device = (info.get("device") or "").lower()
    manu = (info.get("manufacturer") or "").lower()

    if (vid, pid) in ESP32_USB_HWIDS:
        score += 100
    text = " ".join([desc, hwid, device, manu])
    for kw in ESP32_USB_KEYWORDS:
        if kw in text:
            score += 15
    if sys.platform.startswith("win") and device.startswith("com"):
        score += 5
    if sys.platform == "darwin" and "/dev/cu." in device:
        score += 10
    if sys.platform != "win32" and device.startswith("/dev/"):
        score += 5
    return score


def list_serial_candidates() -> list[dict]:
    results: list[dict] = []
    seen = set()

    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            info = {
                "device": p.device,
                "description": p.description or "",
                "manufacturer": getattr(p, "manufacturer", "") or "",
                "vid": getattr(p, "vid", None),
                "pid": getattr(p, "pid", None),
                "hwid": p.hwid or "",
            }
            info["score"] = _port_score(info)
            results.append(info)
            seen.add(info["device"])
    except Exception:
        pass

    if sys.platform == "darwin":
        patterns = [
            "/dev/cu.usb*",
            "/dev/tty.usb*",
            "/dev/cu.SLAB_USBtoUART*",
            "/dev/cu.wchusbserial*",
        ]
    elif sys.platform.startswith("linux"):
        patterns = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/serial/by-id/*",
        ]
    else:
        patterns = []

    for pat in patterns:
        for dev in glob.glob(pat):
            if dev in seen:
                continue
            info = {
                "device": dev,
                "description": "serial device",
                "manufacturer": "",
                "vid": None,
                "pid": None,
                "hwid": "",
            }
            info["score"] = _port_score(info)
            results.append(info)
            seen.add(dev)

    results.sort(key=lambda x: (-int(x.get("score", 0)), x.get("device") or ""))
    return results


def auto_detect_esp32_port(log_cb: Callable[[str], None] | None = None) -> str:
    log = log_cb or (lambda _msg: None)
    candidates = list_serial_candidates()
    if not candidates:
        raise RuntimeError(
            "No serial ports found. Connect the ESP32 and make sure the USB serial driver is installed."
        )

    log(f"Detected {len(candidates)} serial port(s) on {platform.system()}")
    for c in candidates[:10]:
        log(
            "  - {device} | {description} | VID:PID {vid}:{pid} | score={score}".format(
                device=c.get("device", "?"),
                description=c.get("description", ""),
                vid=(f"{c['vid']:04X}" if isinstance(c.get("vid"), int) else "----"),
                pid=(f"{c['pid']:04X}" if isinstance(c.get("pid"), int) else "----"),
                score=c.get("score", 0),
            )
        )

    ranked = [c for c in candidates if int(c.get("score", 0)) > 0]
    if not ranked:
        ranked = candidates

    chosen = ranked[0].get("device", "")
    if not chosen:
        raise RuntimeError("Serial port discovery failed.")

    log(f"Selected serial port: {chosen}")
    return chosen

# ============================================================
# BLE code upload helpers
# ============================================================
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify from robot
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write to robot
DEFAULT_EXTS = {".py", ".mpy", ".json", ".txt", ".cfg", ".ini"}


class GuiBleCodeUploader:
    def __init__(
        self,
        address: str,
        chunk_size: int = 45,
        timeout: float = 8.0,
        log_cb: Callable[[str], None] | None = None,
    ):
        self.address = address
        self.chunk_size = int(chunk_size)
        self.timeout = float(timeout)
        self.log_cb = log_cb or (lambda _msg: None)
        self.client = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    def _log(self, msg: str):
        self.log_cb(str(msg))

    async def __aenter__(self):
        try:
            from bleak import BleakClient
        except Exception as e:
            raise RuntimeError(
                "BLE upload requires the 'bleak' package. Install it with: pip install bleak"
            ) from e

        self.client = BleakClient(self.address)
        await self.client.connect()
        await self.client.start_notify(NUS_TX_UUID, self._on_notify)
        self._log(f"Connected to {self.address}")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self.client and self.client.is_connected:
                await self.client.stop_notify(NUS_TX_UUID)
        except Exception:
            pass
        try:
            if self.client:
                await self.client.disconnect()
        except Exception:
            pass

    def _on_notify(self, _char, data: bytearray):
        try:
            text = bytes(data).decode(errors="ignore")
        except Exception:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if line:
                self._log(f"<- {line}")
                self.queue.put_nowait(line)

    async def _write_line(self, line: str):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("BLE client is not connected")
        payload = (line.strip() + "\n").encode()
        self._log(f"-> {line}")
        await self.client.write_gatt_char(NUS_RX_UUID, payload, response=False)

    async def _wait_for(self, prefixes: Iterable[str]):
        prefixes = tuple(prefixes)
        while True:
            line = await asyncio.wait_for(self.queue.get(), timeout=self.timeout)
            if line.startswith("PUT_ERR"):
                raise RuntimeError(line)
            if line.startswith(prefixes):
                return line

    async def put_bytes(self, remote_path: str, data: bytes):
        path_b64 = base64.b64encode(remote_path.encode()).decode()
        await self._write_line(f"PUT_BEGIN {path_b64}")
        await self._wait_for(("PUT_OK BEGIN",))

        total = len(data)
        sent = 0
        while sent < total:
            chunk = data[sent: sent + self.chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode()
            await self._write_line(f"PUT_CHUNK {chunk_b64}")
            sent += len(chunk)
            if total:
                pct = sent * 100.0 / total
                self._log(f"   progress {sent}/{total} bytes ({pct:.1f}%)")
            await asyncio.sleep(0.015)

        await self._write_line("PUT_END")
        await self._wait_for(("PUT_OK END",))

    async def reboot(self):
        await self._write_line("RESET")


async def discover_ble_address(name_hint: str, timeout: float = 6.0, log_cb: Callable[[str], None] | None = None) -> str:
    try:
        from bleak import BleakScanner
    except Exception as e:
        raise RuntimeError(
            "BLE upload requires the 'bleak' package. Install it with: pip install bleak"
        ) from e

    log = log_cb or (lambda _msg: None)
    log(f"Scanning for BLE device matching: {name_hint}")
    devices = await BleakScanner.discover(timeout=timeout)
    matches = []
    for dev in devices:
        name = dev.name or ""
        if name_hint.lower() in name.lower():
            matches.append(dev)

    if not matches:
        raise RuntimeError(f"No BLE device found matching name: {name_hint}")

    matches.sort(key=lambda d: getattr(d, "rssi", -999), reverse=True)
    chosen = matches[0]
    log(f"Found {chosen.name} @ {chosen.address}")
    return chosen.address


def should_skip_upload_file(path: Path, rel: str = "") -> bool:
    """
    Skip desktop-side teleop / flasher files so they are not copied to the robot.
    This primarily prevents the currently running teleop application from being
    uploaded when the source root is the desktop app folder.
    """
    try:
        path = Path(path).resolve()
    except Exception:
        path = Path(path)

    rel_posix = (rel or path.name).replace("\\", "/")
    name = path.name.lower()

    current_script = Path(__file__).resolve().name.lower()
    if name == current_script:
        return True

    if "/" not in rel_posix and name.startswith("teleop") and path.suffix.lower() == ".py":
        return True

    return False


def gather_upload_files(source: Path, include_exts: set[str]) -> list[tuple[Path, str]]:
    source = source.resolve()
    files: list[tuple[Path, str]] = []

    if source.is_file():
        if not should_skip_upload_file(source, source.name):
            files.append((source, source.name))
        return files

    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in include_exts:
            continue
        rel = path.relative_to(source).as_posix()
        if should_skip_upload_file(path, rel):
            continue
        files.append((path, rel))
    return files


# ============================================================
# Flash/Deploy worker
# ============================================================
@dataclass
class Job:
    kind: str
    port: str = "AUTO"
    firmware_path: str | None = None
    baud: int = 460800
    source_root: str = "."
    left_pwm: int = 18
    left_dir: int = 19
    right_pwm: int = 21
    right_dir: int = 22
    servo_gpio: int = 23
    ble_address: str = ""
    ble_name: str = "ZebraBot"
    ble_source: str = "."
    ble_dest_root: str = "/"
    ble_chunk_size: int = 45
    ble_reboot: bool = False
    ble_exts: str = ",".join(sorted(DEFAULT_EXTS))


def iter_project_files(source_root: str | Path) -> list[tuple[Path, str]]:
    root = Path(source_root).resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"Project root not found: {root}")

    allowed_suffixes = {'.py', '.mpy'}
    skip_dir_names = {"__pycache__", ".git", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"}
    files: list[tuple[Path, str]] = []

    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in skip_dir_names for part in rel.parts[:-1]):
            continue
        if any(part.startswith('.') for part in rel.parts[:-1]):
            continue
        if path.name.startswith('.'):
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        rel_posix = rel.as_posix()
        if should_skip_upload_file(path, rel_posix):
            continue
        files.append((path, rel_posix))

    files.sort(key=lambda item: item[1])
    return files


class Worker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def _resolve_serial_port(self, requested: str) -> str:
        requested = (requested or "").strip()
        if requested and requested.upper() not in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
            return requested
        return auto_detect_esp32_port(log_cb=self.log.emit)

    def _run(self, cmd: list[str], timeout: int | None = None):
        self.log.emit(">> " + " ".join(cmd))
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if cp.stdout:
            self.log.emit(cp.stdout.rstrip())
        if cp.stderr:
            self.log.emit(cp.stderr.rstrip())
        if cp.returncode != 0:
            raise RuntimeError(f"Command failed with code {cp.returncode}")

    def _python_m(self, module: str, *args: str) -> list[str]:
        return [sys.executable, "-m", module, *args]

    def _emit_progress(self, current: int, total: int, label: str = ""):
        try:
            self.progress.emit(int(current), int(total), str(label or ""))
        except Exception:
            pass

    def run(self):
        try:
            if self.job.kind == "flash":
                if not self.job.firmware_path or not os.path.exists(self.job.firmware_path):
                    raise RuntimeError("Firmware path is missing or does not exist.")

                port = self._resolve_serial_port(self.job.port)
                self.log.emit(f">> using serial port: {port}")

                self._run(
                    self._python_m(
                        "esptool",
                        "--chip", "esp32",
                        "--port", port,
                        "erase_flash",
                    ),
                    timeout=180,
                )
                self._run(
                    self._python_m(
                        "esptool",
                        "--chip", "esp32",
                        "--port", port,
                        "--baud", str(self.job.baud),
                        "write_flash", "-z", "0x1000",
                        self.job.firmware_path,
                    ),
                    timeout=240,
                )
                self.done.emit(True, "Flash complete.")

            elif self.job.kind == "deploy":
                source_root = os.path.abspath(self.job.source_root)

                tmp_stage = None
                try:
                    tmp_stage, deploy_root = build_staged_runtime_project(source_root)

                    main_src = os.path.join(str(deploy_root), "main.py")
                    user_main_src = os.path.join(str(deploy_root), "user_main.py")

                    if not os.path.isfile(main_src):
                        raise RuntimeError(f"main.py not found: {main_src}")

                    if not os.path.isfile(user_main_src):
                        raise RuntimeError(
                            "user_main.py was not created in the staged deploy tree. "
                            "If you are deploying a student project, select that specific project folder so its main.py can be copied to user_main.py. "
                            f"Staged path checked: {user_main_src}"
                        )

                    project_files = iter_project_files(deploy_root)
                    if not project_files:
                        raise RuntimeError(f"No deployable files found in: {deploy_root}")

                    self.log.emit(f">> deploying selected project from {source_root}")
                    if str(Path(deploy_root).resolve()) != str(Path(source_root).resolve()):
                        self.log.emit(f">> staged runtime deploy tree: {deploy_root}")
                    self.log.emit(f">> found {len(project_files)} file(s) to upload")

                    port = self._resolve_serial_port(self.job.port)
                    self.log.emit(f">> using serial port: {port}")

                    def mp(*a: str) -> list[str]:
                        return [sys.executable, "-m", "mpremote", "connect", port, *a]

                    total_files = len(project_files)
                    self._emit_progress(0, total_files, "Preparing upload...")
                    created_dirs: set[str] = set()
                    for index, (local_path, rel) in enumerate(project_files, start=1):
                        parent = Path(rel).parent.as_posix()
                        if parent and parent != ".":
                            parts = []
                            for part in parent.split("/"):
                                parts.append(part)
                                remote_dir = ":/" + "/".join(parts)
                                if remote_dir in created_dirs:
                                    continue
                                try:
                                    self._run(mp("fs", "mkdir", remote_dir), timeout=30)
                                except Exception:
                                    self.log.emit(f">> (mkdir {remote_dir}) exists or not supported; continuing...")
                                created_dirs.add(remote_dir)

                        remote_path = ":/" + rel
                        self.log.emit(f">> [{index}/{total_files}] uploading {rel}")
                        self._run(mp("fs", "cp", str(local_path), remote_path), timeout=90)
                        self._emit_progress(index, total_files, rel)

                    self._emit_progress(total_files, total_files, "Resetting device...")
                    self._run(mp("reset"), timeout=30)

                    self.done.emit(
                        True,
                        f"Deploy complete: uploaded {len(project_files)} project file(s) to the ESP32."
                    )
                finally:
                    if tmp_stage is not None:
                        try:
                            tmp_stage.cleanup()
                        except Exception:
                            pass

            elif self.job.kind == "ble_deploy":
                asyncio.run(self._run_ble_deploy())
                self.done.emit(True, "BLE code upload complete.")

            else:
                raise RuntimeError(f"Unknown job kind: {self.job.kind}")

        except Exception as e:
            self.done.emit(False, str(e))


    async def _run_ble_deploy(self):
        source = Path(self.job.ble_source).resolve()
        if not source.exists():
            raise RuntimeError(f"Source does not exist: {source}")

        tmp_stage = None
        try:
            tmp_stage, deploy_root = build_staged_runtime_project(source)

            include_exts = {
                e.strip().lower() if e.strip().startswith('.') else '.' + e.strip().lower()
                for e in self.job.ble_exts.split(',') if e.strip()
            }

            files = gather_upload_files(deploy_root, include_exts)
            if not files:
                raise RuntimeError("No matching files found to upload.")

            dest_root = (self.job.ble_dest_root or "/").strip()
            if not dest_root.startswith("/"):
                dest_root = "/" + dest_root
            dest_root = dest_root.rstrip("/") or "/"

            address = self.job.ble_address.strip()
            if not address:
                address = await discover_ble_address(
                    self.job.ble_name.strip() or "ZebraBot",
                    log_cb=self.log.emit,
                )

            async with GuiBleCodeUploader(
                address=address,
                chunk_size=int(self.job.ble_chunk_size),
                log_cb=self.log.emit,
            ) as up:
                total_files = len(files)
                self._emit_progress(0, total_files, "Preparing BLE upload...")
                for index, (local_path, rel) in enumerate(files, start=1):
                    remote_path = (dest_root + "/" + rel).replace("//", "/")
                    data = local_path.read_bytes()
                    self.log.emit(f"Uploading {local_path} -> {remote_path}")
                    await up.put_bytes(remote_path, data)
                    self._emit_progress(index, total_files, rel)

                if self.job.ble_reboot:
                    self.log.emit("Requesting robot reboot...")
                    await up.reboot()
        finally:
            if tmp_stage is not None:
                try:
                    tmp_stage.cleanup()
                except Exception:
                    pass

    def _patch_config_text(
        self,
        text: str,
        left_pwm: int,
        left_dir: int,
        right_pwm: int,
        right_dir: int,
        servo_gpio: int,
    ) -> str:
        lines = text.splitlines()
        out = []

        replacements = {
            "LEFT_PWM": left_pwm,
            "LEFT_DIR": left_dir,
            "RIGHT_PWM": right_pwm,
            "RIGHT_DIR": right_dir,
            "STEER_SERVO_GPIO": servo_gpio,
        }

        for line in lines:
            stripped = line.strip()
            replaced = False
            for key, value in replacements.items():
                if stripped.startswith(f"{key} ") or stripped.startswith(f"{key}="):
                    out.append(f"{key} = {value}")
                    replaced = True
                    break
            if not replaced:
                out.append(line)

        return "\n".join(out) + "\n"



class SerialMonitorWorker(QThread):
    log = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, port: str, baud: int = DEFAULT_SERIAL_MONITOR_BAUD, duration_s: float = POST_JOB_SERIAL_CAPTURE_SECONDS):
        super().__init__()
        self.port = str(port or "").strip()
        self.baud = int(baud)
        self.duration_s = float(duration_s)
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        if not self.port:
            self.status.emit("Serial monitor skipped: no serial port selected.")
            return

        try:
            import serial
        except Exception as e:
            self.status.emit(f"Serial monitor unavailable: {e}")
            return

        ser = None
        try:
            self.status.emit(f"Opening serial log monitor on {self.port} @ {self.baud} baud...")
            ser = serial.Serial(self.port, self.baud, timeout=0.25)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass

            deadline = time.time() + max(1.0, self.duration_s)
            saw_output = False

            while (not self._stop_requested) and time.time() < deadline:
                try:
                    raw = ser.readline()
                except Exception as e:
                    self.status.emit(f"Serial monitor read failed: {e}")
                    break

                if not raw:
                    continue

                saw_output = True
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.log.emit(f"[SERIAL] {line}")

            if self._stop_requested:
                self.status.emit("Serial log monitor stopped.")
            elif saw_output:
                self.status.emit("Serial log capture complete.")
            else:
                self.status.emit("Serial log monitor timed out with no output.")
        except Exception as e:
            self.status.emit(f"Serial monitor failed: {e}")
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

class JobStatusDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Flash / Deploy Status")
        self.setModal(False)
        self.resize(720, 420)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.method_label = QLabel("-")
        self.status_label = QLabel("Idle")
        self.target_label = QLabel("-")
        self.method_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Method:", self.method_label)
        form.addRow("Status:", self.status_label)
        form.addRow("Target:", self.target_label)
        layout.addLayout(form)

        self.progress_label = QLabel("Waiting to start...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.log_view, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.hide)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

    def start_job(self, method: str, target: str):
        self.method_label.setText(method)
        self.target_label.setText(target or "-")
        self.status_label.setText("Running")
        self.progress_label.setText("Waiting to start...")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.log_view.clear()
        self.show()
        self.raise_()
        self.activateWindow()

    def append_log(self, text: str):
        append_rich_log(self.log_view, text)

    def update_progress(self, current: int, total: int, label: str = ""):
        total = max(1, int(total))
        current = max(0, min(int(current), total))
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        label = (label or "").strip()
        if label:
            self.progress_label.setText(f"{current}/{total} files uploaded — {label}")
        else:
            self.progress_label.setText(f"{current}/{total} files uploaded")

    def finish_job(self, ok: bool, message: str):
        self.status_label.setText("Completed" if ok else "Failed")
        if ok:
            self.progress_bar.setValue(self.progress_bar.maximum())
        if message:
            self.append_log(("✅ " if ok else "❌ ") + message)


class DeviceSelectionDialog(QDialog):
    def __init__(self, mode: str, parent=None, serial_candidates: list[dict] | None = None, ble_name_hint: str = ""):
        super().__init__(parent)
        self.mode = mode
        self.serial_candidates = serial_candidates or []
        self.selected_serial_port = ""
        self.selected_ble_name = ""
        self.selected_ble_address = ""
        self.selected_ble_rssi = None
        self._seen_ble: dict[str, dict] = {}
        self.agent = None

        self.setModal(True)
        self.resize(680, 420)
        self.setWindowTitle("Select Target Device")

        layout = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        controls = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        controls.addWidget(self.refresh_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.detail_label = QLabel("Select a device to continue.")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.ok_btn = QPushButton("Use Selected Device")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept_selection)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.list_widget.currentItemChanged.connect(self.on_item_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept_selection())

        self.ble_name_hint = (ble_name_hint or "").strip().lower()
        self.refresh_devices()

    def closeEvent(self, event):
        try:
            if self.agent and self.agent.isActive():
                self.agent.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def on_item_changed(self, current, _previous):
        data = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not data:
            self.detail_label.setText("Select a device to continue.")
            return
        if self.mode == "serial":
            desc = data.get("description") or "serial device"
            vid = data.get("vid")
            pid = data.get("pid")
            vidpid = f"{vid:04X}:{pid:04X}" if isinstance(vid, int) and isinstance(pid, int) else "unknown"
            self.detail_label.setText(
                f"Serial device: {data.get('device', '-')}\nDescription: {desc}\nVID:PID: {vidpid}"
            )
        else:
            self.detail_label.setText(
                f"BLE device: {data.get('name', '(unnamed)')}\nAddress: {data.get('address', '-')}\nRSSI: {data.get('rssi', '?')}"
            )

    def refresh_devices(self):
        if self.mode == "serial":
            self.refresh_serial_devices()
        else:
            self.refresh_ble_devices()

    def refresh_serial_devices(self):
        if not self.serial_candidates:
            self.serial_candidates = list_serial_candidates()
        self.header.setText("Choose a detected USB serial device for flashing or serial deploy.")
        self.list_widget.clear()
        for info in self.serial_candidates:
            label = f"{info.get('device', '?')} — {info.get('description') or 'serial device'}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
            self.ok_btn.setEnabled(True)
        else:
            self.detail_label.setText("No USB serial devices were found.")
            self.ok_btn.setEnabled(False)

    def refresh_ble_devices(self):
        self.header.setText("Choose a Bluetooth LE device for code deploy. Use Refresh to rescan nearby BLE devices.")
        self.list_widget.clear()
        self.ok_btn.setEnabled(False)
        self.detail_label.setText("Scanning for BLE devices...")
        self._seen_ble = {}
        self.agent = QBluetoothDeviceDiscoveryAgent(self)
        self.agent.setLowEnergyDiscoveryTimeout(5000)
        self.agent.deviceDiscovered.connect(self.on_ble_device_discovered)
        self.agent.finished.connect(self.on_ble_scan_finished)
        self.agent.canceled.connect(self.on_ble_scan_finished)
        self.agent.errorOccurred.connect(self.on_ble_scan_error)
        self.agent.start(QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod)

    def on_ble_device_discovered(self, info):
        try:
            is_ble = bool(info.coreConfigurations() & QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration)
        except Exception:
            is_ble = True
        if not is_ble:
            return
        name = info.name() or "(unnamed)"
        address = info.address().toString()
        rssi = info.rssi()
        payload = {"name": name, "address": address, "rssi": rssi}
        self._seen_ble[address] = payload
        self._rebuild_ble_list()

    def _rebuild_ble_list(self):
        items = sorted(
            self._seen_ble.values(),
            key=lambda d: (
                0 if self.ble_name_hint and self.ble_name_hint in (d.get('name') or '').lower() else 1,
                -(int(d.get('rssi', -999)) if isinstance(d.get('rssi', None), int) else -999),
                (d.get('name') or '').lower(),
                d.get('address') or '',
            ),
        )
        selected_address = ""
        current = self.list_widget.currentItem()
        if current:
            data = current.data(Qt.ItemDataRole.UserRole) or {}
            selected_address = data.get("address", "")
        self.list_widget.clear()
        selected_row = 0
        for idx, data in enumerate(items):
            label = f"{data.get('name', '(unnamed)')} — {data.get('address', '-')} — RSSI {data.get('rssi', '?')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.list_widget.addItem(item)
            if selected_address and data.get("address") == selected_address:
                selected_row = idx
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(selected_row)
            self.ok_btn.setEnabled(True)
        else:
            self.ok_btn.setEnabled(False)

    def on_ble_scan_finished(self):
        if self.list_widget.count() == 0:
            self.detail_label.setText("No BLE devices found.")
        else:
            self.detail_label.setText(f"Found {self.list_widget.count()} BLE device(s).")

    def on_ble_scan_error(self, error):
        self.detail_label.setText(f"BLE scan error: {error}")

    def accept_selection(self):
        item = self.list_widget.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        if self.mode == "serial":
            self.selected_serial_port = data.get("device", "")
        else:
            self.selected_ble_name = data.get("name", "")
            self.selected_ble_address = data.get("address", "")
            self.selected_ble_rssi = data.get("rssi")
        self.accept()


class DeployConfigDialog(QDialog):
    def __init__(self, parent, source_root: str, current_method: str, current_port: str, current_ble_name: str, current_ble_addr: str, current_ble_dest_root: str, current_ble_chunk: int, current_ble_exts: str, current_ble_reboot: bool):
        super().__init__(parent)
        self.setWindowTitle("Configure Project Deploy")
        self.resize(640, 320)
        self.result_payload = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Review the deploy settings before starting. This dialog lets you choose the upload method and target device without starting the deploy until you press Start Deploy."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.source_label = QLineEdit(source_root)
        self.source_label.setReadOnly(True)

        self.method_combo = QComboBox()
        self.method_combo.addItem("Serial (mpremote)", "serial")
        self.method_combo.addItem("Bluetooth LE (BLE)", "ble")
        idx = self.method_combo.findData(current_method or "serial")
        self.method_combo.setCurrentIndex(max(idx, 0))

        self.serial_port_edit = QLineEdit(current_port or "AUTO")
        self.btn_pick_serial = QPushButton("Choose Serial Device…")
        self.btn_pick_serial.clicked.connect(self.pick_serial_device)
        serial_row = QHBoxLayout()
        serial_row.addWidget(self.serial_port_edit, 1)
        serial_row.addWidget(self.btn_pick_serial)
        serial_wrap = QWidget()
        serial_wrap.setLayout(serial_row)

        self.ble_name_edit = QLineEdit(current_ble_name or "ZebraBot")
        self.ble_addr_edit = QLineEdit(current_ble_addr or "")
        self.btn_pick_ble = QPushButton("Choose BLE Device…")
        self.btn_pick_ble.clicked.connect(self.pick_ble_device)
        ble_addr_row = QHBoxLayout()
        ble_addr_row.addWidget(self.ble_addr_edit, 1)
        ble_addr_row.addWidget(self.btn_pick_ble)
        ble_addr_wrap = QWidget()
        ble_addr_wrap.setLayout(ble_addr_row)

        self.ble_dest_root_edit = QLineEdit(current_ble_dest_root or "/")
        self.ble_chunk_spin = QSpinBox()
        self.ble_chunk_spin.setRange(10, 180)
        self.ble_chunk_spin.setValue(int(current_ble_chunk or 45))
        self.ble_exts_edit = QLineEdit(current_ble_exts or ",".join(sorted(DEFAULT_EXTS)))
        self.ble_reboot_check = QCheckBox("Reboot robot after upload")
        self.ble_reboot_check.setChecked(bool(current_ble_reboot))

        form.addRow("Project Root:", self.source_label)
        form.addRow("Upload Method:", self.method_combo)
        form.addRow("Serial Port:", serial_wrap)
        form.addRow("BLE Name Hint:", self.ble_name_edit)
        form.addRow("BLE Address:", ble_addr_wrap)
        form.addRow("Robot Dest Root:", self.ble_dest_root_edit)
        form.addRow("BLE Chunk Size:", self.ble_chunk_spin)
        form.addRow("Include Extensions:", self.ble_exts_edit)
        form.addRow("Options:", self.ble_reboot_check)
        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_start = QPushButton("Start Deploy")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_start.clicked.connect(self.accept_selection)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_start)
        layout.addLayout(buttons)

        self.method_combo.currentIndexChanged.connect(self._update_mode_visibility)
        self._update_mode_visibility()

    def _update_mode_visibility(self):
        is_serial = self.method_combo.currentData() == "serial"
        self.serial_port_edit.setEnabled(is_serial)
        self.btn_pick_serial.setEnabled(is_serial)
        self.ble_name_edit.setEnabled(not is_serial)
        self.ble_addr_edit.setEnabled(not is_serial)
        self.btn_pick_ble.setEnabled(not is_serial)
        self.ble_dest_root_edit.setEnabled(not is_serial)
        self.ble_chunk_spin.setEnabled(not is_serial)
        self.ble_exts_edit.setEnabled(not is_serial)
        self.ble_reboot_check.setEnabled(not is_serial)
        self.status_label.setText(
            "Serial deploy copies the selected project over USB with mpremote." if is_serial
            else "BLE deploy uploads the selected project over Bluetooth LE."
        )

    def pick_serial_device(self):
        candidates = list_serial_candidates()
        dlg = DeviceSelectionDialog("serial", self, serial_candidates=candidates)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_serial_port:
            self.serial_port_edit.setText(dlg.selected_serial_port)

    def pick_ble_device(self):
        dlg = DeviceSelectionDialog("ble", self, ble_name_hint=self.ble_name_edit.text().strip())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_ble_address:
            self.ble_addr_edit.setText(dlg.selected_ble_address)
            if dlg.selected_ble_name:
                self.ble_name_edit.setText(dlg.selected_ble_name)

    def accept_selection(self):
        method = self.method_combo.currentData()
        payload = {
            "method": method,
            "source_root": self.source_label.text().strip(),
            "serial_port": self.serial_port_edit.text().strip(),
            "ble_name": self.ble_name_edit.text().strip(),
            "ble_addr": self.ble_addr_edit.text().strip(),
            "ble_dest_root": self.ble_dest_root_edit.text().strip() or "/",
            "ble_chunk_size": int(self.ble_chunk_spin.value()),
            "ble_exts": self.ble_exts_edit.text().strip() or ",".join(sorted(DEFAULT_EXTS)),
            "ble_reboot": bool(self.ble_reboot_check.isChecked()),
        }
        if method == "serial":
            port = payload["serial_port"]
            if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
                QMessageBox.information(self, "Select serial device", "Choose a USB serial device before starting serial deploy.")
                return
        else:
            if not payload["ble_addr"]:
                QMessageBox.information(self, "Select BLE device", "Choose a Bluetooth LE device before starting BLE deploy.")
                return
        self.result_payload = payload
        self.accept()


# ============================================================
# Tab 1: Flash + Deploy UI
# ============================================================
class FlashDeployTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker: Worker | None = None
        self.serial_monitor: SerialMonitorWorker | None = None
        self.status_dialog = JobStatusDialog(self)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        g_conn = QGroupBox("Connection")
        f_conn = QFormLayout(g_conn)
        default_port = "AUTO" if sys.platform != "win32" else "AUTO"
        self.port_edit = QLineEdit(default_port)
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 3_000_000)
        self.baud_spin.setValue(460800)
        self.btn_scan_ports = QPushButton("Auto Detect Port")
        self.btn_scan_ports.clicked.connect(self.detect_port)
        self.port_list = QComboBox()
        self.port_list.currentIndexChanged.connect(self.on_port_candidate_selected)
        self.auto_scan_check = QCheckBox("Continuous USB scan")
        self.auto_scan_check.setChecked(True)
        self.auto_scan_check.toggled.connect(self.on_auto_scan_toggled)
        self.scan_status = QLabel("Waiting for USB serial devices...")
        self.scan_status.setWordWrap(True)
        self.selected_target_label = QLabel("No target explicitly selected yet.")
        self.selected_target_label.setWordWrap(True)
        self.btn_choose_serial_target = QPushButton("Choose Serial Device…")
        self.btn_choose_serial_target.clicked.connect(self.choose_serial_target)
        self.btn_choose_ble_target = QPushButton("Choose BLE Device…")
        self.btn_choose_ble_target.clicked.connect(self.choose_ble_target)

        port_row = QHBoxLayout()
        port_row.addWidget(self.port_edit, 1)
        port_row.addWidget(self.btn_scan_ports)
        f_conn.addRow("Serial Port:", port_row)
        f_conn.addRow("Detected Devices:", self.port_list)
        f_conn.addRow("USB Scan:", self.auto_scan_check)
        f_conn.addRow("Scan Status:", self.scan_status)
        f_conn.addRow("Selected Target:", self.selected_target_label)
        serial_target_row = QHBoxLayout()
        serial_target_row.addWidget(self.btn_choose_serial_target)
        serial_target_row.addWidget(self.btn_choose_ble_target)
        f_conn.addRow("Target Picker:", serial_target_row)
        f_conn.addRow("Flash Baud:", self.baud_spin)

        g_fw = QGroupBox("Firmware")
        fw_layout = QHBoxLayout(g_fw)
        self.fw_path = QLineEdit("")
        self.btn_pick_fw = QPushButton("Choose .bin…")
        self.btn_pick_fw.clicked.connect(self.pick_firmware)
        fw_layout.addWidget(QLabel("Firmware .bin:"))
        fw_layout.addWidget(self.fw_path, 1)
        fw_layout.addWidget(self.btn_pick_fw)

        g_deploy = QGroupBox("Code Deploy")
        deploy = QFormLayout(g_deploy)

        self.deploy_method = QComboBox()
        self.deploy_method.addItem("Serial (mpremote)", "serial")
        self.deploy_method.addItem("Bluetooth LE (BLE)", "ble")
        self.deploy_method.currentIndexChanged.connect(self.on_deploy_method_changed)

        self.source_root = QLineEdit(os.path.dirname(os.path.abspath(__file__)))
        self.btn_pick_source = QPushButton("Choose Project Folder…")
        self.btn_pick_source.clicked.connect(self.pick_source_root)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_root, 1)
        source_row.addWidget(self.btn_pick_source)

        self.ble_name_edit = QLineEdit("ZebraBot")
        self.ble_addr_edit = QLineEdit("")
        self.ble_dest_root_edit = QLineEdit("/")
        self.ble_chunk_spin = QSpinBox()
        self.ble_chunk_spin.setRange(10, 180)
        self.ble_chunk_spin.setValue(45)
        self.ble_exts_edit = QLineEdit(",".join(sorted(DEFAULT_EXTS)))
        self.ble_reboot_check = QCheckBox("Reboot robot after upload")

        deploy.addRow("Upload Method:", self.deploy_method)
        deploy.addRow("Project Root:", source_row)
        deploy.addRow("BLE Name Hint:", self.ble_name_edit)
        deploy.addRow("BLE Address (optional):", self.ble_addr_edit)
        deploy.addRow("Robot Dest Root:", self.ble_dest_root_edit)
        deploy.addRow("BLE Chunk Size:", self.ble_chunk_spin)
        deploy.addRow("Include Extensions:", self.ble_exts_edit)
        deploy.addRow("Options:", self.ble_reboot_check)

        deploy_help = QLabel(
            "If you select one of the built-in editor projects from the projects/ folder, teleop will deploy it as a student project: "
            "the app's runtime main.py and robot/ package are staged automatically, and the selected project's main.py is uploaded as user_main.py. "
            "External folders can still be deployed as full runtime projects. "
            "Choose Serial to copy with mpremote, or BLE to upload the same staged code set over Bluetooth."
        )
        deploy_help.setWordWrap(True)

        actions = QHBoxLayout()
        self.btn_flash = QPushButton("Erase + Flash Firmware")
        self.btn_deploy = QPushButton("Deploy Project to ESP32")
        self.btn_flash.clicked.connect(self.do_flash)
        self.btn_deploy.clicked.connect(self.do_deploy)
        actions.addWidget(self.btn_flash)
        actions.addWidget(self.btn_deploy)

        self.deploy_progress_label = QLabel("No upload in progress.")
        self.deploy_progress = QProgressBar()
        self.deploy_progress.setRange(0, 1)
        self.deploy_progress.setValue(0)

        self.serial_log_hint = QLabel(
            "After serial flash or deploy, teleop opens the serial port for about 90 seconds and streams boot logs, warnings, errors, and Python tracebacks here automatically."
        )
        self.serial_log_hint.setWordWrap(True)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace;")

        layout.addWidget(g_conn)
        layout.addWidget(g_fw)
        layout.addWidget(g_deploy)
        layout.addWidget(deploy_help)
        layout.addLayout(actions)
        layout.addWidget(self.deploy_progress_label)
        layout.addWidget(self.deploy_progress)
        layout.addWidget(self.serial_log_hint)
        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.log, 1)

        self._last_seen_ports: list[str] = []
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(2000)
        self.scan_timer.timeout.connect(self.refresh_port_candidates)

        self.on_deploy_method_changed()
        self.refresh_port_candidates(initial=True)
        self.scan_timer.start()

    def append_log(self, text: str):
        append_rich_log(self.log, text)

    def on_deploy_method_changed(self):
        is_serial = self.deploy_method.currentData() == "serial"
        self.port_edit.setEnabled(is_serial)
        self.port_list.setEnabled(is_serial)
        self.auto_scan_check.setEnabled(is_serial)
        self.btn_scan_ports.setEnabled(is_serial)
        self.btn_choose_serial_target.setEnabled(is_serial)
        self.btn_choose_ble_target.setEnabled(not is_serial)
        self.baud_spin.setEnabled(is_serial)
        self.ble_name_edit.setEnabled(not is_serial)
        self.ble_addr_edit.setEnabled(not is_serial)
        self.ble_dest_root_edit.setEnabled(not is_serial)
        self.ble_chunk_spin.setEnabled(not is_serial)
        self.ble_exts_edit.setEnabled(not is_serial)
        self.ble_reboot_check.setEnabled(not is_serial)

    def set_busy(self, busy: bool):
        self.btn_flash.setEnabled(not busy)
        self.btn_deploy.setEnabled(not busy)
        self.btn_pick_fw.setEnabled(not busy)
        is_serial = self.deploy_method.currentData() == "serial"
        self.btn_scan_ports.setEnabled((not busy) and is_serial)
        self.btn_choose_serial_target.setEnabled((not busy) and is_serial)
        self.btn_choose_ble_target.setEnabled((not busy) and (not is_serial))
        self.btn_pick_source.setEnabled(not busy)
        self.deploy_method.setEnabled(not busy)
        self.port_list.setEnabled((not busy) and is_serial)
        self.auto_scan_check.setEnabled((not busy) and is_serial)


    def stop_serial_monitor(self):
        if self.serial_monitor is not None:
            try:
                self.serial_monitor.stop()
                self.serial_monitor.wait(1500)
            except Exception:
                pass
            self.serial_monitor = None

    def start_serial_monitor(self, port: str, baud: int = DEFAULT_SERIAL_MONITOR_BAUD, duration_s: float = POST_JOB_SERIAL_CAPTURE_SECONDS):
        port = (port or "").strip()
        if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
            return
        self.stop_serial_monitor()
        self.append_log(f"Starting post-flash serial log capture on {port} for {duration_s:.0f} seconds...")
        self.serial_monitor = SerialMonitorWorker(port=port, baud=baud, duration_s=duration_s)
        self.serial_monitor.log.connect(self.append_log)
        self.serial_monitor.log.connect(self.status_dialog.append_log)
        self.serial_monitor.status.connect(self.on_serial_monitor_status)
        self.serial_monitor.start()

    def on_serial_monitor_status(self, text: str):
        if text:
            self.append_log(text)
            self.status_dialog.append_log(text)

    def _format_port_candidate(self, info: dict) -> str:
        device = info.get("device", "?")
        desc = info.get("description") or "serial device"
        vid = info.get("vid")
        pid = info.get("pid")
        vidpid = ""
        if isinstance(vid, int) and isinstance(pid, int):
            vidpid = f" [{vid:04X}:{pid:04X}]"
        return f"{device} — {desc}{vidpid}"

    def on_auto_scan_toggled(self, checked: bool):
        if checked:
            self.refresh_port_candidates(force_log=True)
            self.scan_timer.start()
        else:
            self.scan_timer.stop()
            self.scan_status.setText("Continuous USB scan paused.")

    def on_port_candidate_selected(self, _index: int):
        data = self.port_list.currentData()
        if not data:
            return
        current_text = self.port_edit.text().strip()
        if not current_text or current_text.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
            self.port_edit.setText(str(data))
            self.append_log(f"Selected detected serial port: {data}")

    def refresh_port_candidates(self, initial: bool = False, force_log: bool = False):
        try:
            candidates = list_serial_candidates()
        except Exception as e:
            self.scan_status.setText(f"USB scan failed: {e}")
            if force_log:
                self.append_log(f"USB scan failed: {e}")
            return

        devices = [c.get("device", "") for c in candidates if c.get("device")]
        changed = devices != self._last_seen_ports
        self._last_seen_ports = devices

        previous_data = self.port_list.currentData()
        with QSignalBlocker(self.port_list):
            self.port_list.clear()
            self.port_list.addItem("AUTO / detect at run time", "AUTO")
            for info in candidates:
                self.port_list.addItem(self._format_port_candidate(info), info.get("device", ""))

            restore_value = previous_data if previous_data in devices or previous_data == "AUTO" else None
            if restore_value is not None:
                idx = self.port_list.findData(restore_value)
                if idx >= 0:
                    self.port_list.setCurrentIndex(idx)
            elif candidates:
                self.port_list.setCurrentIndex(1)
            else:
                self.port_list.setCurrentIndex(0)

        if candidates:
            best = candidates[0]
            self.scan_status.setText(
                f"Found {len(candidates)} USB serial device(s). Best match: {best.get('device', '?')}"
            )
            current_text = self.port_edit.text().strip()
            if not current_text or current_text.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
                chosen = best.get("device", "AUTO") or "AUTO"
                self.port_edit.setText(chosen)
                self.selected_target_label.setText(f"Suggested serial target: {chosen}")
        else:
            self.scan_status.setText("No USB serial devices detected. Plug in the ESP32 and wait for the next scan.")
            current_text = self.port_edit.text().strip()
            if not current_text:
                self.port_edit.setText("AUTO")

        if changed or force_log or initial:
            if candidates:
                self.append_log("USB serial scan updated:")
                for info in candidates[:12]:
                    self.append_log(f"  - {self._format_port_candidate(info)}")
            else:
                self.append_log("USB serial scan updated: no devices detected")

    def choose_serial_target(self):
        candidates = list_serial_candidates()
        dlg = DeviceSelectionDialog("serial", self, serial_candidates=candidates)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_serial_port:
            port = dlg.selected_serial_port
            self.port_edit.setText(port)
            idx = self.port_list.findData(port)
            if idx >= 0:
                self.port_list.setCurrentIndex(idx)
            self.selected_target_label.setText(f"Serial target selected: {port}")
            self.append_log(f"Serial target selected from dialog: {port}")

    def choose_ble_target(self):
        dlg = DeviceSelectionDialog("ble", self, ble_name_hint=self.ble_name_edit.text().strip())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_ble_address:
            self.ble_addr_edit.setText(dlg.selected_ble_address)
            if dlg.selected_ble_name:
                self.ble_name_edit.setText(dlg.selected_ble_name)
            self.selected_target_label.setText(
                f"BLE target selected: {dlg.selected_ble_name or '(unnamed)'} @ {dlg.selected_ble_address}"
            )
            self.append_log(
                f"BLE target selected from dialog: {dlg.selected_ble_name or '(unnamed)'} @ {dlg.selected_ble_address}"
            )

    def detect_port(self):
        try:
            self.refresh_port_candidates(force_log=True)
            port = auto_detect_esp32_port(log_cb=self.append_log)
            self.port_edit.setText(port)
            idx = self.port_list.findData(port)
            if idx >= 0:
                self.port_list.setCurrentIndex(idx)
            self.selected_target_label.setText(f"Serial target selected: {port}")
            self.append_log(f"Auto-detected serial port: {port}")
        except Exception as e:
            QMessageBox.warning(self, "Port detection failed", str(e))
            self.append_log(f"Port detection failed: {e}")

    def pick_source_root(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select project root (folder containing robot/ and main.py)",
            self.source_root.text().strip() or ".",
        )
        if path:
            self.source_root.setText(path)

    def pick_firmware(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select MicroPython firmware (.bin)",
            "",
            "Firmware (*.bin);;All Files (*)",
        )
        if path:
            self.fw_path.setText(path)

    def _describe_job(self, job: Job) -> tuple[str, str]:
        if job.kind == "flash":
            method = "Serial firmware flash (esptool)"
            target = job.port or "AUTO"
        elif job.kind == "deploy":
            method = "Serial project deploy (mpremote)"
            target = job.port or "AUTO"
        elif job.kind == "ble_deploy":
            method = "Bluetooth LE project deploy"
            target = job.ble_address or job.ble_name or "BLE auto-discovery"
        else:
            method = job.kind
            target = "-"
        return method, target

    def _run_job(self, job: Job):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A job is already running.")
            return

        self.stop_serial_monitor()
        self.append_log("\n==============================")
        self.append_log(f"Starting job: {job.kind}")
        method, target = self._describe_job(job)
        self.deploy_progress.setRange(0, 1)
        self.deploy_progress.setValue(0)
        self.deploy_progress_label.setText("Preparing job...")
        self.status_dialog.start_job(method, target)
        self.status_dialog.append_log(f"Starting job: {job.kind}")
        self.set_busy(True)

        self.worker = Worker(job)
        self.worker.log.connect(self.append_log)
        self.worker.log.connect(self.status_dialog.append_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.progress.connect(self.status_dialog.update_progress)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def on_worker_progress(self, current: int, total: int, label: str = ""):
        total = max(1, int(total))
        current = max(0, min(int(current), total))
        self.deploy_progress.setRange(0, total)
        self.deploy_progress.setValue(current)
        label = (label or "").strip()
        if label:
            self.deploy_progress_label.setText(f"Uploading {current}/{total}: {label}")
        else:
            self.deploy_progress_label.setText(f"Uploading {current}/{total}")

    def do_flash(self):
        port = self.port_edit.text().strip()
        fw = self.fw_path.text().strip()
        baud = int(self.baud_spin.value())

        if not fw or not os.path.exists(fw):
            QMessageBox.warning(self, "Missing firmware", "Please choose a valid firmware .bin file.")
            return

        if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
            self.choose_serial_target()
            port = self.port_edit.text().strip()
        if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
            QMessageBox.information(self, "Select serial device", "Choose a USB serial device before flashing.")
            return

        self._run_job(Job(kind="flash", port=port, firmware_path=fw, baud=baud))

    def do_deploy(self):
        method = self.deploy_method.currentData()
        source_root = self.source_root.text().strip()

        if not source_root or not os.path.isdir(source_root):
            QMessageBox.warning(self, "Missing source root", "Please choose a valid project root.")
            return

        source_path = Path(source_root).resolve()
        is_editor_project = is_editor_project_root(source_path)

        if is_editor_project:
            student_main = source_path / "main.py"
            student_user_main = source_path / "user_main.py"
            if not student_main.is_file() and not student_user_main.is_file():
                QMessageBox.warning(
                    self,
                    "Missing student entry file",
                    f"Could not find:\n{student_main}\n\nor\n\n{student_user_main}"
                )
                return
        else:
            robot_dir = os.path.join(source_root, "robot")
            main_py = os.path.join(source_root, "main.py")

            if not os.path.isdir(robot_dir):
                QMessageBox.warning(self, "Missing robot folder", f"Could not find:\n{robot_dir}")
                return

            if not os.path.isfile(main_py):
                QMessageBox.warning(self, "Missing main.py", f"Could not find:\n{main_py}")
                return

            if source_path == app_root_dir().resolve() and (source_path / "projects").is_dir():
                QMessageBox.warning(
                    self,
                    "Select a specific project",
                    "The selected folder is the teleop application root. Choose a specific student project folder inside projects/ or use the Project Editor's Configure + Deploy button so teleop can stage that project's main.py as user_main.py."
                )
                return

        if method == "serial":
            port = self.port_edit.text().strip()
            if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
                self.choose_serial_target()
                port = self.port_edit.text().strip()
            if not port or port.upper() in {"AUTO", "AUTO-DETECT", "AUTODETECT"}:
                QMessageBox.information(self, "Select serial device", "Choose a USB serial device before serial deploy.")
                return

            self._run_job(Job(
                kind="deploy",
                port=port,
                baud=int(self.baud_spin.value()),
                source_root=source_root,
            ))
            return

        if not self.ble_addr_edit.text().strip():
            self.choose_ble_target()
        if not self.ble_addr_edit.text().strip():
            QMessageBox.information(self, "Select BLE device", "Choose a Bluetooth LE device before BLE deploy.")
            return

        self._run_job(Job(
            kind="ble_deploy",
            ble_address=self.ble_addr_edit.text().strip(),
            ble_name=self.ble_name_edit.text().strip() or "ZebraBot",
            ble_source=source_root,
            ble_dest_root=self.ble_dest_root_edit.text().strip() or "/",
            ble_chunk_size=int(self.ble_chunk_spin.value()),
            ble_reboot=bool(self.ble_reboot_check.isChecked()),
            ble_exts=self.ble_exts_edit.text().strip() or ",".join(sorted(DEFAULT_EXTS)),
        ))

    def set_source_root_from_project(self, project_root: str):
        project_root = (project_root or '').strip()
        if not project_root:
            return
        self.source_root.setText(project_root)
        self.append_log(f"Selected project for deploy: {project_root}")

    def deploy_project_from_editor(self, project_root: str):
        self.open_deploy_config_dialog(project_root)

    def open_deploy_config_dialog(self, project_root: str):
        self.set_source_root_from_project(project_root)
        dlg = DeployConfigDialog(
            self,
            source_root=project_root,
            current_method=self.deploy_method.currentData() or "serial",
            current_port=self.port_edit.text().strip(),
            current_ble_name=self.ble_name_edit.text().strip(),
            current_ble_addr=self.ble_addr_edit.text().strip(),
            current_ble_dest_root=self.ble_dest_root_edit.text().strip(),
            current_ble_chunk=int(self.ble_chunk_spin.value()),
            current_ble_exts=self.ble_exts_edit.text().strip(),
            current_ble_reboot=bool(self.ble_reboot_check.isChecked()),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_payload:
            self.append_log("Deploy configuration canceled.")
            return
        payload = dlg.result_payload
        method = payload.get("method", "serial")
        idx = self.deploy_method.findData(method)
        if idx >= 0:
            self.deploy_method.setCurrentIndex(idx)
        self.port_edit.setText(payload.get("serial_port", self.port_edit.text().strip() or "AUTO"))
        self.ble_name_edit.setText(payload.get("ble_name", self.ble_name_edit.text().strip()))
        self.ble_addr_edit.setText(payload.get("ble_addr", self.ble_addr_edit.text().strip()))
        self.ble_dest_root_edit.setText(payload.get("ble_dest_root", self.ble_dest_root_edit.text().strip() or "/"))
        self.ble_chunk_spin.setValue(int(payload.get("ble_chunk_size", int(self.ble_chunk_spin.value()))))
        self.ble_exts_edit.setText(payload.get("ble_exts", self.ble_exts_edit.text().strip() or ",".join(sorted(DEFAULT_EXTS))))
        self.ble_reboot_check.setChecked(bool(payload.get("ble_reboot", self.ble_reboot_check.isChecked())))
        self.append_log(f"Deploy confirmed from dialog using method: {method}")
        self.do_deploy()

    def on_done(self, ok: bool, msg: str):
        finished_job = self.worker.job if self.worker is not None else None
        self.set_busy(False)
        self.status_dialog.finish_job(ok, msg)
        if ok:
            self.deploy_progress.setValue(self.deploy_progress.maximum())
            self.deploy_progress_label.setText(msg or "Upload complete.")
            self.append_log(f"✅ {msg}")
            if finished_job is not None and finished_job.kind in {"flash", "deploy"}:
                serial_baud = DEFAULT_SERIAL_MONITOR_BAUD
                self.start_serial_monitor(
                    finished_job.port,
                    baud=serial_baud,
                    duration_s=POST_JOB_SERIAL_CAPTURE_SECONDS,
                )
        else:
            self.deploy_progress_label.setText(msg or "Operation failed.")
            self.append_log(f"❌ {msg}")
            QMessageBox.critical(self, "Operation failed", msg)
        self.worker = None

    def closeEvent(self, event):
        self.stop_serial_monitor()
        if self.worker and self.worker.isRunning():
            try:
                self.worker.quit()
                self.worker.wait(1000)
            except Exception:
                pass
        super().closeEvent(event)



# ============================================================
# Tab 2: BLE Scanner + Motor Test + Packet Monitor
# ============================================================
class BleTeleopTab(QWidget):
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.local_bt = QBluetoothLocalDevice()
        self.agent = QBluetoothDeviceDiscoveryAgent(self)

        self.agent.deviceDiscovered.connect(self.on_device_discovered)
        self.agent.finished.connect(self.on_scan_finished)
        self.agent.canceled.connect(self.on_scan_finished)
        self.agent.errorOccurred.connect(self.on_scan_error)

        self.seen = {}
        self.controller: QLowEnergyController | None = None
        self.nus_service: QLowEnergyService | None = None

        self.NUS_SERVICE_UUID = QBluetoothUuid("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        self.NUS_TX_UUID = QBluetoothUuid("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
        self.NUS_RX_UUID = QBluetoothUuid("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
        self.CCCD_UUID = QBluetoothUuid(QBluetoothUuid.DescriptorType.ClientCharacteristicConfiguration)

        self.tx_char = None
        self.rx_char = None

        self.packet_count = 0
        self.selected_motor_port = 1
        self.motor_test_power = 25
        self.motor_test_ms = 300

        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        title = QLabel("BLE Scanner + Motor Test (ZebraBot)")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")

        self.status = QLabel("Ready.")
        self.status.setWordWrap(True)

        scan_row = QHBoxLayout()
        self.btn_scan = QPushButton("Scan")
        self.btn_stop = QPushButton("Stop")
        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")

        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_stop.clicked.connect(self.stop_scan)
        self.btn_connect.clicked.connect(self.connect_selected)
        self.btn_disconnect.clicked.connect(self.disconnect)

        self.btn_stop.setEnabled(False)
        self.btn_disconnect.setEnabled(False)

        scan_row.addWidget(self.btn_scan)
        scan_row.addWidget(self.btn_stop)
        scan_row.addWidget(self.btn_connect)
        scan_row.addWidget(self.btn_disconnect)

        self.devices = QListWidget()

        imu_box = QGroupBox("Live IMU Telemetry")
        imu_layout = QGridLayout(imu_box)

        self.lbl_ax = QLabel("ax: --")
        self.lbl_ay = QLabel("ay: --")
        self.lbl_az = QLabel("az: --")
        self.lbl_gx = QLabel("gx: --")
        self.lbl_gy = QLabel("gy: --")
        self.lbl_gz = QLabel("gz: --")
        self.lbl_temp = QLabel("temp: --")
        self.lbl_packets = QLabel("packets: 0")

        imu_layout.addWidget(self.lbl_ax, 0, 0)
        imu_layout.addWidget(self.lbl_ay, 0, 1)
        imu_layout.addWidget(self.lbl_az, 0, 2)
        imu_layout.addWidget(self.lbl_gx, 1, 0)
        imu_layout.addWidget(self.lbl_gy, 1, 1)
        imu_layout.addWidget(self.lbl_gz, 1, 2)
        imu_layout.addWidget(self.lbl_temp, 2, 0)
        imu_layout.addWidget(self.lbl_packets, 2, 1)

        self.sensor_box = QGroupBox("Live Sensor Dashboard")
        sensor_outer = QVBoxLayout(self.sensor_box)

        sensor_help = QLabel(
            "Only connected and identified sensors are shown below. Unknown or unidentified ports remain hidden. Color sensors render a live swatch and TOF sensors show the latest distance reading."
        )
        sensor_help.setWordWrap(True)
        sensor_outer.addWidget(sensor_help)

        self.sensor_rows_fallback = None
        if QWebEngineView is not None:
            self.sensor_web = QWebEngineView()
            self.sensor_web.setMinimumHeight(240)
            sensor_outer.addWidget(self.sensor_web, 1)
        else:
            self.sensor_web = None
            self.sensor_rows_fallback = QTextEdit()
            self.sensor_rows_fallback.setReadOnly(True)
            self.sensor_rows_fallback.setMinimumHeight(240)
            sensor_outer.addWidget(self.sensor_rows_fallback, 1)

        self.sensor_state = {
            port: {
                "status": "unknown",
                "i2c": "--",
                "kind": "",
                "value": "--",
                "rgb": (32, 32, 32),
                "clear": None,
                "last_update": 0.0,
                "connected": False,
            }
            for port in range(1, 7)
        }

        motor_box = QGroupBox("Motor Ports")
        motor_layout = QGridLayout(motor_box)

        self.motor_type_labels = {}
        self.motor_encoder_labels = {}
        self.motor_scan_power_labels = {}
        self.motor_scan_ticks_labels = {}
        self.motor_position_labels = {}

        motor_layout.addWidget(QLabel("Port"), 0, 0)
        motor_layout.addWidget(QLabel("Type"), 0, 1)
        motor_layout.addWidget(QLabel("Encoder"), 0, 2)
        motor_layout.addWidget(QLabel("Scan Pwr"), 0, 3)
        motor_layout.addWidget(QLabel("Scan Ticks"), 0, 4)
        motor_layout.addWidget(QLabel("Position"), 0, 5)

        for port in range(1, 7):
            lbl_port = QLabel(str(port))
            lbl_type = QLabel("unknown")
            lbl_enc = QLabel("--")
            lbl_scan_power = QLabel("--")
            lbl_scan_ticks = QLabel("--")
            lbl_pos = QLabel("--")

            self.motor_type_labels[port] = lbl_type
            self.motor_encoder_labels[port] = lbl_enc
            self.motor_scan_power_labels[port] = lbl_scan_power
            self.motor_scan_ticks_labels[port] = lbl_scan_ticks
            self.motor_position_labels[port] = lbl_pos

            row = port
            motor_layout.addWidget(lbl_port, row, 0)
            motor_layout.addWidget(lbl_type, row, 1)
            motor_layout.addWidget(lbl_enc, row, 2)
            motor_layout.addWidget(lbl_scan_power, row, 3)
            motor_layout.addWidget(lbl_scan_ticks, row, 4)
            motor_layout.addWidget(lbl_pos, row, 5)

        motor_btn_row = QHBoxLayout()
        self.btn_motor_scan_on = QPushButton("Motor Scan ON")
        self.btn_motor_scan_off = QPushButton("Motor Scan OFF")
        self.btn_motor_fb_on = QPushButton("Motor FB ON")
        self.btn_motor_fb_off = QPushButton("Motor FB OFF")
        self.btn_motor_cfg = QPushButton("Motor Config")
        self.btn_motor_state = QPushButton("Motor State")

        self.btn_motor_scan_on.clicked.connect(lambda: self._write_line("MTR_SCAN ON"))
        self.btn_motor_scan_off.clicked.connect(lambda: self._write_line("MTR_SCAN OFF"))
        self.btn_motor_fb_on.clicked.connect(lambda: self._write_line("MTR_FB ON"))
        self.btn_motor_fb_off.clicked.connect(lambda: self._write_line("MTR_FB OFF"))
        self.btn_motor_cfg.clicked.connect(lambda: self._write_line("MTR_CFG"))
        self.btn_motor_state.clicked.connect(lambda: self._write_line("MTR_STATE"))

        motor_btn_row.addWidget(self.btn_motor_scan_on)
        motor_btn_row.addWidget(self.btn_motor_scan_off)
        motor_btn_row.addWidget(self.btn_motor_fb_on)
        motor_btn_row.addWidget(self.btn_motor_fb_off)
        motor_btn_row.addWidget(self.btn_motor_cfg)
        motor_btn_row.addWidget(self.btn_motor_state)

        motor_test_box = QGroupBox("Individual Motor Test")
        motor_test_layout = QGridLayout(motor_test_box)

        self.motor_port_combo = QComboBox()
        self.motor_port_combo.addItem("M1 / Port 1", 1)
        self.motor_port_combo.addItem("M2 / Port 2", 2)
        self.motor_port_combo.addItem("M3 / Port 3", 3)
        self.motor_port_combo.addItem("M4 / Port 4", 4)
        self.motor_port_combo.currentIndexChanged.connect(self.on_motor_port_changed)

        self.s_motor_power = self._make_slider(-100, 100, 25)
        self.s_motor_power.valueChanged.connect(self.on_motor_power_changed)
        self.lbl_motor_power = QLabel("Motor Power: 25")

        self.motor_test_ms_spin = QSpinBox()
        self.motor_test_ms_spin.setRange(50, 5000)
        self.motor_test_ms_spin.setValue(300)
        self.motor_test_ms_spin.valueChanged.connect(self.on_motor_ms_changed)

        self.btn_motor_test = QPushButton("Run Test")
        self.btn_motor_test.clicked.connect(self.run_selected_motor_test)

        self.btn_motor_stop = QPushButton("Stop Test")
        self.btn_motor_stop.clicked.connect(self.stop_selected_motor)

        motor_test_layout.addWidget(QLabel("Motor:"), 0, 0)
        motor_test_layout.addWidget(self.motor_port_combo, 0, 1)
        motor_test_layout.addWidget(self.lbl_motor_power, 1, 0, 1, 2)
        motor_test_layout.addWidget(self.s_motor_power, 2, 0, 1, 2)
        motor_test_layout.addWidget(QLabel("Pulse Duration (ms):"), 3, 0)
        motor_test_layout.addWidget(self.motor_test_ms_spin, 3, 1)
        motor_test_layout.addWidget(self.btn_motor_test, 4, 0)
        motor_test_layout.addWidget(self.btn_motor_stop, 4, 1)

        help_text = QLabel(
            "Use the individual motor test controls to probe M1-M4.\n"
            "Commands used: MTEST <port> <power> <ms>, MSTOP <port>, MTR_CFG, MTR_STATE."
        )
        help_text.setWordWrap(True)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("font-family: Consolas, monospace;")
        self.log.connect(self.log_box.append)

        root.addWidget(title)
        root.addWidget(self.status)
        root.addLayout(scan_row)
        root.addWidget(self.devices, 1)
        root.addWidget(imu_box)
        root.addWidget(self.sensor_box)
        root.addWidget(motor_box)
        root.addLayout(motor_btn_row)
        root.addWidget(motor_test_box)
        root.addWidget(help_text)
        root.addWidget(QLabel("BLE Log:"))
        root.addWidget(self.log_box, 1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.sensor_timer = QTimer(self)
        self.sensor_timer.setInterval(500)
        self.sensor_timer.timeout.connect(self._refresh_sensor_dashboard)
        self.sensor_timer.start()
        self._refresh_sensor_dashboard()

        if not self.local_bt.isValid():
            self.status.setText("No usable local Bluetooth adapter found by Qt.")
            self.btn_scan.setEnabled(False)

    def _make_slider(self, mn, mx, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(mn, mx)
        s.setValue(val)
        return s

    def _sensor_card_html(self, port: int, state: dict) -> str:
        status = html.escape(state.get("status", "unknown"))
        i2c = html.escape(state.get("i2c", "--"))
        kind = html.escape(state.get("kind") or "sensor")
        value = html.escape(state.get("value", "--"))
        rgb = state.get("rgb", (32, 32, 32))
        if not isinstance(rgb, tuple) or len(rgb) != 3:
            rgb = (32, 32, 32)
        rgb = tuple(max(0, min(255, int(v))) for v in rgb)
        active = (time.time() - float(state.get("last_update", 0.0))) < 2.0
        dot_class = "dot active" if active else "dot stale"
        active_text = "reporting" if active else "idle"

        extra = f"<div class='kv'><span>Value</span><strong>{value}</strong></div>"
        if state.get("kind") == "color":
            clear_val = state.get("clear")
            clear_txt = f"{int(clear_val)}" if clear_val is not None else "--"
            extra += (
                f"<div class='color-row'>"
                f"<div class='color-box' style='background: rgb({rgb[0]}, {rgb[1]}, {rgb[2]});'></div>"
                f"<div class='kv-stack'><div class='kv'><span>RGB</span><strong>{rgb[0]}, {rgb[1]}, {rgb[2]}</strong></div>"
                f"<div class='kv'><span>Clear</span><strong>{clear_txt}</strong></div></div></div>"
            )
        elif state.get("kind") == "tof":
            extra += f"<div class='distance'>{value}</div>"

        return (
            f"<div class='card'>"
            f"<div class='head'><div><div class='port'>Port {port}</div><div class='kind'>{kind}</div></div>"
            f"<div class='status'><span class='{dot_class}'></span>{active_text}</div></div>"
            f"<div class='kv'><span>State</span><strong>{status}</strong></div>"
            f"<div class='kv'><span>I2C</span><strong>{i2c}</strong></div>"
            f"{extra}</div>"
        )

    def _sensor_is_displayable(self, state: dict) -> bool:
        if not state.get("connected"):
            return False
        kind = (state.get("kind") or "").strip().lower()
        status = (state.get("status") or "").strip().lower()
        if not kind:
            return False
        if status in {"unknown", "empty", "unidentified"}:
            return False
        return True

    def _refresh_sensor_dashboard(self):
        cards = []
        fallback_lines = []
        visible_count = 0
        for port in range(1, 7):
            state = self.sensor_state[port]
            if not self._sensor_is_displayable(state):
                continue
            visible_count += 1
            cards.append(self._sensor_card_html(port, state))
            fallback_lines.append(
                f"Port {port} | {state.get('kind') or 'sensor'} | {state.get('status')} | {state.get('i2c')} | {state.get('value')}"
            )

        self.sensor_box.setVisible(visible_count > 0)

        if not cards:
            if self.sensor_web is not None:
                self.sensor_web.setHtml("<html><body style='background:#11161d;'></body></html>")
            elif self.sensor_rows_fallback is not None:
                self.sensor_rows_fallback.clear()
            return

        page = f"""
        <html><head><style>
        body {{ background:#11161d; color:#e6edf3; font-family: Arial, sans-serif; margin:0; padding:10px; }}
        .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }}
        .card {{ background:#18212b; border:1px solid #263444; border-radius:12px; padding:12px; box-shadow:0 4px 12px rgba(0,0,0,0.22); }}
        .head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
        .port {{ font-size:16px; font-weight:700; color:#dce7f3; }}
        .kind {{ font-size:12px; color:#8fa6bc; text-transform:uppercase; letter-spacing:0.08em; margin-top:2px; }}
        .status {{ font-size:12px; color:#b8c7d6; display:flex; align-items:center; gap:6px; }}
        .dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
        .dot.active {{ background:#31c46c; box-shadow:0 0 10px rgba(49,196,108,0.7); }}
        .dot.stale {{ background:#7b8794; }}
        .kv, .distance {{ background:#121a22; border-radius:8px; padding:8px 10px; margin-top:8px; }}
        .kv {{ display:flex; justify-content:space-between; gap:10px; }}
        .kv span {{ color:#8fa6bc; }}
        .kv strong, .distance {{ color:#f4f8fb; font-weight:700; }}
        .color-row {{ display:flex; gap:10px; align-items:stretch; margin-top:8px; }}
        .color-box {{ width:70px; min-width:70px; border-radius:10px; border:1px solid #3a4a5a; }}
        .kv-stack {{ flex:1; display:flex; flex-direction:column; gap:8px; }}
        .distance {{ text-align:center; font-size:24px; }}
        .empty {{ border:1px dashed #3a4a5a; border-radius:12px; padding:20px; text-align:center; color:#8fa6bc; }}
        </style></head><body><div class='grid'>{''.join(cards)}</div></body></html>"""
        if self.sensor_web is not None:
            self.sensor_web.setHtml(page)
        elif self.sensor_rows_fallback is not None:
            self.sensor_rows_fallback.setPlainText("\n".join(fallback_lines))

    def _update_sensor_state(self, port: int, **updates):
        if not (1 <= port <= 6):
            return
        state = self.sensor_state[port]
        state.update(updates)
        if updates.get("status") in {"empty", "unknown"}:
            state["connected"] = False
            state["kind"] = ""
            state["value"] = "--"
            state["i2c"] = "--"
            state["last_update"] = 0.0
        elif updates.get("status") == "unidentified":
            state["connected"] = False
            state["kind"] = ""
            state["value"] = "--"
            state["last_update"] = 0.0
        elif updates:
            state["connected"] = True
            state["last_update"] = time.time()
        self._refresh_sensor_dashboard()

    def _reset_sensor_rows(self):
        for port in range(1, 7):
            self.sensor_state[port] = {
                "status": "unknown",
                "i2c": "--",
                "kind": "",
                "value": "--",
                "rgb": (32, 32, 32),
                "clear": None,
                "last_update": 0.0,
                "connected": False,
            }
        self._refresh_sensor_dashboard()

    def _reset_motor_rows(self):
        for port in range(1, 7):
            self.motor_type_labels[port].setText("unknown")
            self.motor_encoder_labels[port].setText("--")
            self.motor_scan_power_labels[port].setText("--")
            self.motor_scan_ticks_labels[port].setText("--")
            self.motor_position_labels[port].setText("--")

    def start_scan(self):
        self.devices.clear()
        self.seen.clear()
        self.packet_count = 0
        self._reset_sensor_rows()
        self._reset_motor_rows()
        self.lbl_packets.setText("packets: 0")
        self.status.setText("Scanning for ZebraBot...")
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_connect.setEnabled(False)
        try:
            self.agent.start()
        except Exception as e:
            self.status.setText(f"Scan start failed: {e}")
            self.btn_scan.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def stop_scan(self):
        if self.agent.isActive():
            self.agent.stop()

    def on_device_discovered(self, info: QBluetoothDeviceInfo):
        name = info.name() or ""
        if "zebrabot" not in name.lower():
            return

        try:
            addr = info.address().toString()
        except Exception:
            addr = "(no address)"

        rssi = info.rssi()
        core = info.coreConfigurations()
        is_ble = bool(core & QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration)

        key = (name, addr)
        if key in self.seen:
            return
        self.seen[key] = True

        item = QListWidgetItem(f"{name} | {addr} | RSSI {rssi} | BLE {'Yes' if is_ble else 'No/Unknown'}")
        item.setData(Qt.ItemDataRole.UserRole, info)
        self.devices.addItem(item)

        self.status.setText(f"Found {self.devices.count()} ZebraBot device(s)...")
        self.btn_connect.setEnabled(True)

    def on_scan_finished(self):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.devices.count() == 0:
            self.status.setText("Scan finished. No ZebraBot found.")
            self.btn_connect.setEnabled(False)
        else:
            self.status.setText("Scan finished. Select a device and click Connect.")
            self.btn_connect.setEnabled(True)

    def on_scan_error(self, error):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status.setText(f"Scan error: {error}")

    def connect_selected(self):
        item = self.devices.currentItem()
        if not item:
            QMessageBox.information(self, "Select a device", "Select a ZebraBot device from the list first.")
            return

        info = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(info, QBluetoothDeviceInfo):
            QMessageBox.critical(self, "Internal error", "Selected device info not available.")
            return

        self.log.emit("Connecting...")
        self.status.setText("Connecting...")

        self.disconnect()

        self.controller = QLowEnergyController.createCentral(info, self)
        self.controller.connected.connect(self._on_connected)
        self.controller.disconnected.connect(self._on_disconnected)
        self.controller.errorOccurred.connect(self._on_ctrl_error)
        self.controller.serviceDiscovered.connect(self._on_service_discovered)
        self.controller.discoveryFinished.connect(self._on_service_scan_finished)

        self.controller.connectToDevice()

    def disconnect(self):
        try:
            if self.nus_service:
                self.nus_service.deleteLater()
        except Exception:
            pass
        self.nus_service = None
        self.tx_char = None
        self.rx_char = None

        try:
            if self.controller:
                self.controller.disconnectFromDevice()
                self.controller.deleteLater()
        except Exception:
            pass
        self.controller = None

        self.btn_disconnect.setEnabled(False)

    def _on_connected(self):
        self.log.emit("Connected. Discovering services...")
        self.status.setText("Connected. Discovering services...")
        if self.controller:
            self.controller.discoverServices()
        self.btn_disconnect.setEnabled(True)

    def _on_disconnected(self):
        self.log.emit("Disconnected.")
        self.status.setText("Disconnected.")
        self.btn_disconnect.setEnabled(False)
        self.nus_service = None
        self.tx_char = None
        self.rx_char = None

    def _on_ctrl_error(self, err):
        self.log.emit(f"Controller error: {err}")
        self.status.setText(f"BLE error: {err}")

    def _on_service_discovered(self, uuid):
        if uuid == self.NUS_SERVICE_UUID:
            self.log.emit("Found NUS service.")

    def _on_service_scan_finished(self):
        if not self.controller:
            return

        self.log.emit("Service discovery finished. Creating NUS service object...")
        svc = self.controller.createServiceObject(self.NUS_SERVICE_UUID, self)
        if svc is None:
            self.log.emit("NUS service not found. (Robot may not be running teleop firmware.)")
            self.status.setText("NUS service not found. Is the robot running the teleop firmware?")
            return

        self.nus_service = svc
        self.nus_service.stateChanged.connect(self._on_svc_state)
        self.nus_service.characteristicChanged.connect(self._on_char_changed)
        self.nus_service.characteristicWritten.connect(self._on_char_written)
        self.nus_service.errorOccurred.connect(self._on_svc_error)
        self.nus_service.descriptorWritten.connect(self._on_desc_written)
        self.nus_service.discoverDetails()

    def _on_svc_state(self, state):
        if state == QLowEnergyService.ServiceState.ServiceDiscovered:
            self.log.emit("NUS service discovered. Locating TX/RX characteristics...")
            self.status.setText("Connected to ZebraBot (NUS ready).")

            self.tx_char = self.nus_service.characteristic(self.NUS_TX_UUID)
            self.rx_char = self.nus_service.characteristic(self.NUS_RX_UUID)

            if not self.tx_char.isValid():
                self.log.emit("TX notify characteristic not found.")
            else:
                self.log.emit("TX notify characteristic found.")

            if not self.rx_char.isValid():
                self.log.emit("RX write characteristic not found.")
                self.status.setText("RX characteristic not found.")
                return
            else:
                self.log.emit("RX write characteristic found.")

            if self.tx_char and self.tx_char.isValid():
                desc = self.tx_char.descriptor(self.CCCD_UUID)
                if desc.isValid():
                    self.log.emit("Enabling TX notifications...")
                    self.nus_service.writeDescriptor(desc, bytes([0x01, 0x00]))
                else:
                    self.log.emit("CCCD not found on TX characteristic.")

            self._write_line("MTR_CFG")
            self._write_line("MTR_STATE")

    def _on_svc_error(self, err):
        self.log.emit(f"Service error: {err}")

    def _on_desc_written(self, desc, value):
        try:
            v = bytes(value).hex()
        except Exception:
            v = repr(value)
        self.log.emit(f"Descriptor written: {desc.uuid().toString()} = {v}")

    def _on_char_written(self, ch, value):
        try:
            txt = bytes(value).decode(errors="ignore").strip()
        except Exception:
            txt = repr(bytes(value))
        self.log.emit(f"TX-> {txt}")

    def _on_char_changed(self, ch, value):
        try:
            text = bytes(value).decode(errors="ignore").strip()
        except Exception:
            text = repr(bytes(value))

        if not text:
            return

        self.packet_count += 1
        self.lbl_packets.setText(f"packets: {self.packet_count}")
        self.log.emit(f"RX<- {text}")

        for line in text.splitlines():
            self._parse_packet(line.strip())

    def _parse_packet(self, line: str):
        if not line:
            return

        parts = line.split()
        if not parts:
            return

        if parts[0] == "IMU" and len(parts) >= 8:
            try:
                ax = float(parts[1])
                ay = float(parts[2])
                az = float(parts[3])
                gx = float(parts[4])
                gy = float(parts[5])
                gz = float(parts[6])
                temp = float(parts[7])

                self.lbl_ax.setText(f"ax: {ax:.3f} g")
                self.lbl_ay.setText(f"ay: {ay:.3f} g")
                self.lbl_az.setText(f"az: {az:.3f} g")
                self.lbl_gx.setText(f"gx: {gx:.3f} dps")
                self.lbl_gy.setText(f"gy: {gy:.3f} dps")
                self.lbl_gz.setText(f"gz: {gz:.3f} dps")
                self.lbl_temp.setText(f"temp: {temp:.2f} C")
            except Exception as e:
                self.log.emit(f"IMU parse error: {e}")
            return

        if parts[0] == "SNS" and len(parts) >= 3:
            try:
                port = int(parts[1])
                state = parts[2]
                self._update_sensor_state(port, status=state)
            except Exception as e:
                self.log.emit(f"SNS parse error: {e}")
            return

        if parts[0] == "SNS_I2C" and len(parts) >= 3:
            try:
                port = int(parts[1])
                addrs = " ".join(parts[2:])
                self._update_sensor_state(port, i2c=addrs, connected=True)
            except Exception as e:
                self.log.emit(f"SNS_I2C parse error: {e}")
            return

        if parts[0] == "SNS_TOF" and len(parts) >= 3:
            try:
                port = int(parts[1])
                dist_mm = int(parts[2])
                self._update_sensor_state(
                    port,
                    status="ok",
                    kind="tof",
                    value=f"{dist_mm} mm",
                    connected=True,
                )
            except Exception as e:
                self.log.emit(f"SNS_TOF parse error: {e}")
            return

        if parts[0] == "SNS_COLOR" and len(parts) >= 6:
            try:
                port = int(parts[1])
                r = int(parts[2])
                g = int(parts[3])
                b = int(parts[4])
                c = int(parts[5])
                self._update_sensor_state(
                    port,
                    status="ok",
                    kind="color",
                    value=f"R{r} G{g} B{b}",
                    rgb=(r, g, b),
                    clear=c,
                    connected=True,
                )
            except Exception as e:
                self.log.emit(f"SNS_COLOR parse error: {e}")
            return

        if parts[0] == "SNS_ERR" and len(parts) >= 3:
            try:
                port = int(parts[1])
                msg = " ".join(parts[2:])
                self._update_sensor_state(port, status="error", kind="sensor", value=msg, connected=True)
            except Exception as e:
                self.log.emit(f"SNS_ERR parse error: {e}")
            return

        if parts[0] == "MTR_CFG" and len(parts) >= 6:
            try:
                port = int(parts[1])
                motor_name = parts[2]
                pwm = parts[3]
                direc = parts[4]
                enc = parts[5]
                if 1 <= port <= 6:
                    self.motor_type_labels[port].setText(motor_name)
                    self.motor_position_labels[port].setText(f"{pwm} {direc} {enc}")
            except Exception as e:
                self.log.emit(f"MTR_CFG parse error: {e}")
            return

        if parts[0] == "MTR_SCAN" and len(parts) >= 4:
            try:
                port = int(parts[1])
                scan_power = parts[2]
                scan_ticks = parts[3]
                if 1 <= port <= 6:
                    self.motor_scan_power_labels[port].setText(str(scan_power))
                    self.motor_scan_ticks_labels[port].setText(str(scan_ticks))
                    try:
                        ticks_i = int(scan_ticks)
                        if ticks_i > 0 and self.motor_type_labels[port].text() == "unknown":
                            self.motor_type_labels[port].setText("motor+encoder?")
                    except Exception:
                        pass
                    if len(parts) >= 5:
                        self.motor_type_labels[port].setText(parts[4])
                    if len(parts) >= 6:
                        self.motor_position_labels[port].setText(parts[5])
            except Exception as e:
                self.log.emit(f"MTR_SCAN parse error: {e}")
            return

        if parts[0] == "MTR_FB":
            try:
                if len(parts) >= 4:
                    port = int(parts[1])
                    motor_name = parts[2]
                    ticks = parts[3]
                    if 1 <= port <= 6:
                        self.motor_type_labels[port].setText(str(motor_name))
                        self.motor_encoder_labels[port].setText(str(ticks))
                    return
                elif len(parts) >= 3:
                    port = int(parts[1])
                    ticks = parts[2]
                    if 1 <= port <= 6:
                        self.motor_encoder_labels[port].setText(str(ticks))
                    return
            except Exception as e:
                self.log.emit(f"MTR_FB parse error: {e}")
            return

        if parts[0] == "MTR_TYPE" and len(parts) >= 3:
            try:
                port = int(parts[1])
                motor_type = " ".join(parts[2:])
                if 1 <= port <= 6:
                    self.motor_type_labels[port].setText(motor_type)
            except Exception as e:
                self.log.emit(f"MTR_TYPE parse error: {e}")
            return

        if parts[0] == "MTR_POS" and len(parts) >= 3:
            try:
                port = int(parts[1])
                pos = " ".join(parts[2:])
                if 1 <= port <= 6:
                    self.motor_position_labels[port].setText(pos)
            except Exception as e:
                self.log.emit(f"MTR_POS parse error: {e}")
            return

        if parts[0] == "MTR_INFO" and len(parts) >= 5:
            try:
                port = int(parts[1])
                motor_type = parts[2]
                enc_ticks = parts[3]
                pos = parts[4]
                if 1 <= port <= 6:
                    self.motor_type_labels[port].setText(motor_type)
                    self.motor_encoder_labels[port].setText(enc_ticks)
                    self.motor_position_labels[port].setText(pos)
            except Exception as e:
                self.log.emit(f"MTR_INFO parse error: {e}")
            return

        if parts[0] == "MTR_ERR" and len(parts) >= 3:
            try:
                port = int(parts[1])
                msg = " ".join(parts[2:])
                if 1 <= port <= 6:
                    self.motor_type_labels[port].setText("error")
                    self.motor_position_labels[port].setText(msg)
            except Exception as e:
                self.log.emit(f"MTR_ERR parse error: {e}")
            return

        if parts[0] == "INFO":
            self.log.emit("[ROBOT] " + line[5:])
            return

        if parts[0] == "ERR":
            self.log.emit("[ROBOT ERROR] " + line[4:])
            return

        if parts[0] == "IMU_ERR":
            self.log.emit("[ROBOT IMU ERROR] " + " ".join(parts[1:]))
            return

        self.log.emit("[ROBOT RAW] " + line)

    def _write_line(self, line: str):
        if not self.nus_service or not self.rx_char or not self.rx_char.isValid():
            self.log.emit("Write skipped: RX characteristic not ready.")
            return
        payload = (line.strip() + "\n").encode()
        self.nus_service.writeCharacteristic(
            self.rx_char,
            payload,
            QLowEnergyService.WriteMode.WriteWithoutResponse
        )

    def on_motor_port_changed(self, _idx):
        self.selected_motor_port = int(self.motor_port_combo.currentData())

    def on_motor_power_changed(self, v):
        self.motor_test_power = int(v)
        self.lbl_motor_power.setText(f"Motor Power: {self.motor_test_power}")

    def on_motor_ms_changed(self, v):
        self.motor_test_ms = int(v)

    def run_selected_motor_test(self):
        port = int(self.selected_motor_port)
        power = int(self.motor_test_power)
        dur = int(self.motor_test_ms)
        self._write_line(f"MTEST {port} {power} {dur}")
        self.log.emit(f"TX: MTEST {port} {power} {dur}")

    def stop_selected_motor(self):
        port = int(self.selected_motor_port)
        self._write_line(f"MSTOP {port}")
        self.log.emit(f"TX: MSTOP {port}")



# ============================================================
# Tab 3: Local Project Editor
# ============================================================
class MonacoBridge(QObject):
    dirtyChanged = pyqtSignal(bool)
    statusMessage = pyqtSignal(str)
    contentChanged = pyqtSignal(str)
    readyChanged = pyqtSignal(bool)

    @pyqtSlot(bool)
    def notifyDirty(self, dirty: bool):
        self.dirtyChanged.emit(bool(dirty))

    @pyqtSlot(str)
    def postStatus(self, message: str):
        self.statusMessage.emit(str(message))

    @pyqtSlot(str)
    def updateContent(self, text: str):
        self.contentChanged.emit(str(text))

    @pyqtSlot(bool)
    def editorReady(self, ready: bool):
        self.readyChanged.emit(bool(ready))


class ProjectEditorTab(QWidget):
    deployRequested = pyqtSignal(str)
    configureDeployRequested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.base_dir = projects_root_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.current_project_dir: Path | None = None
        self.external_project_dir: Path | None = None
        self.current_file_path: Path | None = None
        self.editor_dirty = False
        self._latest_editor_text = ""
        self._editor_ready = False
        self._pending_markers: list[dict] = []
        self._diag_timer = QTimer(self)
        self._diag_timer.setSingleShot(True)
        self._diag_timer.timeout.connect(self.run_python_diagnostics)
        self._diag_debounce_ms = 180
        self._pyright_timer = QTimer(self)
        self._pyright_timer.setSingleShot(True)
        self._pyright_timer.timeout.connect(self.run_pyright_diagnostics)
        self._pyright_debounce_ms = 900
        self._build()
        self.refresh_projects(select_first=True)

    def _build(self):
        root = QVBoxLayout(self)

        help_text = QLabel(
            "Create and manage local MicroPython projects, edit files in Monaco, and save code before deploying. Use 'Use for Deploy' to mark the current project in the Flash + Deploy tab, or use 'Configure + Deploy…' to review the method and target device before the upload starts. Projects are stored in your local app data folder under ZebraTeleopFlasher/projects."
        )
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        toolbar = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.on_project_changed)
        self.btn_refresh_projects = QPushButton("Refresh")
        self.btn_refresh_projects.clicked.connect(self.refresh_projects)
        self.btn_new_project = QPushButton("New Project")
        self.btn_new_project.clicked.connect(self.create_project)
        self.btn_open_project_folder = QPushButton("Open Project Folder…")
        self.btn_open_project_folder.clicked.connect(self.open_project_folder_dialog)
        self.btn_close_project = QPushButton("Close Project")
        self.btn_close_project.clicked.connect(self.close_project)
        self.btn_delete_project = QPushButton("Delete Project")
        self.btn_delete_project.clicked.connect(self.delete_project)
        self.btn_use_for_deploy = QPushButton("Use for Deploy")
        self.btn_use_for_deploy.clicked.connect(self.use_current_project_for_deploy)
        self.btn_deploy_project = QPushButton("Configure + Deploy…")
        self.btn_deploy_project.clicked.connect(self.deploy_current_project)

        toolbar.addWidget(QLabel("Project:"))
        toolbar.addWidget(self.project_combo, 1)
        toolbar.addWidget(self.btn_refresh_projects)
        toolbar.addWidget(self.btn_new_project)
        toolbar.addWidget(self.btn_open_project_folder)
        toolbar.addWidget(self.btn_close_project)
        toolbar.addWidget(self.btn_delete_project)
        toolbar.addWidget(self.btn_use_for_deploy)
        toolbar.addWidget(self.btn_deploy_project)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_project_path = QLabel("Project path: --")
        self.lbl_project_path.setWordWrap(True)
        left_layout.addWidget(self.lbl_project_path)

        file_actions = QHBoxLayout()
        self.btn_new_file = QPushButton("New File")
        self.btn_new_file.clicked.connect(self.create_file)
        self.btn_new_folder = QPushButton("New Folder")
        self.btn_new_folder.clicked.connect(self.create_folder)
        self.btn_rename = QPushButton("Rename")
        self.btn_rename.clicked.connect(self.rename_selected_path)
        self.btn_delete_path = QPushButton("Delete")
        self.btn_delete_path.clicked.connect(self.delete_selected_path)
        file_actions.addWidget(self.btn_new_file)
        file_actions.addWidget(self.btn_new_folder)
        file_actions.addWidget(self.btn_rename)
        file_actions.addWidget(self.btn_delete_path)
        left_layout.addLayout(file_actions)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Project Files"])
        self.file_tree.setColumnCount(1)
        self.file_tree.itemDoubleClicked.connect(self.on_file_double_clicked)
        self.file_tree.itemClicked.connect(self.on_file_tree_clicked)
        self.file_tree.setAlternatingRowColors(True)
        left_layout.addWidget(self.file_tree, 1)

        self.project_log = QTextEdit()
        self.project_log.setReadOnly(True)
        self.project_log.setMaximumHeight(180)
        self.project_log.setStyleSheet("font-family: Consolas, monospace;")
        left_layout.addWidget(QLabel("Project Log:"))
        left_layout.addWidget(self.project_log)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        editor_bar = QHBoxLayout()
        self.lbl_open_file = QLabel("No file open")
        self.lbl_open_file.setWordWrap(True)
        self.lbl_dirty = QLabel("Saved")
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_current_file)
        self.btn_save_as = QPushButton("Save As")
        self.btn_save_as.clicked.connect(self.save_as_current_file)
        editor_bar.addWidget(self.lbl_open_file, 1)
        editor_bar.addWidget(self.lbl_dirty)
        editor_bar.addWidget(self.btn_save)
        editor_bar.addWidget(self.btn_save_as)
        right_layout.addLayout(editor_bar)

        self.editor_fallback = None
        self.editor_web = None
        self.editor_bridge = None
        if QWebEngineView is not None and QWebChannel is not None:
            self.editor_bridge = MonacoBridge()
            self.editor_bridge.dirtyChanged.connect(self.on_editor_dirty_changed)
            self.editor_bridge.statusMessage.connect(self.append_log)
            self.editor_bridge.contentChanged.connect(self.on_editor_content_changed)
            self.editor_bridge.readyChanged.connect(self.on_editor_ready_changed)
            self.editor_web = QWebEngineView()
            channel = QWebChannel(self.editor_web.page())
            channel.registerObject("bridge", self.editor_bridge)
            self.editor_web.page().setWebChannel(channel)
            self.editor_web.setHtml(self._build_monaco_html(), QUrl("https://cdnjs.cloudflare.com/"))
            right_layout.addWidget(self.editor_web, 1)
        else:
            self.editor_fallback = QTextEdit()
            self.editor_fallback.setAcceptRichText(False)
            self.editor_fallback.textChanged.connect(self.on_fallback_text_changed)
            right_layout.addWidget(self.editor_fallback, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 860])
        root.addWidget(splitter, 1)

    def append_log(self, message: str):
        self.project_log.append(str(message))

    def _project_names(self) -> list[str]:
        return sorted([p.name for p in self.base_dir.iterdir() if p.is_dir()])


    def _looks_like_project_dir(self, path: Path) -> bool:
        path = Path(path)
        if not path.exists() or not path.is_dir():
            return False
        return (
            (path / "main.py").is_file()
            or (path / "user_main.py").is_file()
            or (path / "robot").is_dir()
        )

    def _activate_project_dir(self, project_dir: Path, source: str = "library"):
        project_dir = Path(project_dir).resolve()
        self.current_project_dir = project_dir
        self.current_file_path = None
        self.editor_dirty = False
        self.update_dirty_label()
        self.lbl_project_path.setText(f"Project path: {project_dir}")
        self.populate_file_list()

        preferred = None
        for candidate in ("main.py", "user_main.py"):
            p = project_dir / candidate
            if p.exists():
                preferred = p
                break

        if preferred is not None:
            self.load_file(preferred)
        else:
            self.clear_editor()

        self.append_log(f"Opened project ({source}): {project_dir}")

    def close_project(self):
        if self.editor_dirty and not self.maybe_save_changes():
            return
        self.current_project_dir = None
        self.current_file_path = None
        self.external_project_dir = None
        self.file_tree.clear()
        self.clear_editor()
        self.lbl_project_path.setText(f"Project path: {self.base_dir}")
        self.project_combo.blockSignals(True)
        if self.project_combo.count() > 0:
            self.project_combo.setCurrentIndex(-1)
        self.project_combo.blockSignals(False)
        self.append_log("Closed current project.")

    def refresh_projects(self, *_args, select_first: bool = False):
        names = self._project_names()
        current = self.project_combo.currentText().strip()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for name in names:
            self.project_combo.addItem(name)
        self.project_combo.blockSignals(False)

        if self.external_project_dir and self.external_project_dir.exists():
            self.project_combo.blockSignals(True)
            self.project_combo.setCurrentIndex(-1)
            self.project_combo.blockSignals(False)
            self._activate_project_dir(self.external_project_dir, source="external")
            return

        if current in names:
            idx = self.project_combo.findText(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
                return
        elif select_first and names:
            self.project_combo.setCurrentIndex(0)
            return

        self.on_project_changed()

    def on_project_changed(self, *_args):
        name = self.project_combo.currentText().strip()
        if name:
            self.external_project_dir = None
            self._activate_project_dir(self.base_dir / name, source="library")
        else:
            self.current_project_dir = None
            self.current_file_path = None
            self.editor_dirty = False
            self.update_dirty_label()
            self.lbl_project_path.setText(f"Project path: {self.base_dir}")
            self.file_tree.clear()
            self.clear_editor()

    def ensure_project_selected(self) -> bool:
        if self.current_project_dir and self.current_project_dir.exists():
            return True
        QMessageBox.information(self, "No project selected", "Create or select a project first.")
        return False

    def use_current_project_for_deploy(self):
        if not self.ensure_project_selected():
            return
        self.deployRequested.emit(str(self.current_project_dir))
        self.append_log(f"Marked deploy source in Flash + Deploy tab: {self.current_project_dir}")

    def deploy_current_project(self):
        if not self.ensure_project_selected():
            return
        if self.editor_dirty:
            saved = self.save_current_file()
            if not saved:
                return
        self.configureDeployRequested.emit(str(self.current_project_dir))
        self.append_log(f"Opened deploy configuration for: {self.current_project_dir}")


    def create_project(self):
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        project_dir = self.base_dir / safe_name
        if project_dir.exists():
            QMessageBox.warning(self, "Exists", f"Project already exists:\n{project_dir}")
            return

        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "robot").mkdir(parents=True, exist_ok=True)
        init_py = project_dir / "robot" / "__init__.py"
        if not init_py.exists():
            init_py.write_text("# Local robot helpers for desktop authoring.\n", encoding="utf-8")

        (project_dir / "main.py").write_text(
            '''
            import uasyncio as asyncio
            import gc
            from robot.ackermann import AckermannDrive

            async def main(zbot):
                gc.collect()

                # Explicit hardware definition
                drive = AckermannDrive(
                    zbot,
                    drive_motor_port=1,
                    steering_port=2,
                    center_angle=90
                )

                tof = zbot.sensor(1)

                drive.steer_center()

                while True:
                    d = tof.read()

                    if d is None:
                        drive.stop()
                        drive.steer_center()
                        zbot.display("NO SENSOR", "")
                    elif d < 100:
                        drive.stop()
                        drive.steer_center()
                        zbot.display("STOP", str(d))
                    else:
                        drive.forward(60)
                        drive.steer_center()
                        zbot.display("GO", str(d))

                    await asyncio.sleep_ms(50)
            ''',
            encoding="utf-8",
        )

        self.refresh_projects(select_first=False)
        idx = self.project_combo.findText(safe_name)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)
        self.append_log(f"Created student project: {project_dir}")

    def open_project_folder_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a project folder", str(self.current_project_dir or self.base_dir))
        if not path:
            return

        chosen = Path(path).resolve()
        if self._looks_like_project_dir(chosen):
            if self.editor_dirty and not self.maybe_save_changes():
                return
            self.external_project_dir = chosen
            self.project_combo.blockSignals(True)
            self.project_combo.setCurrentIndex(-1)
            self.project_combo.blockSignals(False)
            self._activate_project_dir(chosen, source="external")
            return

        child_projects = [p for p in sorted(chosen.iterdir()) if p.is_dir() and self._looks_like_project_dir(p)]
        if child_projects:
            self.base_dir = chosen
            self.external_project_dir = None
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.append_log(f"Using projects root: {self.base_dir}")
            self.refresh_projects(select_first=True)
            return

        QMessageBox.warning(
            self,
            "Not a project folder",
            "The selected folder does not look like a project and does not contain any child project folders.\n\n"
            "A project folder should contain main.py, user_main.py, or a robot/ folder."
        )

    def delete_project(self):
        if not self.ensure_project_selected():
            return
        if self.external_project_dir and self.current_project_dir and self.current_project_dir.resolve() == self.external_project_dir.resolve():
            QMessageBox.information(self, "Delete Project", "This project was opened from an external folder. Delete it manually from the filesystem if needed.")
            return
        reply = QMessageBox.question(
            self,
            "Delete Project",
            f"Delete project folder?\n{self.current_project_dir}",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        shutil.rmtree(self.current_project_dir, ignore_errors=True)
        self.append_log(f"Deleted project: {self.current_project_dir}")
        self.refresh_projects(select_first=True)

    def relative_files(self) -> list[Path]:
        if not self.current_project_dir or not self.current_project_dir.exists():
            return []
        items = []
        for path in sorted(self.current_project_dir.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.name.startswith("."):
                continue
            items.append(path)
        return items

    def populate_file_list(self):
        self.file_tree.clear()
        if not self.current_project_dir or not self.current_project_dir.exists():
            return

        root = self.current_project_dir.resolve()
        node_map: dict[Path, QTreeWidgetItem] = {}

        def make_item(path: Path) -> QTreeWidgetItem:
            rel = path.relative_to(root).as_posix()
            label = path.name if rel != "." else path.name
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            if path.is_dir():
                item.setToolTip(0, f"Directory: {rel}")
            else:
                item.setToolTip(0, rel)
            node_map[path] = item
            return item

        children = []
        for path in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            children.append(path)

        for path in children:
            item = make_item(path)
            self.file_tree.addTopLevelItem(item)
            if path.is_dir():
                self._populate_tree_children(item, path, root)

        self.file_tree.expandToDepth(0)

    def _populate_tree_children(self, parent_item: QTreeWidgetItem, directory: Path, root: Path):
        children = []
        for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            children.append(path)

        for path in children:
            item = QTreeWidgetItem([path.name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                item.setToolTip(0, f"Directory: {rel}")
            else:
                item.setToolTip(0, rel)
            parent_item.addChild(item)
            if path.is_dir():
                self._populate_tree_children(item, path, root)

    def selected_path(self) -> Path | None:
        item = self.file_tree.currentItem()
        if not item:
            return None
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        return Path(raw) if raw else None

    def create_file(self):
        if not self.ensure_project_selected():
            return
        name, ok = QInputDialog.getText(self, "New File", "Relative file path:", text="robot/project.py")
        if not ok:
            return
        rel = (name or "").strip().replace("\\", "/")
        if not rel:
            return
        dest = (self.current_project_dir / rel).resolve()
        if self.current_project_dir.resolve() not in dest.parents and dest != self.current_project_dir.resolve():
            QMessageBox.warning(self, "Invalid path", "File must stay inside the selected project.")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_text("", encoding="utf-8")
            self.append_log(f"Created file: {dest}")
        self.populate_file_list()
        self.load_file(dest)

    def create_folder(self):
        if not self.ensure_project_selected():
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Relative folder path:", text="robot")
        if not ok:
            return
        rel = (name or "").strip().replace("\\", "/")
        if not rel:
            return
        dest = (self.current_project_dir / rel).resolve()
        if self.current_project_dir.resolve() not in dest.parents and dest != self.current_project_dir.resolve():
            QMessageBox.warning(self, "Invalid path", "Folder must stay inside the selected project.")
            return
        dest.mkdir(parents=True, exist_ok=True)
        self.append_log(f"Created folder: {dest}")
        self.populate_file_list()

    def rename_selected_path(self):
        path = self.selected_path()
        if not path:
            QMessageBox.information(self, "Rename", "Select a file or folder first.")
            return
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=path.name)
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            return
        new_path = path.with_name(new_name)
        if new_path.exists():
            QMessageBox.warning(self, "Exists", f"Path already exists:\n{new_path}")
            return
        path.rename(new_path)
        self.append_log(f"Renamed {path} -> {new_path}")
        self.populate_file_list()
        if self.current_file_path == path:
            self.current_file_path = new_path
            self.lbl_open_file.setText(str(new_path))

    def delete_selected_path(self):
        path = self.selected_path()
        if not path:
            QMessageBox.information(self, "Delete", "Select a file or folder first.")
            return
        reply = QMessageBox.question(self, "Delete", f"Delete this path?\n{path}")
        if reply != QMessageBox.StandardButton.Yes:
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self.append_log(f"Deleted: {path}")
        if self.current_file_path == path:
            self.current_file_path = None
            self.clear_editor()
        self.populate_file_list()

    def on_file_tree_clicked(self, item, _column=0):
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(raw)
        if path.is_dir():
            item.setExpanded(not item.isExpanded())

    def on_file_double_clicked(self, item, _column=0):
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(raw)
        if path.is_dir():
            item.setExpanded(not item.isExpanded())
            return
        if path.is_file():
            self.load_file(path)

    def maybe_save_changes(self) -> bool:
        if not self.editor_dirty:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current file before continuing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            return self.save_current_file()
        return True

    def load_file(self, path: Path):
        if not self.maybe_save_changes():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        self.current_file_path = path
        self.lbl_open_file.setText(str(path))
        self.set_editor_text(text)
        self.editor_dirty = False
        self.update_dirty_label()
        self.set_editor_language_for_path(path)
        self.append_log(f"Opened: {path}")

    def clear_editor(self):
        self.current_file_path = None
        self.lbl_open_file.setText("No file open")
        self.set_editor_text("")
        self.set_editor_language_for_path(None)
        self.editor_dirty = False
        self.update_dirty_label()

    def set_editor_text(self, text: str):
        self._latest_editor_text = text or ""
        if self.editor_web is not None:
            payload = json.dumps(text)
            self.editor_web.page().runJavaScript(f"window.setEditorValue({payload});")
        elif self.editor_fallback is not None:
            self.editor_fallback.blockSignals(True)
            self.editor_fallback.setPlainText(text)
            self.editor_fallback.blockSignals(False)
            self.apply_pygments_fallback(self._latest_editor_text)

    def get_editor_text(self, callback):
        if self.editor_web is not None:
            self.editor_web.page().runJavaScript("window.getEditorValue();", callback)
        elif self.editor_fallback is not None:
            callback(self.editor_fallback.toPlainText())
        else:
            callback("")

    def on_fallback_text_changed(self):
        if self.editor_fallback is None:
            return
        self._latest_editor_text = self.editor_fallback.toPlainText()
        self.on_editor_dirty_changed(True)
        self.schedule_python_diagnostics()

    def on_editor_content_changed(self, text: str):
        self._latest_editor_text = text or ""
        self.schedule_python_diagnostics()

    def on_editor_ready_changed(self, ready: bool):
        self._editor_ready = bool(ready)
        self.append_log(f"Monaco ready state: {self._editor_ready}")
        if self._editor_ready:
            self.set_editor_language_for_path(self.current_file_path)
            self.set_editor_text(self._latest_editor_text)
            self.apply_editor_markers(self._pending_markers)
            self.schedule_python_diagnostics()

    def schedule_python_diagnostics(self):
        language = self.current_language()
        if language != "python":
            self.apply_editor_markers([])
            return
        self._diag_timer.start(self._diag_debounce_ms)

    def current_language(self) -> str:
        if self.current_file_path is None:
            return "plaintext"
        suffix = self.current_file_path.suffix.lower()
        if suffix in {'.py', '.mpy', '.pyi'}:
            return 'python'
        if suffix in {'.json'}:
            return 'json'
        if suffix in {'.md'}:
            return 'markdown'
        if suffix in {'.html', '.htm'}:
            return 'html'
        if suffix in {'.css'}:
            return 'css'
        if suffix in {'.js', '.mjs', '.cjs'}:
            return 'javascript'
        if suffix in {'.ts'}:
            return 'typescript'
        if suffix in {'.xml'}:
            return 'xml'
        if suffix in {'.yml', '.yaml'}:
            return 'yaml'
        if suffix in {'.ini', '.cfg', '.toml'}:
            return 'ini'
        return 'plaintext'

    def run_python_diagnostics(self):
        if self.current_language() != 'python':
            self.apply_editor_markers([])
            return
        text = self._latest_editor_text
        if not text and self.current_file_path and self.current_file_path.exists():
            try:
                text = self.current_file_path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                text = ''
        markers = self._compile_syntax_markers(text)
        self.apply_editor_markers(markers)
        if not markers:
            self._pyright_timer.start(700)

    def run_pyright_diagnostics(self):
        if self.current_language() != 'python':
            return
        text = self._latest_editor_text
        if not text and self.current_file_path and self.current_file_path.exists():
            try:
                text = self.current_file_path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                text = ''
        if self._compile_syntax_markers(text):
            return
        markers = self._run_pyright(text)
        self.apply_editor_markers(markers)

    def _compile_syntax_markers(self, source: str) -> list[dict]:
        source = source or ''
        markers: list[dict] = []
        try:
            compile(source, '<editor>', 'exec')
            return markers
        except SyntaxError as e:
            line = int(e.lineno or 1)
            col = int(e.offset or 1)
            end_line = int(getattr(e, 'end_lineno', 0) or line)
            end_col = int(getattr(e, 'end_offset', 0) or (col + 1))
            if end_line < line:
                end_line = line
            if end_col <= col:
                end_col = col + 1
            markers.append({
                'startLineNumber': line,
                'startColumn': col,
                'endLineNumber': end_line,
                'endColumn': end_col,
                'message': e.msg or 'Syntax error',
                'severity': 8,
                'source': 'python',
            })
            return markers

    def _run_pyright(self, source: str) -> list[dict]:
        source = source or ''
        markers: list[dict] = []
        with tempfile.TemporaryDirectory(prefix='zebra_pyright_') as td:
            td_path = Path(td)
            test_file = td_path / 'main.py'
            test_file.write_text(source, encoding='utf-8')
            config = td_path / 'pyrightconfig.json'
            config.write_text(json.dumps({
                'typeCheckingMode': 'basic',
                'reportMissingImports': 'none',
                'reportMissingModuleSource': 'none',
                'pythonVersion': '3.11'
            }), encoding='utf-8')
            commands = []
            pyright_bin = shutil.which('pyright')
            if pyright_bin:
                commands.append([pyright_bin, '--outputjson', str(test_file)])
            if shutil.which('npx'):
                commands.append(['npx', '-y', 'pyright', '--outputjson', str(test_file)])
            for cmd in commands:
                try:
                    proc = subprocess.run(cmd, cwd=str(td_path), capture_output=True, text=True, timeout=8)
                    raw = (proc.stdout or '').strip() or (proc.stderr or '').strip()
                    self.append_log(f"Diagnostics command: {' '.join(cmd)} | exit={proc.returncode}")
                    if not raw:
                        continue
                    data = json.loads(raw)
                    for diag in data.get('generalDiagnostics', []):
                        rng = diag.get('range', {})
                        start = rng.get('start', {})
                        end = rng.get('end', {})
                        start_line = int(start.get('line', 0)) + 1
                        start_col = int(start.get('character', 0)) + 1
                        end_line = int(end.get('line', start.get('line', 0))) + 1
                        end_col = max(int(end.get('character', start.get('character', 0))) + 1, start_col + 1)
                        sev_name = str(diag.get('severity', 'error')).lower()
                        sev = 8 if sev_name == 'error' else 4 if sev_name == 'warning' else 2
                        markers.append({
                            'startLineNumber': start_line,
                            'startColumn': start_col,
                            'endLineNumber': end_line,
                            'endColumn': end_col,
                            'message': diag.get('message', 'Python diagnostic'),
                            'severity': sev,
                            'source': 'pyright',
                        })
                    return markers
                except Exception:
                    continue
        return markers

    def apply_editor_markers(self, markers: list[dict]):
        self._pending_markers = list(markers or [])
        payload = json.dumps(self._pending_markers)
        if self.editor_web is not None:
            if self._editor_ready:
                self.editor_web.page().runJavaScript(f"window.applyEditorMarkers({payload});")
            else:
                self.append_log("Queued Monaco markers until editor is ready")
        if self.editor_fallback is not None:
            self.apply_pygments_fallback(self._latest_editor_text or self.editor_fallback.toPlainText())

    def apply_pygments_fallback(self, text: str):
        if self.editor_fallback is None or highlight is None or HtmlFormatter is None:
            return
        language = self.current_language()
        if language == 'plaintext':
            return
        try:
            lexer = get_lexer_by_name(language) if get_lexer_by_name else TextLexer()
        except Exception:
            lexer = TextLexer() if TextLexer else None
        if lexer is None:
            return
        formatter = HtmlFormatter(style='monokai', noclasses=True)
        html_body = highlight(text or '', lexer, formatter)
        self.editor_fallback.blockSignals(True)
        self.editor_fallback.setHtml(html_body)
        self.editor_fallback.blockSignals(False)

    def save_current_file(self) -> bool:
        if self.current_file_path is None:
            return self.save_as_current_file()
        result = {"ok": False}
        def _save(text: str):
            try:
                self.current_file_path.write_text(text or "", encoding="utf-8")
                self.editor_dirty = False
                self.update_dirty_label()
                self.append_log(f"Saved: {self.current_file_path}")
                self.populate_file_list()
                self.run_python_diagnostics()
                result["ok"] = True
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))
        self.get_editor_text(_save)
        return result["ok"]

    def save_as_current_file(self) -> bool:
        if not self.ensure_project_selected():
            return False
        name, ok = QInputDialog.getText(self, "Save As", "Relative file path:", text=(self.current_file_path.relative_to(self.current_project_dir).as_posix() if self.current_file_path and self.current_project_dir and self.current_file_path.exists() and self.current_project_dir in self.current_file_path.parents else "main.py"))
        if not ok:
            return False
        rel = (name or "").strip().replace("\\", "/")
        if not rel:
            return False
        dest = (self.current_project_dir / rel).resolve()
        if self.current_project_dir.resolve() not in dest.parents and dest != self.current_project_dir.resolve():
            QMessageBox.warning(self, "Invalid path", "File must stay inside the selected project.")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.current_file_path = dest
        self.lbl_open_file.setText(str(dest))
        self.set_editor_language_for_path(dest)
        return self.save_current_file()


    def set_editor_language_for_path(self, path: Path | None):
        language = self.current_language() if path else 'plaintext'
        if self.editor_web is not None:
            self.editor_web.page().runJavaScript(f"window.setEditorLanguage({json.dumps(language)});")
        self.append_log(f"Editor language: {language}")
        self.schedule_python_diagnostics()

    def on_editor_dirty_changed(self, dirty: bool):
        self.editor_dirty = bool(dirty)
        self.update_dirty_label()

    def update_dirty_label(self):
        self.lbl_dirty.setText("Modified" if self.editor_dirty else "Saved")

    def _build_monaco_html(self) -> str:
        return r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https://cdnjs.cloudflare.com qrc:; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com qrc:; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; connect-src https://cdnjs.cloudflare.com data: blob:; img-src data: blob: https:; worker-src blob: data:;">
  <style>
    html, body, #editor { margin: 0; padding: 0; width: 100%; height: 100%; background: #0f141b; overflow: hidden; }
    body { font-family: Consolas, monospace; }
    #status { position: absolute; right: 12px; top: 8px; z-index: 10; font-size: 12px; color: #8aa2bf; background: rgba(15, 20, 27, 0.75); padding: 4px 8px; border-radius: 6px; }
  </style>
  <script>
    window.__pendingValue = '';
    window.__pendingLanguage = 'python';
    window.__editorReady = false;
    window.__bridge = null;
    window.__editor = null;
    window.__suppressDirty = false;
    window.__pendingMarkers = [];
    window.__contentPushTimer = null;

    window.setEditorValue = function(value) {
      window.__pendingValue = (value == null) ? '' : String(value);
      if (!window.__editor) return false;
      window.__suppressDirty = true;
      window.__editor.setValue(window.__pendingValue);
      window.__suppressDirty = false;
      if (window.monaco && window.__editor.getModel()) {
        window.monaco.editor.setModelMarkers(window.__editor.getModel(), 'python', window.__pendingMarkers || []);
      }
      if (window.__bridge && window.__bridge.notifyDirty) window.__bridge.notifyDirty(false);
      return true;
    };

    window.setEditorLanguage = function(language) {
      window.__pendingLanguage = language || 'plaintext';
      if (!window.__editor || !window.monaco || !window.__editor.getModel()) return false;
      window.monaco.editor.setModelLanguage(window.__editor.getModel(), window.__pendingLanguage);
      var el = document.getElementById('status');
      if (el) el.textContent = 'Syntax: ' + window.__pendingLanguage;
      return true;
    };

    window.getEditorValue = function() {
      return window.__editor ? window.__editor.getValue() : window.__pendingValue;
    };

    window.isEditorReady = function() {
      return !!window.__editorReady;
    };

    window.applyEditorMarkers = function(markers) {
      window.__pendingMarkers = Array.isArray(markers) ? markers : [];
      if (!window.__editor || !window.monaco || !window.__editor.getModel()) return false;
      window.monaco.editor.setModelMarkers(window.__editor.getModel(), 'python', window.__pendingMarkers);
      return true;
    };
  </script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs/loader.min.js"></script>
</head>
<body>
  <div id="status">Monaco loading...</div>
  <div id="editor"></div>
  <script>
    (function() {
      function setStatus(msg) {
        var el = document.getElementById('status');
        if (el) el.textContent = msg || '';
        if (window.__bridge && window.__bridge.postStatus) window.__bridge.postStatus(msg || '');
      }

      window.MonacoEnvironment = {
        getWorker: function() {
          var source = "self.onmessage=function(){};";
          var blob = new Blob([source], {type: 'text/javascript'});
          return new Worker(URL.createObjectURL(blob));
        }
      };

      function startMonaco() {
        require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs' } });
        require(['vs/editor/editor.main'], function() {
          monaco.editor.defineTheme('zebra-dark', {
            base: 'vs-dark',
            inherit: true,
            rules: [],
            colors: {}
          });
          window.__editor = monaco.editor.create(document.getElementById('editor'), {
            value: window.__pendingValue,
            language: window.__pendingLanguage,
            theme: 'zebra-dark',
            automaticLayout: true,
            minimap: { enabled: true },
            glyphMargin: true,
            renderValidationDecorations: 'on',
            fontSize: 14,
            roundedSelection: false,
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            renderWhitespace: 'selection',
            guides: { indentation: true },
            bracketPairColorization: { enabled: true }
          });
          window.__editor.getModel().onDidChangeContent(function() {
            if (!window.__suppressDirty && window.__bridge && window.__bridge.notifyDirty) {
              window.__bridge.notifyDirty(true);
            }
            if (window.__contentPushTimer) {
              clearTimeout(window.__contentPushTimer);
            }
            window.__contentPushTimer = setTimeout(function() {
              if (window.__bridge && window.__bridge.updateContent) {
                window.__bridge.updateContent(window.__editor.getValue());
              }
            }, 120);
          });
          monaco.editor.setTheme('zebra-dark');
          if (window.__pendingLanguage) {
            monaco.editor.setModelLanguage(window.__editor.getModel(), window.__pendingLanguage);
          }
          if (window.__pendingMarkers && window.__pendingMarkers.length) {
            monaco.editor.setModelMarkers(window.__editor.getModel(), 'python', window.__pendingMarkers);
          } else {
            monaco.editor.setModelMarkers(window.__editor.getModel(), 'python', []);
          }
          window.__editorReady = true;
          setStatus('Syntax: ' + (window.__pendingLanguage || 'plaintext'));
          if (window.__bridge && window.__bridge.updateContent) window.__bridge.updateContent(window.__editor.getValue());
          if (window.__bridge && window.__bridge.editorReady) window.__bridge.editorReady(true);
          if (window.__bridge && window.__bridge.postStatus) window.__bridge.postStatus('Monaco ready');
        }, function(err) {
          setStatus('Monaco failed to load');
          console.error(err);
        });
      }

      if (typeof qt !== 'undefined' && qt.webChannelTransport) {
        new QWebChannel(qt.webChannelTransport, function(channel) {
          window.__bridge = channel.objects.bridge;
          startMonaco();
        });
      } else {
        setStatus('Qt WebChannel unavailable');
      }
    })();
  </script>
</body>
</html>
        """


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("teleopp - ZebraBot Flash/Deploy + BLE Motor Test")
        self.setStyleSheet("""
            QWidget { background: #10151c; color: #dce7f3; }
            QGroupBox { border: 1px solid #283545; border-radius: 10px; margin-top: 12px; padding-top: 10px; font-weight: 600; background: #151d27; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9fc4ff; }
            QPushButton { background: #1f2a36; border: 1px solid #304153; border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover { background: #283648; }
            QPushButton:disabled { color: #6e7d8c; background: #18202a; }
            QLineEdit, QTextEdit, QListWidget, QSpinBox, QComboBox { background: #0f141b; border: 1px solid #2d3a49; border-radius: 8px; padding: 6px; color: #e6edf3; }
            QLabel { color: #dce7f3; }
            QTabWidget::pane { border: 1px solid #283545; background: #10151c; }
            QTabBar::tab { background: #18212b; color: #b9c8d6; padding: 8px 14px; border: 1px solid #283545; border-bottom: none; margin-right: 2px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            QTabBar::tab:selected { background: #223041; color: #ffffff; }
        """)
        self.resize(1180, 960)

        root = QVBoxLayout(self)
        tabs = QTabWidget()

        self.tab_flash = FlashDeployTab()
        self.tab_ble = BleTeleopTab()
        self.tab_editor = ProjectEditorTab()
        self.tab_editor.deployRequested.connect(self.tab_flash.set_source_root_from_project)
        self.tab_editor.configureDeployRequested.connect(self.tab_flash.open_deploy_config_dialog)

        tabs.addTab(self.tab_flash, "Flash + Deploy")
        tabs.addTab(self.tab_ble, "BLE Motor Test")
        tabs.addTab(self.tab_editor, "Project Editor")

        root.addWidget(tabs)

        foot = QLabel(
            "Tip: BLE upload is for user code files only. Firmware flashing still uses the serial tab controls. "
            "Serial port selection supports AUTO detection for Windows, macOS, and Linux."
        )
        foot.setWordWrap(True)
        root.addWidget(foot)


def main():
    try:
        os.chdir(app_root_dir())
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
