import json
import traceback
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothDeviceInfo,
    QBluetoothLocalDevice,
    QBluetoothUuid,
    QLowEnergyController,
    QLowEnergyService,
)

from PyQt6.QtBluetooth import QBluetoothDeviceInfo

class BleBridge(QObject):
    logMessage = pyqtSignal(str)
    bleStatusChanged = pyqtSignal(str)
    devicesChanged = pyqtSignal(str)
    telemetryChanged = pyqtSignal(str)
    imuChanged = pyqtSignal(str)
    portStateChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.local_bt = QBluetoothLocalDevice()
        self.agent = QBluetoothDeviceDiscoveryAgent(self)
        self.agent.deviceDiscovered.connect(self._safe(self.on_device_discovered))
        self.agent.finished.connect(self._safe(self.on_scan_finished))
        self.agent.canceled.connect(self._safe(self.on_scan_finished))
        self.agent.errorOccurred.connect(self._safe(self.on_scan_error))

        self.controller: Optional[QLowEnergyController] = None
        self.status_service: Optional[QLowEnergyService] = None
        self.teleop_service: Optional[QLowEnergyService] = None

        self.devices: list[dict] = []
        self.device_infos: list[QBluetoothDeviceInfo] = []
        self.discovered_services: set[str] = set()

        self._is_disconnecting = False
        self._conn_generation = 0

        self.drive_throttle = 0
        self.drive_turn = 0
        self.drive_steer = 90

        self.send_debounce = QTimer(self)
        self.send_debounce.setInterval(80)
        self.send_debounce.setSingleShot(True)
        self.send_debounce.timeout.connect(self._safe(self._flush_drive))

        self._telemetry = {
            "uptime_ms": None,
            "mem_free": None,
            "mem_alloc": None,
            "load_pct": None,
            "loop_lag_ms": None,
            "i2c": {"devices": []},
            "ble": {"connected": False, "conn_count": 0},
        }
        self._imu = {}
        self._ports = {
            i: {"port": i, "status": "unknown", "i2c": "--", "value": "--"}
            for i in range(1, 7)
        }

        # RTOS structured service UUIDs
        self.SYS_SERVICE_UUID = QBluetoothUuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f01")
        self.SYS_HOST_UUID = QBluetoothUuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f02")
        self.SYS_STATUS_UUID = QBluetoothUuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f03")
        self.SYS_CMD_UUID = QBluetoothUuid("2a0b7d3a-8f1c-4f2f-9c20-4d88d6f46f04")

        # Legacy teleop NUS UUIDs
        self.NUS_SERVICE_UUID = QBluetoothUuid("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        self.NUS_TX_UUID = QBluetoothUuid("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
        self.NUS_RX_UUID = QBluetoothUuid("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")

        self.CCCD_UUID = QBluetoothUuid(
            QBluetoothUuid.DescriptorType.ClientCharacteristicConfiguration
        )

        self.sys_host_char = None
        self.sys_status_char = None
        self.sys_cmd_char = None
        self.nus_tx_char = None
        self.nus_rx_char = None

    def _safe(self, fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                self.logMessage.emit(f"BLE callback error in {fn.__name__}: {exc}")
                self.logMessage.emit(traceback.format_exc())
        return wrapper

    def _current_sender_is(self, obj) -> bool:
        try:
            return self.sender() is obj
        except Exception:
            return False

    def _emit_devices(self):
        self.devicesChanged.emit(json.dumps(self.devices))

    def _emit_telemetry(self):
        self.telemetryChanged.emit(json.dumps(self._telemetry))

    def _emit_imu(self):
        self.imuChanged.emit(json.dumps(self._imu))

    def _emit_port(self, port: int):
        if port in self._ports:
            self.portStateChanged.emit(json.dumps(self._ports[port]))

    def _clear_runtime_handles(self):
        self.status_service = None
        self.teleop_service = None
        self.sys_host_char = None
        self.sys_status_char = None
        self.sys_cmd_char = None
        self.nus_tx_char = None
        self.nus_rx_char = None
        self.discovered_services.clear()

    @pyqtSlot()
    def scanBle(self):
        try:
            if self.agent.isActive():
                self.agent.stop()
        except Exception:
            pass

        self.devices.clear()
        self.device_infos.clear()
        self.discovered_services.clear()
        self._emit_devices()

        self.bleStatusChanged.emit("Scanning for ZebraBot / mp-rtos devices...")
        self.logMessage.emit("BLE scan started")

        try:
            self.agent.start()
        except Exception as exc:
            self.bleStatusChanged.emit(f"Scan failed: {exc}")
            self.logMessage.emit(f"Scan failed: {exc}")

    @pyqtSlot(int)
    def connectBle(self, index: int):
        if index < 0 or index >= len(self.device_infos):
            self.logMessage.emit("Invalid BLE device selection")
            return

        try:
            if self.agent.isActive():
                self.logMessage.emit("Stopping scan before connect")
                self.agent.stop()
                QTimer.singleShot(300, self._begin_pending_connect)
                return
        except Exception as exc:
            self.logMessage.emit(f"Agent stop warning before connect: {exc}")

        self._begin_pending_connect()

    def _disconnect_internal(self, user_initiated: bool = False):
        self._is_disconnecting = True
        self._conn_generation += 1
        self.send_debounce.stop()
        self.logMessage.emit("BLE disconnect requested")

        # Stop scanning if still active
        try:
            if self.agent.isActive():
                self.agent.stop()
        except Exception:
            pass

        # Detach service signals first, but do not aggressively delete objects here.
        try:
            if self.teleop_service is not None:
                try:
                    self.teleop_service.stateChanged.disconnect()
                except Exception:
                    pass
                try:
                    self.teleop_service.characteristicChanged.disconnect()
                except Exception:
                    pass
                try:
                    self.teleop_service.errorOccurred.disconnect()
                except Exception:
                    pass
        except Exception as exc:
            self.logMessage.emit(f"teleop_service cleanup warning: {exc}")

        try:
            if self.status_service is not None:
                try:
                    self.status_service.stateChanged.disconnect()
                except Exception:
                    pass
                try:
                    self.status_service.characteristicChanged.disconnect()
                except Exception:
                    pass
                try:
                    self.status_service.errorOccurred.disconnect()
                except Exception:
                    pass
        except Exception as exc:
            self.logMessage.emit(f"status_service cleanup warning: {exc}")

        old_controller = self.controller

        try:
            if old_controller is not None:
                try:
                    old_controller.connected.disconnect()
                except Exception:
                    pass
                try:
                    old_controller.disconnected.disconnect()
                except Exception:
                    pass
                try:
                    old_controller.errorOccurred.disconnect()
                except Exception:
                    pass
                try:
                    old_controller.serviceDiscovered.disconnect()
                except Exception:
                    pass
                try:
                    old_controller.discoveryFinished.disconnect()
                except Exception:
                    pass

                try:
                    old_controller.disconnectFromDevice()
                except Exception:
                    pass
        except Exception as exc:
            self.logMessage.emit(f"controller cleanup warning: {exc}")

        self.controller = None
        self._clear_runtime_handles()

        self._telemetry.setdefault("ble", {})["connected"] = False
        self._emit_telemetry()
        self.bleStatusChanged.emit("Disconnected" if user_initiated else "Link closed")
        self._is_disconnecting = False

    def _begin_pending_connect(self):

        if index is None:
            return
        if index < 0 or index >= len(self.device_infos):
            self.logMessage.emit("Pending connect index became invalid")
            return

        info = self.device_infos[index]

        try:
            name = info.name() or "Unnamed device"
        except Exception:
            name = "Unnamed device"

        try:
            address = info.address().toString()
        except Exception:
            address = "(no address)"

        self.logMessage.emit(f"Preparing controller for {name} @ {address}")

        self._disconnect_internal(user_initiated=False)

        self._conn_generation += 1
        gen = self._conn_generation

        self.controller = QLowEnergyController.createCentral(info, self)
        self.controller.connected.connect(self._safe(lambda: self._on_connected(gen)))
        self.controller.disconnected.connect(self._safe(lambda: self._on_disconnected(gen)))
        self.controller.errorOccurred.connect(self._safe(lambda err: self._on_ctrl_error(gen, err)))
        self.controller.serviceDiscovered.connect(
            self._safe(lambda uuid: self._on_service_discovered(gen, uuid))
        )
        self.controller.discoveryFinished.connect(
            self._safe(lambda: self._on_service_scan_finished(gen))
        )

        self.bleStatusChanged.emit("Connecting...")
        self.logMessage.emit("Calling controller.connectToDevice()")
        self.controller.connectToDevice()

    @pyqtSlot(int)
    def connectBle(self, index: int):
        if index < 0 or index >= len(self.device_infos):
            self.logMessage.emit("Invalid BLE device selection")
            return

        try:
            if self.agent.isActive():
                self.agent.stop()
        except Exception:
            pass

        info = self.device_infos[index]
        name = info.name() or "Unnamed device"
        self.logMessage.emit(f"Connecting to {name}")

        self._disconnect_internal(user_initiated=False)

        self._conn_generation += 1
        gen = self._conn_generation

        self.controller = QLowEnergyController.createCentral(info, self)
        self.controller.connected.connect(self._safe(lambda: self._on_connected(gen)))
        self.controller.disconnected.connect(self._safe(lambda: self._on_disconnected(gen)))
        self.controller.errorOccurred.connect(self._safe(lambda err: self._on_ctrl_error(gen, err)))
        self.controller.serviceDiscovered.connect(
            self._safe(lambda uuid: self._on_service_discovered(gen, uuid))
        )
        self.controller.discoveryFinished.connect(
            self._safe(lambda: self._on_service_scan_finished(gen))
        )

        self.bleStatusChanged.emit("Connecting...")
        self.controller.connectToDevice()

    def on_device_discovered(self, info: QBluetoothDeviceInfo):
        name = info.name() or ""
        try:
            address = info.address().toString()
        except Exception:
            address = "(no address)"

        try:
            core_cfg = int(info.coreConfigurations())
        except Exception:
            core_cfg = -1

        self.logMessage.emit(
            f"Scan hit: name={name or '<unnamed>'}, address={address}, core_cfg={core_cfg}, rssi={info.rssi()}"
        )

        try:
            is_le = bool(
                info.coreConfigurations()
                & QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration
            )
        except Exception:
            is_le = True

        if not is_le:
            self.logMessage.emit("Ignoring non-LE device")
            return

        if name.strip() and not any(tag in name.lower() for tag in ("zebrabot", "mp-rtos")):
            return

        self.devices.append(
            {
                "name": name or "Unnamed device",
                "address": address,
                "rssi": info.rssi(),
            }
        )
        self.device_infos.append(info)
        self._emit_devices()
        self.bleStatusChanged.emit(f"Found {len(self.devices)} matching device(s)")
    def on_scan_finished(self):
        self.bleStatusChanged.emit("Scan complete")
        self.logMessage.emit("BLE scan finished")

    def on_scan_error(self, error):
        self.bleStatusChanged.emit(f"Scan error: {error}")
        self.logMessage.emit(f"Scan error: {error}")

    def _on_connected(self, gen: int):
        if gen != self._conn_generation or self.controller is None:
            self.logMessage.emit("Ignoring stale connected callback")
            return
        self.logMessage.emit("Connected. Discovering services...")
        self.bleStatusChanged.emit("Connected. Discovering services...")
        self.controller.discoverServices()

    def _on_disconnected(self, gen: int):
        if gen != self._conn_generation:
            self.logMessage.emit("Ignoring stale disconnected callback")
            return
        self.logMessage.emit("Controller reported disconnect")
        self._telemetry.setdefault("ble", {})["connected"] = False
        self._emit_telemetry()
        self.bleStatusChanged.emit("Disconnected")

    def _on_ctrl_error(self, gen: int, err):
        if gen != self._conn_generation:
            self.logMessage.emit(f"Ignoring stale controller error: {err}")
            return
        self.logMessage.emit(f"BLE controller error: {err}")
        self.bleStatusChanged.emit(f"BLE error: {err}")

    def _on_service_discovered(self, gen: int, uuid):
        if gen != self._conn_generation:
            return
        u = uuid.toString().lower()
        self.discovered_services.add(u)
        self.logMessage.emit(f"Discovered service {u}")

    def _on_service_scan_finished(self, gen: int):
        if gen != self._conn_generation or self.controller is None:
            self.logMessage.emit("Ignoring stale service scan finished callback")
            return

        sys_uuid = self.SYS_SERVICE_UUID.toString().lower()
        nus_uuid = self.NUS_SERVICE_UUID.toString().lower()

        self.logMessage.emit(
            f"Service discovery finished. Found={sorted(self.discovered_services)}"
        )

        if sys_uuid in self.discovered_services:
            sys_svc = self.controller.createServiceObject(self.SYS_SERVICE_UUID, self)
            if sys_svc is not None:
                self.status_service = sys_svc
                sys_svc.stateChanged.connect(
                    self._safe(lambda state: self._on_sys_service_state(gen, state))
                )
                sys_svc.characteristicChanged.connect(
                    self._safe(lambda ch, value: self._on_sys_char_changed(gen, ch, value))
                )
                try:
                    sys_svc.errorOccurred.connect(
                        self._safe(lambda err: self._on_sys_service_error(gen, err))
                    )
                except Exception:
                    pass
                sys_svc.discoverDetails()
                self.logMessage.emit("SYS service object created")

        if nus_uuid in self.discovered_services:
            nus_svc = self.controller.createServiceObject(self.NUS_SERVICE_UUID, self)
            if nus_svc is not None:
                self.teleop_service = nus_svc
                nus_svc.stateChanged.connect(
                    self._safe(lambda state: self._on_nus_service_state(gen, state))
                )
                nus_svc.characteristicChanged.connect(
                    self._safe(lambda ch, value: self._on_nus_char_changed(gen, ch, value))
                )
                try:
                    nus_svc.errorOccurred.connect(
                        self._safe(lambda err: self._on_nus_service_error(gen, err))
                    )
                except Exception:
                    pass
                nus_svc.discoverDetails()
                self.logMessage.emit("NUS service object created")

        if self.status_service is None and self.teleop_service is None:
            self.bleStatusChanged.emit("No known robot services found")
            self.logMessage.emit("No known robot services found")

    def _on_sys_service_state(self, gen: int, state):
        if gen != self._conn_generation or self.status_service is None:
            self.logMessage.emit("Ignoring stale SYS service state callback")
            return

        if state != QLowEnergyService.ServiceState.ServiceDiscovered:
            return

        self.sys_host_char = self.status_service.characteristic(self.SYS_HOST_UUID)
        self.sys_status_char = self.status_service.characteristic(self.SYS_STATUS_UUID)
        self.sys_cmd_char = self.status_service.characteristic(self.SYS_CMD_UUID)

        self.logMessage.emit(
            "SYS chars: "
            f"host={self.sys_host_char.isValid() if self.sys_host_char else False}, "
            f"status={self.sys_status_char.isValid() if self.sys_status_char else False}, "
            f"cmd={self.sys_cmd_char.isValid() if self.sys_cmd_char else False}"
        )

        if self.sys_status_char and self.sys_status_char.isValid():
            desc = self.sys_status_char.descriptor(self.CCCD_UUID)
            if desc.isValid():
                self.logMessage.emit("Enabling SYS status notifications")
                self.status_service.writeDescriptor(desc, bytes([0x01, 0x00]))

        self._telemetry.setdefault("ble", {})["connected"] = True
        self._emit_telemetry()
        self.bleStatusChanged.emit("Connected to RTOS status service")

    def _on_nus_service_state(self, gen: int, state):
        if gen != self._conn_generation or self.teleop_service is None:
            self.logMessage.emit("Ignoring stale NUS service state callback")
            return

        if state != QLowEnergyService.ServiceState.ServiceDiscovered:
            return

        self.nus_tx_char = self.teleop_service.characteristic(self.NUS_TX_UUID)
        self.nus_rx_char = self.teleop_service.characteristic(self.NUS_RX_UUID)

        self.logMessage.emit(
            "NUS chars: "
            f"tx={self.nus_tx_char.isValid() if self.nus_tx_char else False}, "
            f"rx={self.nus_rx_char.isValid() if self.nus_rx_char else False}"
        )

        if self.nus_tx_char and self.nus_tx_char.isValid():
            desc = self.nus_tx_char.descriptor(self.CCCD_UUID)
            if desc.isValid():
                self.logMessage.emit("Enabling NUS TX notifications")
                self.teleop_service.writeDescriptor(desc, bytes([0x01, 0x00]))

        self.bleStatusChanged.emit("Connected to teleop service")

    def _on_sys_service_error(self, gen: int, err):
        if gen != self._conn_generation:
            return
        self.logMessage.emit(f"SYS service error: {err}")

    def _on_nus_service_error(self, gen: int, err):
        if gen != self._conn_generation:
            return
        self.logMessage.emit(f"NUS service error: {err}")

    def _on_sys_char_changed(self, gen: int, ch, value):
        if gen != self._conn_generation or self.status_service is None:
            return

        try:
            text = bytes(value).decode(errors="ignore")
        except Exception as exc:
            self.logMessage.emit(f"SYS decode error: {exc}")
            return

        self.logMessage.emit(f"SYS notify: {text[:240]}")

        try:
            data = json.loads(text)
        except Exception as exc:
            self.logMessage.emit(f"SYS parse error: {exc}")
            return

        if not isinstance(data, dict):
            self.logMessage.emit("SYS payload was not a JSON object")
            return

        if "ble" not in data:
            data["ble"] = {}
        data["ble"]["connected"] = True

        self._telemetry = data
        self._emit_telemetry()

    def _on_nus_char_changed(self, gen: int, ch, value):
        if gen != self._conn_generation or self.teleop_service is None:
            return

        try:
            text = bytes(value).decode(errors="ignore").strip()
        except Exception as exc:
            self.logMessage.emit(f"NUS decode error: {exc}")
            return

        if not text:
            return

        for line in text.splitlines():
            self._parse_robot_line(line.strip())

    def _parse_robot_line(self, line: str):
        if not line:
            return

        self.logMessage.emit(f"RX<- {line}")
        parts = line.split()
        if not parts:
            return

        tag = parts[0]

        if tag == "IMU" and len(parts) >= 8:
            try:
                self._imu = {
                    "ax": float(parts[1]),
                    "ay": float(parts[2]),
                    "az": float(parts[3]),
                    "gx": float(parts[4]),
                    "gy": float(parts[5]),
                    "gz": float(parts[6]),
                    "temp": float(parts[7]),
                }
                self._emit_imu()
            except Exception as exc:
                self.logMessage.emit(f"Bad IMU packet: {exc} :: {line}")
            return

        if tag == "SNS" and len(parts) >= 3:
            try:
                port = int(parts[1])
                if port in self._ports:
                    self._ports[port]["status"] = parts[2]
                    if parts[2] == "empty":
                        self._ports[port]["i2c"] = "--"
                        self._ports[port]["value"] = "--"
                    self._emit_port(port)
            except Exception as exc:
                self.logMessage.emit(f"Bad SNS packet: {exc} :: {line}")
            return

        if tag == "SNS_I2C" and len(parts) >= 3:
            try:
                port = int(parts[1])
                if port in self._ports:
                    self._ports[port]["i2c"] = " ".join(parts[2:])
                    self._emit_port(port)
            except Exception as exc:
                self.logMessage.emit(f"Bad SNS_I2C packet: {exc} :: {line}")
            return

        if tag == "SNS_TOF" and len(parts) >= 3:
            try:
                port = int(parts[1])
                if port in self._ports:
                    self._ports[port]["status"] = "VL53"
                    self._ports[port]["value"] = f"{parts[2]} mm"
                    self._emit_port(port)
            except Exception as exc:
                self.logMessage.emit(f"Bad SNS_TOF packet: {exc} :: {line}")
            return

        if tag == "SNS_COLOR" and len(parts) >= 6:
            try:
                port = int(parts[1])
                if port in self._ports:
                    self._ports[port]["status"] = "TCS3472"
                    self._ports[port]["value"] = (
                        f"R{parts[2]} G{parts[3]} B{parts[4]} C{parts[5]}"
                    )
                    self._emit_port(port)
            except Exception as exc:
                self.logMessage.emit(f"Bad SNS_COLOR packet: {exc} :: {line}")
            return

        if tag == "SNS_ERR" and len(parts) >= 3:
            try:
                port = int(parts[1])
                if port in self._ports:
                    self._ports[port]["status"] = "error"
                    self._ports[port]["value"] = " ".join(parts[2:])
                    self._emit_port(port)
            except Exception as exc:
                self.logMessage.emit(f"Bad SNS_ERR packet: {exc} :: {line}")
            return

    @pyqtSlot(int, int, int)
    def setDrive(self, throttle: int, turn: int, steer: int):
        self.drive_throttle = int(throttle)
        self.drive_turn = int(turn)
        self.drive_steer = int(steer)
        self.send_debounce.start()

    def _flush_drive(self):
        if self.nus_rx_char is None or self.teleop_service is None:
            return
        self.sendRobotLine(f"D {self.drive_throttle} {self.drive_turn}")
        self.sendRobotLine(f"S {self.drive_steer}")

    @pyqtSlot()
    def stopRobot(self):
        self.drive_throttle = 0
        self.drive_turn = 0
        self.sendRobotLine("STOP")
        self.sendRobotLine(f"S {self.drive_steer}")

    @pyqtSlot()
    def centerSteering(self):
        self.drive_steer = 90
        self.sendRobotLine("S 90")

    @pyqtSlot(str)
    def sendRobotLine(self, line: str):
        if not self.teleop_service or not self.nus_rx_char or not self.nus_rx_char.isValid():
            self.logMessage.emit("Write skipped: teleop RX characteristic not ready")
            return

        payload = (line.strip() + "\n").encode()
        try:
            self.teleop_service.writeCharacteristic(
                self.nus_rx_char,
                payload,
                QLowEnergyService.WriteMode.WriteWithoutResponse,
            )
            self.logMessage.emit(f"TX-> {line}")
        except Exception as exc:
            self.logMessage.emit(f"NUS write failed: {exc}")

    @pyqtSlot(str)
    def sendSysCommand(self, command: str):
        if not self.status_service or not self.sys_cmd_char or not self.sys_cmd_char.isValid():
            self.logMessage.emit("Write skipped: system command characteristic not ready")
            return

        payload = command.strip().encode()
        try:
            self.status_service.writeCharacteristic(
                self.sys_cmd_char,
                payload,
                QLowEnergyService.WriteMode.WriteWithoutResponse,
            )
            self.logMessage.emit(f"SYS-> {command}")
        except Exception as exc:
            self.logMessage.emit(f"SYS write failed: {exc}")