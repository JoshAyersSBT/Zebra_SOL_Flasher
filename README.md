# 🤖 MicroPython RTOS Robot Driver + Teleop Flasher

A modular **MicroPython-based RTOS framework for ESP32 robotics**, paired with a **desktop Teleop + Flasher application** for deployment, monitoring, and control.

---

## 📦 Overview

This project consists of two main components:

### 1. 🧠 Robot Driver Library (RTOS)
A lightweight cooperative RTOS for ESP32 that:
- Manages sensors, BLE, I2C, and system health
- Provides a structured API for user robot code
- Ensures system stability via watchdog + supervisor

### 2. 💻 Teleop Flasher Application
A desktop application that:
- Flashes firmware to ESP32
- Deploys robot code (serial or BLE)
- Provides real-time telemetry and control

---

## 🏗️ Architecture

```
robot/
├── boot.py
├── main.py
├── user_main.py
├── config.json
└── rtos/
    ├── main.py
    ├── sysapi.py
    ├── syscfg.py
    ├── sysstate.py
    ├── syscli.py
    └── services/
        ├── logger.py
        ├── watchdog.py
        ├── status_monitor.py
        ├── i2c_manager.py
        └── ble_manager.py
```

---

## ⚙️ Core Features

### 🔁 Supervisor + Fault Recovery
- Runs user code safely in a loop
- Automatically restarts on crash
- Prevents system lockups

### 📊 System Registry (Global State)
- Shared runtime state across all services
- Accessible globally via sysapi

### 🔌 I2C Sensor Management
- Automatic bus scanning
- Error recovery + restart
- Periodic updates

### 📡 BLE Control + Telemetry
- Custom GATT service
- Live JSON status streaming
- Command interface (reboot, scan, etc.)

### 🛡️ Watchdog Protection
- Only feeds if all systems are healthy
- Prevents system hangs

### 📈 Status Monitoring
- Memory usage
- Loop lag + CPU estimate
- Sensor and system status

### 🖥️ CLI Diagnostics
- Neofetch-style system summary
- Debug-friendly output

---

## 🚀 Getting Started

### 1. Flash MicroPython Firmware
```
esptool.py --chip esp32 erase_flash
esptool.py --chip esp32 write_flash 0x1000 firmware.bin
```

### 2. Deploy Robot Files
```
mpremote connect COMX
mpremote fs cp -r robot/ :
```

### 3. Run the System
boot.py runs automatically on startup.

### 4. Write Your Robot Logic
Edit user_main.py to control your robot.

---

## ⚙️ Configuration

Edit config.json to customize:
- BLE settings
- I2C pins
- Watchdog behavior
- System timing

---

# 💻 Teleop Flasher Application

## 🎯 Features

- USB Serial flashing (esptool)
- BLE device discovery + connection
- Project deployment (serial + BLE)
- Live telemetry panel
- Integrated code editor
- Installer + updater support

---

## 🛠️ Flashing Workflow

The Teleop Flasher application uses a guided workflow so the user can choose how they want to connect, flash, and deploy the robot project.

### 1. Choose a Flash / Deploy Method
From the Teleop window, the user selects the workflow they want to use:
- **Serial / USB**
- **BLE**
- **Firmware Flash**
- **Project Deploy**

This keeps firmware flashing separate from project deployment and avoids accidentally reflashing when the user only wants to update code.

### 2. Scan for Available Devices
Depending on the selected workflow, the Teleop app scans for:
- **Serial ports** for USB flashing and deployment
- **BLE devices** for wireless connection and upload

The user then selects the target device from the discovered list before continuing.

### 3. Configure the Action
After selecting the device, the app allows the user to confirm the action:
- **Flash firmware** to the ESP32
- **Deploy project files** to the robot
- **Connect for telemetry / control**

This matches the Teleop program behavior where the connection step happens first and the action is chosen explicitly.

### 4. Run the Job With Live Status Output
The Teleop app shows progress and logs while the action is running so the user can see:
- active port or BLE target
- current file being uploaded
- flash progress
- errors and tracebacks
- completion state

### 5. Verify Connection and Telemetry
After flashing or deploying, the app can connect to the robot and show:
- BLE connection state
- detected sensors
- telemetry updates
- error output if user code fails

### Firmware Flash
Typical serial firmware flashing in the Teleop workflow uses `esptool`:

```
python -m esptool --chip esp32 --port COMX erase_flash
python -m esptool --chip esp32 --port COMX --baud 460800 write_flash -z 0x1000 firmware.bin
```

### Project Deployment

#### Serial Deploy
Typical serial project deployment uses `mpremote` to copy the robot project to the device:

```
mpremote connect COMX fs mkdir :/robot
mpremote connect COMX fs cp -r robot/ :
```

#### BLE Deploy
In BLE mode, the Teleop program:
- scans for nearby robot devices
- connects to the selected robot
- uploads project files wirelessly
- reports progress file-by-file

---

## 📡 Telemetry Features

- Sensor detection panel
- Color + distance visualization
- BLE connection status
- Error + traceback output

---

## 🧪 Development Notes

- Supervisor prevents system crashes from user code
- BLE acts as control + telemetry channel
- I2C scanning is non-blocking
- Watchdog ensures recovery from failures

---

## 🔧 Future Improvements

- WiFi provisioning via BLE
- OTA updates
- Advanced sensor abstraction
- Motor auto-detection
- Web dashboard UI

---

## 🤝 Contributing

Contributions welcome:
- New services (SPI, UART, motors)
- UI improvements
- Additional drivers

---

## 📄 License

MIT License (recommended)

---

## 🙌 Acknowledgments

- MicroPython community
- ESP32 ecosystem
- BLE design inspiration
