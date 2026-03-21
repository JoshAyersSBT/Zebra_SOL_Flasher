import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from ble_bridge import BleBridge
from deploy_worker import DeployWorker, DeployJob


class IdeWebPage(QWebEnginePage):
    jsConsoleMessage = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        lvl = getattr(level, "name", str(level))
        src = source_id or "<inline>"
        self.jsConsoleMessage.emit(f"[JS:{lvl}] {src}:{line_number} :: {message}")


class IdeBridge(QObject):
    logMessage = pyqtSignal(str)
    bleStatusChanged = pyqtSignal(str)
    devicesChanged = pyqtSignal(str)
    telemetryChanged = pyqtSignal(str)
    imuChanged = pyqtSignal(str)
    portStateChanged = pyqtSignal(str)
    firmwarePathChanged = pyqtSignal(str)
    projectRootChanged = pyqtSignal(str)
    serialPortChanged = pyqtSignal(str)
    baudRateChanged = pyqtSignal(int)
    frontendReadyChanged = pyqtSignal(bool)

    def __init__(self, parent_widget: QWidget | None = None):
        super().__init__(parent_widget)
        self.parent_widget = parent_widget

        self.ble = BleBridge(self)
        self.worker: DeployWorker | None = None
        self.pending_flash_after_current = False

        self.firmware_path = ""
        self.project_root = str(Path.cwd())
        self.port = "COM7"
        self.baud = 460800

        self.frontend_ready = False

        self._wire_ble_signals()

    def _wire_ble_signals(self):
        self.ble.logMessage.connect(self.logMessage.emit)
        self.ble.bleStatusChanged.connect(self.bleStatusChanged.emit)
        self.ble.devicesChanged.connect(self.devicesChanged.emit)
        self.ble.telemetryChanged.connect(self.telemetryChanged.emit)
        self.ble.imuChanged.connect(self.imuChanged.emit)
        self.ble.portStateChanged.connect(self.portStateChanged.emit)

    def _set_frontend_ready(self, ready: bool):
        ready = bool(ready)
        if self.frontend_ready == ready:
            return
        self.frontend_ready = ready
        self.frontendReadyChanged.emit(ready)
        self.logMessage.emit(f"Frontend ready = {self.frontend_ready}")

    def _start_worker(self, job: DeployJob):
        if self.worker is not None and self.worker.isRunning():
            self.logMessage.emit("Busy: a deploy or flash operation is already running.")
            QMessageBox.information(
                self.parent_widget,
                "Busy",
                "A deploy or flash operation is already running.",
            )
            return False

        self.logMessage.emit(
            f"Starting worker: kind={job.kind}, port={job.port}, baud={job.baud}"
        )
        self.worker = DeployWorker(job)
        self.worker.log.connect(self.logMessage.emit)
        self.worker.done.connect(self._on_worker_done)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()
        return True

    def _on_worker_done(self, ok: bool, message: str):
        self.logMessage.emit(("OK: " if ok else "ERR: ") + message)

        if self.pending_flash_after_current:
            self.pending_flash_after_current = False
            if ok:
                self.logMessage.emit("flashAndDeploy: flash finished, starting deploy step")
                deploy_job = DeployJob(
                    kind="deploy",
                    port=self.port,
                    source_root=self.project_root,
                    baud=self.baud,
                )
                self._start_worker(deploy_job)
                return
            else:
                self.logMessage.emit("flashAndDeploy aborted because flash step failed")

        if not ok:
            QMessageBox.critical(self.parent_widget, "Operation failed", message)

    def _on_worker_finished(self):
        self.logMessage.emit("Worker finished")

    @pyqtSlot()
    def syncFrontend(self):
        self.logMessage.emit("syncFrontend() called")

        self.firmwarePathChanged.emit(self.firmware_path)
        self.projectRootChanged.emit(self.project_root)
        self.serialPortChanged.emit(self.port)
        self.baudRateChanged.emit(self.baud)
        self.bleStatusChanged.emit("Ready.")

        self.ble._emit_devices()
        self.ble._emit_telemetry()
        self.ble._emit_imu()
        for port in range(1, 7):
            self.ble._emit_port(port)

        self._set_frontend_ready(True)

    @pyqtSlot()
    def notifyPageLoaded(self):
        self.logMessage.emit("notifyPageLoaded() called from JS")
        self._set_frontend_ready(True)

    @pyqtSlot()
    def scanBle(self):
        self.logMessage.emit("scanBle() called")
        self.ble.scanBle()

    @pyqtSlot()
    def disconnectBle(self):
        self.logMessage.emit("disconnectBle() called")
        self.ble.disconnectBle()

    @pyqtSlot(int)
    def connectBle(self, index: int):
        self.logMessage.emit(f"connectBle({index}) called")
        self.ble.connectBle(index)

    @pyqtSlot(int, int, int)
    def setDrive(self, throttle: int, turn: int, steer: int):
        self.logMessage.emit(f"setDrive({throttle}, {turn}, {steer}) called")
        self.ble.setDrive(throttle, turn, steer)

    @pyqtSlot()
    def stopRobot(self):
        self.logMessage.emit("stopRobot() called")
        self.ble.stopRobot()

    @pyqtSlot()
    def centerSteering(self):
        self.logMessage.emit("centerSteering() called")
        self.ble.centerSteering()

    @pyqtSlot(str)
    def sendRobotLine(self, line: str):
        self.logMessage.emit(f"sendRobotLine({line}) called")
        self.ble.sendRobotLine(line)

    @pyqtSlot(str)
    def sendSysCommand(self, command: str):
        self.logMessage.emit(f"sendSysCommand({command}) called")
        self.ble.sendSysCommand(command)

    @pyqtSlot()
    def pickFirmware(self):
        path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            "Select firmware image",
            self.firmware_path or self.project_root,
            "Firmware (*.bin);;All Files (*)",
        )
        if path:
            self.firmware_path = path
            self.firmwarePathChanged.emit(path)
            self.logMessage.emit(f"Selected firmware: {path}")
        else:
            self.logMessage.emit("Firmware selection cancelled")

    @pyqtSlot()
    def pickProjectRoot(self):
        path = QFileDialog.getExistingDirectory(
            self.parent_widget,
            "Select project root",
            self.project_root,
        )
        if path:
            self.project_root = path
            self.projectRootChanged.emit(path)
            self.logMessage.emit(f"Selected project root: {path}")
        else:
            self.logMessage.emit("Project root selection cancelled")

    @pyqtSlot(str)
    def setSerialPort(self, port: str):
        port = (port or "").strip()
        if not port:
            self.logMessage.emit("Ignored empty serial port")
            return
        self.port = port
        self.serialPortChanged.emit(self.port)
        self.logMessage.emit(f"Serial port set to: {self.port}")

    @pyqtSlot(int)
    def setBaudRate(self, baud: int):
        if baud <= 0:
            self.logMessage.emit(f"Ignored invalid baud rate: {baud}")
            return
        self.baud = int(baud)
        self.baudRateChanged.emit(self.baud)
        self.logMessage.emit(f"Baud rate set to: {self.baud}")

    @pyqtSlot()
    def flashFirmware(self):
        self.logMessage.emit("flashFirmware() called")
        if not self.firmware_path:
            self.logMessage.emit("Flash blocked: no firmware selected")
            QMessageBox.warning(
                self.parent_widget,
                "Missing firmware",
                "Please select a firmware .bin file first.",
            )
            return

        self.pending_flash_after_current = False
        job = DeployJob(
            kind="flash",
            port=self.port,
            firmware_path=self.firmware_path,
            baud=self.baud,
        )
        self._start_worker(job)

    @pyqtSlot()
    def deployProject(self):
        self.logMessage.emit("deployProject() called")
        self.pending_flash_after_current = False
        job = DeployJob(
            kind="deploy",
            port=self.port,
            source_root=self.project_root,
            baud=self.baud,
        )
        self._start_worker(job)

    @pyqtSlot()
    def flashAndDeploy(self):
        self.logMessage.emit("flashAndDeploy() called")

        if not self.firmware_path:
            self.logMessage.emit("flashAndDeploy blocked: no firmware selected")
            QMessageBox.warning(
                self.parent_widget,
                "Missing firmware",
                "Please select a firmware .bin file first.",
            )
            return

        self.pending_flash_after_current = True
        flash_job = DeployJob(
            kind="flash",
            port=self.port,
            firmware_path=self.firmware_path,
            baud=self.baud,
        )
        started = self._start_worker(flash_job)
        if not started:
            self.pending_flash_after_current = False


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZebraBot IDE")
        self.resize(1600, 950)

        self.view = QWebEngineView(self)
        self.view.setMinimumSize(1200, 800)
        self.view.setZoomFactor(1.0)

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)

        self.page = IdeWebPage(self.view)
        self.view.setPage(self.page)

        self.bridge = IdeBridge(self)

        self.channel = QWebChannel(self.page)
        self.channel.registerObject("api", self.bridge)
        self.page.setWebChannel(self.channel)

        self.page.jsConsoleMessage.connect(self.bridge.logMessage.emit)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.urlChanged.connect(self._on_url_changed)
        self.page.renderProcessTerminated.connect(self._on_render_terminated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self._load_frontend()

    def _load_frontend(self):
        base_dir = Path(__file__).resolve().parent
        web_dir = base_dir / "web"

        index_path = web_dir / "index.html"
        css_path = web_dir / "style.css"
        js_path = web_dir / "app.js"
        imu_js_path = web_dir / "imu_viz.js"

        missing = []
        for p in (index_path, css_path, js_path, imu_js_path):
            if not p.exists():
                missing.append(str(p))

        if missing:
            QMessageBox.critical(
                self,
                "Missing frontend assets",
                "These files are missing:\n\n" + "\n".join(missing),
            )
            return

        self.bridge.logMessage.emit(f"Loading frontend: {index_path}")
        self.bridge.logMessage.emit(f"Resolved CSS: {css_path}")
        self.bridge.logMessage.emit(f"Resolved JS: {js_path}")
        self.bridge.logMessage.emit(f"Resolved IMU JS: {imu_js_path}")

        html = index_path.read_text(encoding="utf-8")
        base_url = QUrl.fromLocalFile(str(web_dir.resolve()) + "/")
        self.page.setHtml(html, base_url)
    def _on_load_started(self):
        self.bridge.logMessage.emit("WebView load started")
        self.bridge._set_frontend_ready(False)

    def _on_load_finished(self, ok: bool):
        self.bridge.logMessage.emit(f"WebView load finished: ok={ok}")
        if not ok:
            QMessageBox.critical(
                self,
                "Frontend load failed",
                "The web frontend failed to load. Check the log panel for details.",
            )
            return

        self.page.runJavaScript(
            """
            (function() {
              try {
                if (window.zebraIde) {
                  console.log("window.zebraIde detected");
                } else {
                  console.log("window.zebraIde not present yet");
                }
                return true;
              } catch (e) {
                console.error("post-load probe failed:", e);
                return false;
              }
            })();
            """
        )

    def _on_url_changed(self, url: QUrl):
        self.bridge.logMessage.emit(f"WebView URL changed: {url.toString()}")

    def _on_render_terminated(self, termination_status, exit_code):
        self.bridge.logMessage.emit(
            f"Render process terminated: status={termination_status}, exit_code={exit_code}"
        )
        QMessageBox.critical(
            self,
            "Renderer crashed",
            f"The embedded web renderer terminated.\nStatus: {termination_status}\nExit code: {exit_code}",
        )

    def closeEvent(self, event):
        try:
            self.bridge.disconnectBle()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()