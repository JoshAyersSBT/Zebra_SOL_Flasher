import os
import sys
import tempfile
import subprocess
import argparse
import asyncio
import base64
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Iterable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QLineEdit, QTextEdit, QGroupBox, QFormLayout, QMessageBox,
    QSpinBox, QTabWidget, QListWidget, QListWidgetItem, QSlider, QGridLayout,
    QComboBox, QCheckBox
)

from PyQt6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothDeviceInfo,
    QBluetoothLocalDevice,
    QLowEnergyController,
    QLowEnergyService,
    QBluetoothUuid,
)


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


def gather_upload_files(source: Path, include_exts: set[str]) -> list[tuple[Path, str]]:
    source = source.resolve()
    files: list[tuple[Path, str]] = []

    if source.is_file():
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
        files.append((path, rel))
    return files


# ============================================================
# Flash/Deploy worker
# ============================================================
@dataclass
class Job:
    kind: str
    port: str = ""
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


class Worker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, job: Job):
        super().__init__()
        self.job = job

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

    def run(self):
        try:
            if self.job.kind == "flash":
                if not self.job.firmware_path or not os.path.exists(self.job.firmware_path):
                    raise RuntimeError("Firmware path is missing or does not exist.")

                self._run(
                    self._python_m(
                        "esptool",
                        "--chip", "esp32",
                        "--port", self.job.port,
                        "erase_flash",
                    ),
                    timeout=180,
                )
                self._run(
                    self._python_m(
                        "esptool",
                        "--chip", "esp32",
                        "--port", self.job.port,
                        "--baud", str(self.job.baud),
                        "write_flash", "-z", "0x1000",
                        self.job.firmware_path,
                    ),
                    timeout=240,
                )
                self.done.emit(True, "Flash complete.")

            elif self.job.kind == "deploy":
                source_root = os.path.abspath(self.job.source_root)
                robot_src = os.path.join(source_root, "robot")
                main_src = os.path.join(source_root, "main.py")

                if not os.path.isdir(robot_src):
                    raise RuntimeError(f"robot/ folder not found: {robot_src}")
                if not os.path.isfile(main_src):
                    raise RuntimeError(f"main.py not found: {main_src}")

                tmp = tempfile.mkdtemp(prefix="zebrabot_deploy_")
                self.log.emit(f">> staging files from {source_root}")
                self.log.emit(f">> temp dir: {tmp}")

                staged_robot = os.path.join(tmp, "robot")
                os.makedirs(staged_robot, exist_ok=True)

                for name in os.listdir(robot_src):
                    src_path = os.path.join(robot_src, name)
                    dst_path = os.path.join(staged_robot, name)

                    if os.path.isfile(src_path):
                        with open(src_path, "r", encoding="utf-8") as f:
                            text = f.read()

                        if name == "config.py":
                            text = self._patch_config_text(
                                text,
                                left_pwm=self.job.left_pwm,
                                left_dir=self.job.left_dir,
                                right_pwm=self.job.right_pwm,
                                right_dir=self.job.right_dir,
                                servo_gpio=self.job.servo_gpio,
                            )

                        with open(dst_path, "w", encoding="utf-8") as f:
                            f.write(text)

                staged_main = os.path.join(tmp, "main.py")
                with open(main_src, "r", encoding="utf-8") as f:
                    main_text = f.read()
                with open(staged_main, "w", encoding="utf-8") as f:
                    f.write(main_text)

                def mp(*a: str) -> list[str]:
                    return [sys.executable, "-m", "mpremote", "connect", self.job.port, *a]

                try:
                    self._run(mp("fs", "mkdir", ":/robot"), timeout=60)
                except Exception:
                    self.log.emit(">> (mkdir :/robot) exists or not supported; continuing...")

                for name in os.listdir(staged_robot):
                    local_path = os.path.join(staged_robot, name)
                    if os.path.isfile(local_path):
                        self._run(mp("fs", "cp", local_path, f":/robot/{name}"), timeout=60)

                self._run(mp("fs", "cp", staged_main, ":/main.py"), timeout=60)
                self._run(mp("reset"), timeout=30)

                self.done.emit(True, "Deploy complete from local robot/ + main.py.")

            elif self.job.kind == "ble_deploy":
                asyncio.run(self._run_ble_deploy())
                self.done.emit(True, "BLE code upload complete.")
            else:
                raise RuntimeError(f"Unknown job kind: {self.job.kind}")

        except Exception as e:
            self.done.emit(False, str(e))

    async def _run_ble_deploy(self):
        source = Path(self.job.ble_source)
        if not source.exists():
            raise RuntimeError(f"Source does not exist: {source}")

        include_exts = {
            e.strip().lower() if e.strip().startswith('.') else '.' + e.strip().lower()
            for e in self.job.ble_exts.split(',') if e.strip()
        }
        files = gather_upload_files(source, include_exts)
        if not files:
            raise RuntimeError("No matching files found to upload.")

        dest_root = (self.job.ble_dest_root or "/").strip()
        if not dest_root.startswith("/"):
            dest_root = "/" + dest_root
        dest_root = dest_root.rstrip("/") or "/"

        address = self.job.ble_address.strip()
        if not address:
            address = await discover_ble_address(self.job.ble_name.strip() or "ZebraBot", log_cb=self.log.emit)

        async with GuiBleCodeUploader(
            address=address,
            chunk_size=int(self.job.ble_chunk_size),
            log_cb=self.log.emit,
        ) as up:
            for local_path, rel in files:
                remote_path = (dest_root + "/" + rel).replace("//", "/")
                data = local_path.read_bytes()
                self.log.emit(f"Uploading {local_path} -> {remote_path}")
                await up.put_bytes(remote_path, data)

            if self.job.ble_reboot:
                self.log.emit("Requesting robot reboot...")
                await up.reboot()

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


# ============================================================
# Tab 1: Flash + Deploy UI
# ============================================================
class FlashDeployTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker: Worker | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        g_conn = QGroupBox("Connection")
        f_conn = QFormLayout(g_conn)
        self.port_edit = QLineEdit("COM7")
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 3_000_000)
        self.baud_spin.setValue(460800)
        f_conn.addRow("Serial Port (COMx):", self.port_edit)
        f_conn.addRow("Flash Baud:", self.baud_spin)

        g_fw = QGroupBox("Firmware")
        fw_layout = QHBoxLayout(g_fw)
        self.fw_path = QLineEdit("")
        self.btn_pick_fw = QPushButton("Choose .bin…")
        self.btn_pick_fw.clicked.connect(self.pick_firmware)
        fw_layout.addWidget(QLabel("Firmware .bin:"))
        fw_layout.addWidget(self.fw_path, 1)
        fw_layout.addWidget(self.btn_pick_fw)

        g_cfg = QGroupBox("Robot Pin Config (used for serial deploy)")
        cfg = QFormLayout(g_cfg)
        self.left_pwm = QSpinBox()
        self.left_pwm.setRange(0, 39)
        self.left_pwm.setValue(18)
        self.left_dir = QSpinBox()
        self.left_dir.setRange(0, 39)
        self.left_dir.setValue(19)
        self.right_pwm = QSpinBox()
        self.right_pwm.setRange(0, 39)
        self.right_pwm.setValue(21)
        self.right_dir = QSpinBox()
        self.right_dir.setRange(0, 39)
        self.right_dir.setValue(22)
        self.servo_gpio = QSpinBox()
        self.servo_gpio.setRange(0, 39)
        self.servo_gpio.setValue(23)

        cfg.addRow("Left motor PWM GPIO:", self.left_pwm)
        cfg.addRow("Left motor DIR GPIO:", self.left_dir)
        cfg.addRow("Right motor PWM GPIO:", self.right_pwm)
        cfg.addRow("Right motor DIR GPIO:", self.right_dir)
        cfg.addRow("Steering servo GPIO:", self.servo_gpio)

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
            "Deploy uses the same local project root for both methods and expects a main.py file "
            "plus a robot/ folder. Choose Serial to copy with mpremote, or BLE to upload the same "
            "code set over Bluetooth."
        )
        deploy_help.setWordWrap(True)

        actions = QHBoxLayout()
        self.btn_flash = QPushButton("Erase + Flash Firmware")
        self.btn_deploy = QPushButton("Deploy main.py + robot/")
        self.btn_flash.clicked.connect(self.do_flash)
        self.btn_deploy.clicked.connect(self.do_deploy)
        actions.addWidget(self.btn_flash)
        actions.addWidget(self.btn_deploy)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace;")

        layout.addWidget(g_conn)
        layout.addWidget(g_fw)
        layout.addWidget(g_cfg)
        layout.addWidget(g_deploy)
        layout.addWidget(deploy_help)
        layout.addLayout(actions)
        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.log, 1)

        self.on_deploy_method_changed()

    def append_log(self, text: str):
        self.log.append(text)

    def on_deploy_method_changed(self):
        is_serial = self.deploy_method.currentData() == "serial"
        self.port_edit.setEnabled(is_serial)
        self.baud_spin.setEnabled(is_serial)
        self.left_pwm.setEnabled(is_serial)
        self.left_dir.setEnabled(is_serial)
        self.right_pwm.setEnabled(is_serial)
        self.right_dir.setEnabled(is_serial)
        self.servo_gpio.setEnabled(is_serial)

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
        self.btn_pick_source.setEnabled(not busy)
        self.deploy_method.setEnabled(not busy)

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

    def _run_job(self, job: Job):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A job is already running.")
            return

        self.append_log("\n==============================")
        self.append_log(f"Starting job: {job.kind}")
        self.set_busy(True)

        self.worker = Worker(job)
        self.worker.log.connect(self.append_log)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def do_flash(self):
        port = self.port_edit.text().strip()
        fw = self.fw_path.text().strip()
        baud = int(self.baud_spin.value())

        if not port:
            QMessageBox.warning(self, "Missing port", "Please enter a serial port (e.g., COM7).")
            return
        if not fw or not os.path.exists(fw):
            QMessageBox.warning(self, "Missing firmware", "Please choose a valid firmware .bin file.")
            return

        self._run_job(Job(kind="flash", port=port, firmware_path=fw, baud=baud))

    def do_deploy(self):
        method = self.deploy_method.currentData()
        source_root = self.source_root.text().strip()

        if not source_root or not os.path.isdir(source_root):
            QMessageBox.warning(self, "Missing source root", "Please choose a valid project root.")
            return

        robot_dir = os.path.join(source_root, "robot")
        main_py = os.path.join(source_root, "main.py")

        if not os.path.isdir(robot_dir):
            QMessageBox.warning(self, "Missing robot folder", f"Could not find:\n{robot_dir}")
            return

        if not os.path.isfile(main_py):
            QMessageBox.warning(self, "Missing main.py", f"Could not find:\n{main_py}")
            return

        if method == "serial":
            port = self.port_edit.text().strip()
            if not port:
                QMessageBox.warning(self, "Missing port", "Please enter a serial port (e.g., COM7).")
                return

            self._run_job(Job(
                kind="deploy",
                port=port,
                baud=int(self.baud_spin.value()),
                source_root=source_root,
                left_pwm=int(self.left_pwm.value()),
                left_dir=int(self.left_dir.value()),
                right_pwm=int(self.right_pwm.value()),
                right_dir=int(self.right_dir.value()),
                servo_gpio=int(self.servo_gpio.value()),
            ))
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

    def on_done(self, ok: bool, msg: str):
        self.set_busy(False)
        if ok:
            self.append_log(f"✅ {msg}")
        else:
            self.append_log(f"❌ {msg}")
            QMessageBox.critical(self, "Operation failed", msg)


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

        sensor_box = QGroupBox("Sensor Ports (MUX 1-6)")
        sensor_layout = QGridLayout(sensor_box)

        self.sensor_labels = {}
        self.sensor_i2c_labels = {}
        self.sensor_value_labels = {}

        sensor_layout.addWidget(QLabel("Port"), 0, 0)
        sensor_layout.addWidget(QLabel("Status"), 0, 1)
        sensor_layout.addWidget(QLabel("I2C Addr(s)"), 0, 2)
        sensor_layout.addWidget(QLabel("Value"), 0, 3)

        for port in range(1, 7):
            lbl_port = QLabel(str(port))
            lbl_status = QLabel("unknown")
            lbl_i2c = QLabel("--")
            lbl_value = QLabel("--")

            self.sensor_labels[port] = lbl_status
            self.sensor_i2c_labels[port] = lbl_i2c
            self.sensor_value_labels[port] = lbl_value

            row = port
            sensor_layout.addWidget(lbl_port, row, 0)
            sensor_layout.addWidget(lbl_status, row, 1)
            sensor_layout.addWidget(lbl_i2c, row, 2)
            sensor_layout.addWidget(lbl_value, row, 3)

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
        root.addWidget(sensor_box)
        root.addWidget(motor_box)
        root.addLayout(motor_btn_row)
        root.addWidget(motor_test_box)
        root.addWidget(help_text)
        root.addWidget(QLabel("BLE Log:"))
        root.addWidget(self.log_box, 1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        if not self.local_bt.isValid():
            self.status.setText("No usable local Bluetooth adapter found by Qt.")
            self.btn_scan.setEnabled(False)

    def _make_slider(self, mn, mx, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(mn, mx)
        s.setValue(val)
        return s

    def _reset_sensor_rows(self):
        for port in range(1, 7):
            self.sensor_labels[port].setText("unknown")
            self.sensor_i2c_labels[port].setText("--")
            self.sensor_value_labels[port].setText("--")

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
                if 1 <= port <= 6:
                    self.sensor_labels[port].setText(state)
                    if state == "empty":
                        self.sensor_i2c_labels[port].setText("--")
                        self.sensor_value_labels[port].setText("--")
                    elif state == "unidentified":
                        self.sensor_value_labels[port].setText("--")
            except Exception as e:
                self.log.emit(f"SNS parse error: {e}")
            return

        if parts[0] == "SNS_I2C" and len(parts) >= 3:
            try:
                port = int(parts[1])
                addrs = " ".join(parts[2:])
                if 1 <= port <= 6:
                    self.sensor_i2c_labels[port].setText(addrs)
            except Exception as e:
                self.log.emit(f"SNS_I2C parse error: {e}")
            return

        if parts[0] == "SNS_TOF" and len(parts) >= 3:
            try:
                port = int(parts[1])
                dist_mm = int(parts[2])
                if 1 <= port <= 6:
                    self.sensor_labels[port].setText("UL53LDK")
                    self.sensor_value_labels[port].setText(f"{dist_mm} mm")
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
                if 1 <= port <= 6:
                    self.sensor_labels[port].setText("TCS3472")
                    self.sensor_value_labels[port].setText(f"R{r} G{g} B{b} C{c}")
            except Exception as e:
                self.log.emit(f"SNS_COLOR parse error: {e}")
            return

        if parts[0] == "SNS_ERR" and len(parts) >= 3:
            try:
                port = int(parts[1])
                msg = " ".join(parts[2:])
                if 1 <= port <= 6:
                    self.sensor_labels[port].setText("error")
                    self.sensor_value_labels[port].setText(msg)
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


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("teleopp - ZebraBot Flash/Deploy + BLE Motor Test")
        self.resize(1180, 960)

        root = QVBoxLayout(self)
        tabs = QTabWidget()

        self.tab_flash = FlashDeployTab()
        self.tab_ble = BleTeleopTab()

        tabs.addTab(self.tab_flash, "Flash + Deploy")
        tabs.addTab(self.tab_ble, "BLE Motor Test")

        root.addWidget(tabs)

        foot = QLabel(
            "Tip: BLE upload is for user code files only. Firmware flashing still uses the serial tab controls."
        )
        foot.setWordWrap(True)
        root.addWidget(foot)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
