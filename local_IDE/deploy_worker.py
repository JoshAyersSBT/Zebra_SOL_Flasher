import os
import sys
import subprocess
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


@dataclass
class DeployJob:
    kind: str
    port: str
    firmware_path: Optional[str] = None
    baud: int = 460800
    source_root: str = "."


class DeployWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, job: DeployJob):
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

    def _mpremote(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "mpremote", "connect", self.job.port, *args]

    def _ensure_rtos_layout(self, source_root: str):
        main_src = os.path.join(source_root, "main.py")
        rtos_src = os.path.join(source_root, "rtos")

        if not os.path.isfile(main_src):
            raise RuntimeError(f"main.py not found: {main_src}")
        if not os.path.isdir(rtos_src):
            raise RuntimeError(f"rtos/ not found: {rtos_src}")

        return main_src, rtos_src

    def _copy_tree_to_board(self, local_dir: str, remote_dir: str):
        try:
            self._run(self._mpremote("fs", "mkdir", remote_dir), timeout=60)
        except Exception:
            self.log.emit(f">> mkdir skipped for {remote_dir}")

        for name in os.listdir(local_dir):
            local_path = os.path.join(local_dir, name)
            remote_path = f"{remote_dir.rstrip('/')}/{name}"

            if os.path.isdir(local_path):
                self._copy_tree_to_board(local_path, remote_path)
            elif os.path.isfile(local_path):
                self._run(self._mpremote("fs", "cp", local_path, remote_path), timeout=60)

    def _flash(self):
        if not self.job.firmware_path or not os.path.exists(self.job.firmware_path):
            raise RuntimeError("Firmware path missing or invalid")

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

    def _deploy_rtos_project(self):
        source_root = os.path.abspath(self.job.source_root)
        main_src, rtos_src = self._ensure_rtos_layout(source_root)

        self.log.emit(f">> staging RTOS project from {source_root}")

        try:
            self._run(self._mpremote("fs", "mkdir", ":/rtos"), timeout=60)
        except Exception:
            self.log.emit(">> :/rtos exists or mkdir unsupported")

        self._run(self._mpremote("fs", "cp", main_src, ":/main.py"), timeout=60)
        self._copy_tree_to_board(rtos_src, ":/rtos")

    def run(self):
        try:
            if self.job.kind == "flash":
                self._flash()
                self.done.emit(True, "Flash complete")
                return

            if self.job.kind == "deploy":
                self._deploy_rtos_project()
                self.done.emit(True, "Deploy complete")
                return

            raise RuntimeError(f"Unknown job kind: {self.job.kind}")

        except Exception as exc:
            self.done.emit(False, str(exc))