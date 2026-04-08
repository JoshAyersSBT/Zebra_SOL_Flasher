# robot/ble_teleop.py
import os
import sys
import bluetooth
import uasyncio as asyncio
import ubinascii
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


def _append_adv_field(payload, adv_type, value):
    payload += bytes((len(value) + 1, adv_type)) + value
    return payload


def _uuid_bytes(uuid_obj):
    try:
        return bytes(uuid_obj)
    except Exception:
        s = str(uuid_obj).replace("-", "")
        if len(s) == 32:
            out = bytearray()
            for i in range(0, 32, 2):
                out.append(int(s[i:i + 2], 16))
            return bytes(out)
        return b""


def _adv_payload(name=None, services=None):
    payload = bytearray()
    payload = _append_adv_field(payload, 0x01, b"\x06")

    if name:
        nb = name.encode()
        if len(nb) > 16:
            payload = _append_adv_field(payload, 0x08, nb[:16])
        else:
            payload = _append_adv_field(payload, 0x09, nb)

    if services:
        svc128 = bytearray()
        for svc in services:
            b = _uuid_bytes(svc)
            if len(b) == 16:
                svc128 += b
        if svc128:
            payload = _append_adv_field(payload, 0x07, svc128)

    return bytes(payload)


def _dirname(path):
    i = path.rfind("/")
    if i <= 0:
        return "/" if path.startswith("/") else ""
    return path[:i]


def _mkdirs(path):
    if not path or path == "/":
        return
    cur = "/" if path.startswith("/") else ""
    for part in path.split("/"):
        if not part:
            continue
        if cur in ("", "/"):
            cur = cur + part if cur == "/" else part
        else:
            cur = cur + "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


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

        self.motor_feedback = None
        self.motor_scanner = None
        self.motor_ports = ()
        self.motor_port_map = {}
        self._motor_fb_enabled = True

        self._rx_buf = b""
        self._max_rx = 4096
        self._connected_flag = False
        self._disconnected_flag = False

        self._upload_path = None
        self._upload_tmp = None
        self._upload_fp = None

        self._oled_msg_task = None

        self._tx_queue = []
        self._tx_queue_max = 80
        self._tx_drop_count = 0

        # BLE should not interrupt user code unless BLE has actively taken
        # control of motion and stop_on_disconnect is enabled.
        self.stop_on_disconnect = False
        self._ble_motion_active = False

        # Important for file upload:
        # allow longer BLE writes to accumulate instead of truncating.
        try:
            self._ble.gatts_set_buffer(self._rx_handle, self._max_rx, True)
        except Exception as e:
            print("BLE RX buffer setup failed:", e)

        # Optional: seed TX handle with a small buffer too.
        try:
            self._ble.gatts_set_buffer(self._tx_handle, 512, False)
        except Exception:
            pass

        self.steering.angle(SERVO_CENTER_DEG)

        self._adv_data = _adv_payload(BLE_NAME, services=[_UART_UUID])
        self._advertise()

        try:
            asyncio.create_task(self._housekeeping())
            asyncio.create_task(self._tx_task())
        except Exception as e:
            print("BLE task start failed:", e)

    async def imu_task(self):
        """
        Periodically read the MPU6050 and publish IMU packets over both
        serial and BLE using the existing notifier path.

        Packet format matches what the desktop teleop parser expects:
            IMU ax ay az gx gy gz temp
        """
        if self.imu is None:
            self.notify_info("IMU task disabled: no IMU configured")
            return

        self.notify_info("IMU task started")

        while True:
            try:
                if self._imu_enabled:
                    d = self.imu.read_scaled()
                    self.notify_line(
                        "IMU {:.3f} {:.3f} {:.3f} {:.3f} {:.3f} {:.3f} {:.2f}".format(
                            d["ax_g"],
                            d["ay_g"],
                            d["az_g"],
                            d["gx_dps"],
                            d["gy_dps"],
                            d["gz_dps"],
                            d["temp_c"],
                        )
                    )
            except Exception as e:
                self.notify_error("IMU_TASK", e)

            await asyncio.sleep_ms(self.imu_period_ms)

    def _advertise(self):
        self._conn_handle = None
        try:
            # MicroPython commonly expects advertising interval in microseconds.
            self._ble.gap_advertise(100_000, adv_data=self._adv_data)
            print("BLE advertising as:", BLE_NAME)
        except Exception as e:
            print("BLE advertise failed:", e)

    def _cancel_oled_msg(self):
        try:
            if self._oled_msg_task is not None:
                self._oled_msg_task.cancel()
        except Exception:
            pass
        self._oled_msg_task = None

    def _oled_temp_message(self, *lines, hold_ms=800, clear_after=True):
        if not self.oled:
            return
        self._cancel_oled_msg()
        try:
            self._oled_msg_task = asyncio.create_task(
                self._oled_temp_message_task(lines, hold_ms, clear_after)
            )
        except Exception as e:
            self.notify_error("OLED_TEMP", e)

    async def _oled_temp_message_task(self, lines, hold_ms, clear_after):
        try:
            if self.oled:
                self.oled.show_lines(*lines)
                await asyncio.sleep_ms(int(hold_ms))
                if clear_after:
                    self.oled.clear()
        except Exception as e:
            self.notify_error("OLED_TEMP_TASK", e)

    async def _housekeeping(self):
        while True:
            try:
                if self._connected_flag:
                    self._connected_flag = False
                    self._on_connected()

                if self._disconnected_flag:
                    self._disconnected_flag = False
                    self._on_disconnected()
            except Exception as e:
                self.notify_error("HOUSEKEEP", e)

            await asyncio.sleep_ms(50)

    async def _tx_task(self):
        while True:
            try:
                if self._conn_handle is None or not self._tx_queue:
                    await asyncio.sleep_ms(20)
                    continue

                text = self._tx_queue.pop(0)
                try:
                    self._ble.gatts_notify(self._conn_handle, self._tx_handle, text.encode() + b"\n")
                except Exception:
                    if self._conn_handle is not None:
                        self._tx_queue.insert(0, text)
                    await asyncio.sleep_ms(50)
                    continue

                await asyncio.sleep_ms(12)
            except Exception as e:
                try:
                    print("BLE TX task error:", repr(e))
                except Exception:
                    pass
                await asyncio.sleep_ms(50)

    def _queue_notify(self, text: str):
        try:
            text = str(text)
        except Exception:
            text = "<notify encode error>"

        if len(self._tx_queue) >= self._tx_queue_max:
            self._tx_drop_count += 1
            self._tx_queue.pop(0)

        self._tx_queue.append(text)

    def _serial_write(self, text: str):
        try:
            print(str(text))
        except Exception:
            pass

    def _serial_exception(self, tag: str, exc):
        try:
            self._serial_write("ERR " + str(tag))
            if isinstance(exc, BaseException):
                sys.print_exception(exc)
            else:
                self._serial_write(repr(exc))
        except Exception as inner_exc:
            try:
                self._serial_write("ERR {} {}".format(tag, repr(exc)))
                self._serial_write("ERR SERIAL_EXC {}".format(repr(inner_exc)))
            except Exception:
                pass

    def _broadcast_line(self, text: str):
        try:
            text = str(text)
        except Exception:
            text = "<broadcast stringify error>"
        self._serial_write(text)
        self._notify(text)

    def _on_connected(self):
        print("BLE connected")
        self.notify_info("BLE connected")

        # Do not replay the boot log on BLE connect. Replaying old boot lines
        # like "Starting BLE" makes it look like the supervisor restarted and
        # can interfere with the OLED/user experience.
        if self._tx_drop_count:
            self.notify_line("WARN TX dropped {}".format(self._tx_drop_count))
            self._tx_drop_count = 0

        if self.oled:
            try:
                self.oled.flash_connected()
                try:
                    asyncio.create_task(self._clear_oled_after_connect())
                except Exception as e:
                    self.notify_error("OLED_CLEAR_SCHED", e)
            except Exception as e:
                self.notify_error("OLED_FLASH", e)

        self._emit_motor_config()
        if self._motor_fb_enabled:
            self._emit_motor_snapshot()

    async def _clear_oled_after_connect(self):
        try:
            await asyncio.sleep_ms(1800)
            if self.oled:
                self.oled.clear()
        except Exception as e:
            self.notify_error("OLED_CLEAR", e)

    def _on_disconnected(self):
        print("BLE disconnected")
        self._abort_upload(silent=True)

        # Only stop motion on disconnect if BLE was actively commanding motion
        # and the caller explicitly opted into that behavior.
        if self.stop_on_disconnect and self._ble_motion_active:
            try:
                self.drive.stop()
            except Exception as e:
                self.notify_error("DRIVE_STOP", e)

            try:
                self.steering.angle(SERVO_CENTER_DEG)
            except Exception as e:
                self.notify_error("STEER_CENTER", e)

        if self.oled:
            self._oled_temp_message("ZebraBot", "Disconnected", hold_ms=700, clear_after=True)

        self._ble_motion_active = False
        self._tx_queue = []
        self._rx_buf = b""
        self._advertise()

    def _irq(self, event, data):
        try:
            if event == _IRQ_CENTRAL_CONNECT:
                self._conn_handle, _, _ = data
                self._connected_flag = True

            elif event == _IRQ_CENTRAL_DISCONNECT:
                self._conn_handle = None
                self._disconnected_flag = True

            elif event == _IRQ_GATTS_WRITE:
                conn_handle, value_handle = data
                if value_handle != self._rx_handle:
                    return

                raw = self._ble.gatts_read(self._rx_handle)
                if not raw:
                    return

                self._rx_buf += raw
                if len(self._rx_buf) > self._max_rx:
                    self._rx_buf = self._rx_buf[-self._max_rx:]

                self._drain_rx_lines()

        except Exception as e:
            self.notify_error("IRQ", e)

    def _drain_rx_lines(self):
        while b"\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line:
                continue
            try:
                self._handle_cmd(line.decode())
            except Exception as e:
                self.notify_error("RX_CMD", e)

    def _notify(self, text: str):
        if self._conn_handle is None:
            return
        self._queue_notify(text)

    def notify_line(self, text: str):
        self._broadcast_line(str(text))

    def notify_info(self, msg: str):
        for line in split_lines(str(msg), max_len=120):
            self._broadcast_line("INFO " + line)

    def notify_error(self, tag: str, exc):
        self._serial_exception(tag, exc)

        try:
            self._notify("ERR " + str(tag))

            if isinstance(exc, BaseException):
                msg = exc_to_string(exc)
            else:
                msg = repr(exc)

            for line in split_lines(str(msg), max_len=120):
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
                    "MTR_CFG {} {} PWM={} DIR={} ENC={}".format(port, name, pwm, direc, enc)
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

    def _begin_upload(self, path_b64: str):
        self._abort_upload(silent=True)
        try:
            path = ubinascii.a2b_base64(path_b64).decode().strip()
        except Exception as e:
            self.notify_error("PUT_BEGIN_DECODE", e)
            self._notify("PUT_ERR bad path")
            return

        if not path:
            self._notify("PUT_ERR empty path")
            return

        if not path.startswith("/"):
            path = "/" + path

        self._upload_path = path
        self._upload_tmp = path + ".part"

        try:
            _mkdirs(_dirname(path))
            try:
                os.remove(self._upload_tmp)
            except OSError:
                pass

            self._upload_fp = open(self._upload_tmp, "wb")
            self._notify("PUT_OK BEGIN")
        except Exception as e:
            self._upload_fp = None
            self.notify_error("PUT_BEGIN", e)
            self._notify("PUT_ERR begin")

    def _chunk_upload(self, data_b64: str):
        if self._upload_fp is None:
            self._notify("PUT_ERR no active upload")
            return

        try:
            data = ubinascii.a2b_base64(data_b64)
            self._upload_fp.write(data)
            self._notify("PUT_OK CHUNK {}".format(len(data)))
        except Exception as e:
            self.notify_error("PUT_CHUNK", e)
            self._notify("PUT_ERR chunk")
            self._abort_upload(silent=True)

    def _end_upload(self):
        if self._upload_fp is None or self._upload_path is None or self._upload_tmp is None:
            self._notify("PUT_ERR no active upload")
            return

        try:
            self._upload_fp.close()
            self._upload_fp = None

            try:
                os.remove(self._upload_path)
            except OSError:
                pass

            os.rename(self._upload_tmp, self._upload_path)
            self._notify("PUT_OK END")
            self.notify_info("Uploaded {}".format(self._upload_path))

        except Exception as e:
            self.notify_error("PUT_END", e)
            self._notify("PUT_ERR end")
            self._abort_upload(silent=True)

        finally:
            self._upload_path = None
            self._upload_tmp = None

    def _abort_upload(self, silent=False):
        try:
            if self._upload_fp is not None:
                try:
                    self._upload_fp.close()
                except Exception:
                    pass

            if self._upload_tmp:
                try:
                    os.remove(self._upload_tmp)
                except OSError:
                    pass

        finally:
            self._upload_fp = None
            self._upload_path = None
            self._upload_tmp = None

        if not silent:
            self._notify("PUT_OK ABORT")

    def _handle_cmd(self, text: str):
        line = text.strip()
        if not line:
            return

        try:
            if line == "PING":
                self._notify("PONG")
                return

            if line == "STOP":
                self._ble_motion_active = True
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
                self._ble_motion_active = True
                self.drive.drive(throttle, turn)
                return

            if line.startswith("S "):
                parts = line.split()
                if len(parts) != 2:
                    self._notify("ERR CMD bad S format")
                    return
                ang = int(parts[1])
                self._ble_motion_active = True
                self.steering.angle(ang)
                return

            if line.startswith("PUT_BEGIN "):
                self._begin_upload(line[len("PUT_BEGIN "):].strip())
                return

            if line.startswith("PUT_CHUNK "):
                self._chunk_upload(line[len("PUT_CHUNK "):].strip())
                return

            if line == "PUT_END":
                self._end_upload()
                return

            if line == "PUT_ABORT":
                self._abort_upload(silent=False)
                return

            if line == "RESET":
                self._notify("INFO rebooting")
                try:
                    import machine
                    machine.reset()
                except Exception as e:
                    self.notify_error("RESET", e)
                return

            self._notify("ERR CMD unknown: " + line)

        except Exception as e:
            self.notify_error("CMD", e)