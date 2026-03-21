# robot/ble_teleop.py
import bluetooth
import uasyncio as asyncio
from micropython import const

from .config import BLE_NAME, SERVO_CENTER_DEG
from .error_report import exc_to_string, split_lines
from .debug_io import replay_boot_log

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX = (bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_NOTIFY)
_UART_RX = (bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_WRITE)
_UART_SERVICE = (_UART_UUID, (_UART_TX, _UART_RX))

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)


def _adv_payload(name: str):
    nb = name.encode()
    return b"\x02\x01\x06" + bytes((len(nb) + 1, 0x09)) + nb


class BleTeleop:
    def __init__(self, drive, steering, imu=None, imu_period_ms=10, oled=None):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)

        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((_UART_SERVICE,))
        self._conn_handle = None

        self.drive = drive
        self.steering = steering
        self.imu = imu
        self.imu_period_ms = int(imu_period_ms)
        self._imu_enabled = True
        self.oled = oled

        # Filled in by main.py after construction.
        self.motor_feedback = None
        self.motor_scanner = None
        self.motor_ports = ()
        self.motor_port_map = {}
        self._motor_fb_enabled = True

        self.steering.angle(SERVO_CENTER_DEG)

        self._payload = _adv_payload(BLE_NAME)
        self._advertise()

    def _advertise(self):
        self._conn_handle = None
        self._ble.gap_advertise(100_000, adv_data=self._payload)
        print("BLE advertising as:", BLE_NAME)
        if self.oled:
            try:
                self.oled.show_lines("ZebraBot", "Advertising")
            except Exception:
                pass

    def _irq(self, event, data):
        try:
            if event == _IRQ_CENTRAL_CONNECT:
                self._conn_handle, _, _ = data
                print("BLE connected")
                self.notify_info("BLE connected")

                replay_boot_log()

                if self.oled:
                    try:
                        self.oled.flash_connected()
                    except Exception as e:
                        self.notify_error("OLED_FLASH", e)

                self._emit_motor_config()
                if self._motor_fb_enabled:
                    self._emit_motor_snapshot()

            elif event == _IRQ_CENTRAL_DISCONNECT:
                print("BLE disconnected -> stopping robot")

                try:
                    self.drive.stop()
                except Exception as e:
                    self.notify_error("DRIVE_STOP", e)

                try:
                    self.steering.angle(SERVO_CENTER_DEG)
                except Exception as e:
                    self.notify_error("STEER_CENTER", e)

                if self.oled:
                    try:
                        self.oled.show_lines("ZebraBot", "Disconnected")
                    except Exception as e:
                        self.notify_error("OLED_DISCONNECT", e)

                self._advertise()

            elif event == _IRQ_GATTS_WRITE:
                conn_handle, value_handle = data
                if value_handle != self._rx_handle:
                    return

                raw = self._ble.gatts_read(self._rx_handle)
                try:
                    text = raw.decode().strip()
                except Exception as e:
                    self.notify_error("RX_DECODE", e)
                    return

                self._handle_cmd(text)

        except Exception as e:
            self.notify_error("IRQ", e)

    def _notify(self, text: str):
        if self._conn_handle is None:
            return
        try:
            self._ble.gatts_notify(self._conn_handle, self._tx_handle, text.encode() + b"\n")
        except Exception:
            pass

    def notify_line(self, text: str):
        self._notify(str(text))

    def notify_info(self, msg: str):
        for line in split_lines(str(msg), max_len=120):
            self._notify("INFO " + line)

    def notify_error(self, tag: str, exc):
        try:
            self._notify("ERR " + str(tag))
            for line in split_lines(exc_to_string(exc), max_len=120):
                self._notify("ERR " + line)
        except Exception:
            pass

    def _emit_motor_config(self):
        try:
            if not self.motor_port_map:
                self._notify("ERR MTR_CFG unavailable")
                return

            for port in sorted(self.motor_port_map.keys()):
                cfg = self.motor_port_map[port]
                name = cfg.get("name", "M{}".format(port))
                pwm = cfg.get("pwm", -1)
                direc = cfg.get("dir", -1)
                enc = cfg.get("enc", -1)
                self._notify(
                    "MTR_CFG {} {} PWM={} DIR={} ENC={}".format(
                        port, name, pwm, direc, enc
                    )
                )
        except Exception as e:
            self.notify_error("MTR_CFG", e)

    def _emit_motor_snapshot(self):
        try:
            if self.motor_feedback is None:
                self._notify("ERR MTR_FB unavailable")
                return

            ports = self.motor_ports
            if not ports and self.motor_port_map:
                ports = tuple(sorted(self.motor_port_map.keys()))

            for port in ports:
                ticks = self.motor_feedback.get(port)
                cfg = self.motor_port_map.get(port, {})
                name = cfg.get("name", "M{}".format(port))
                self._notify("MTR_FB {} {} {}".format(port, name, ticks))
        except Exception as e:
            self.notify_error("MTR_SNAPSHOT", e)

    def _handle_cmd(self, text: str):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                if line == "STOP":
                    self.drive.stop()
                    return

                if line == "IMU ON":
                    self._imu_enabled = True
                    self.notify_info("IMU enabled")
                    return

                if line == "IMU OFF":
                    self._imu_enabled = False
                    self.notify_info("IMU disabled")
                    return

                if line == "MTR_CFG":
                    self._emit_motor_config()
                    return

                if line == "MTR_STATE":
                    self._emit_motor_snapshot()
                    return

                if line == "MTR_SCAN ON":
                    if self.motor_scanner is None:
                        self._notify("ERR MTR_SCAN unavailable")
                        return
                    self.motor_scanner.enabled = True
                    self.notify_info("Motor scan enabled")
                    return

                if line == "MTR_SCAN OFF":
                    if self.motor_scanner is None:
                        self._notify("ERR MTR_SCAN unavailable")
                        return
                    self.motor_scanner.enabled = False
                    self.notify_info("Motor scan disabled")
                    return

                if line == "MTR_FB ON":
                    self._motor_fb_enabled = True
                    self.notify_info("Motor feedback enabled")
                    self._emit_motor_snapshot()
                    return

                if line == "MTR_FB OFF":
                    self._motor_fb_enabled = False
                    self.notify_info("Motor feedback disabled")
                    return

                if line.startswith("D "):
                    parts = line.split()
                    if len(parts) != 3:
                        self._notify("ERR CMD bad D format")
                        return
                    throttle = int(parts[1])
                    turn = int(parts[2])
                    self.drive.drive(throttle, turn)
                    return

                if line.startswith("S "):
                    parts = line.split()
                    if len(parts) != 2:
                        self._notify("ERR CMD bad S format")
                        return
                    ang = int(parts[1])
                    self.steering.angle(ang)
                    return

                self._notify("ERR CMD unknown: " + line)

            except Exception as e:
                self.notify_error("CMD", e)

    async def imu_task(self):
        if self.imu is None:
            self.notify_info("IMU task running without IMU")
            while True:
                await asyncio.sleep_ms(1000)

        self.notify_info("IMU task started")

        while True:
            try:
                if self._imu_enabled:
                    d = self.imu.read_scaled()
                    msg = (
                        "IMU "
                        "{:.3f} {:.3f} {:.3f} "
                        "{:.3f} {:.3f} {:.3f} "
                        "{:.2f}"
                    ).format(
                        d["ax_g"], d["ay_g"], d["az_g"],
                        d["gx_dps"], d["gy_dps"], d["gz_dps"],
                        d["temp_c"],
                    )
                    self._notify(msg)

            except Exception as e:
                self.notify_error("IMU_TASK", e)

            await asyncio.sleep_ms(self.imu_period_ms)