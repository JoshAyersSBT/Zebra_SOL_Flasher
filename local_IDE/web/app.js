/* global QWebChannel, qt */

(function () {
    "use strict";
  
    let api = null;
  
    const state = {
      devices: [],
      telemetry: {
        uptime_ms: null,
        mem_free: null,
        mem_alloc: null,
        load_pct: null,
        loop_lag_ms: null,
        i2c: { devices: [] },
        ble: { connected: false, conn_count: 0 },
      },
      imu: {},
      ports: {},
      selectedDeviceIndex: -1,
      firmwarePath: "",
      projectRoot: "",
      serialPort: "COM7",
      baudRate: 460800,
      frontendReady: false,
    };
  
    function $(id) {
      return document.getElementById(id);
    }
  
    function appendLog(line) {
      const box = $("logBox");
      if (!box) return;
      const ts = new Date().toLocaleTimeString();
      box.textContent += `[${ts}] ${line}\n`;
      box.scrollTop = box.scrollHeight;
    }
  
    function safeText(id, value) {
      const el = $(id);
      if (el) el.textContent = value;
    }
  
    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }
  
    function callApi(methodName) {
      const args = Array.prototype.slice.call(arguments, 1);
  
      if (!api) {
        appendLog(`API not ready: ${methodName}`);
        return;
      }
      if (typeof api[methodName] !== "function") {
        appendLog(`Missing api method: ${methodName}`);
        return;
      }
  
      appendLog(`JS -> api.${methodName}(${args.map(String).join(", ")})`);
      try {
        api[methodName].apply(api, args);
      } catch (err) {
        appendLog(`Call failed for ${methodName}: ${err}`);
      }
    }
  
    function parseJson(payload, fallback) {
      try {
        return JSON.parse(payload);
      } catch (err) {
        appendLog(`JSON parse error: ${err}`);
        return fallback;
      }
    }
  
    function formatValue(value, suffix) {
      if (value === null || value === undefined || value === "") return "--";
      return `${value}${suffix || ""}`;
    }
  
    function ensureDefaultPorts() {
      for (let i = 1; i <= 6; i += 1) {
        if (!state.ports[i]) {
          state.ports[i] = {
            port: i,
            status: "unknown",
            i2c: "--",
            value: "--",
          };
        }
      }
    }
  
    function renderDevices() {
      const list = $("deviceList");
      if (!list) return;
  
      list.innerHTML = "";
  
      if (!state.devices.length) {
        const empty = document.createElement("div");
        empty.className = "device-empty";
        empty.textContent = "No devices found.";
        list.appendChild(empty);
        return;
      }
  
      state.devices.forEach((dev, idx) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "device-item";
        if (idx === state.selectedDeviceIndex) item.classList.add("selected");
  
        item.innerHTML = `
          <div class="device-name">${escapeHtml(dev.name || "Unnamed device")}</div>
          <div class="device-meta">${escapeHtml(dev.address || "(unknown address)")}</div>
          <div class="device-meta">RSSI: ${escapeHtml(String(dev.rssi ?? "--"))}</div>
        `;
  
        item.addEventListener("click", function () {
          state.selectedDeviceIndex = idx;
          renderDevices();
          callApi("connectBle", idx);
        });
  
        list.appendChild(item);
      });
    }
  
    function renderTelemetry() {
      const t = state.telemetry || {};
      const ble = t.ble || {};
      const i2c = t.i2c || {};
  
      safeText("uptime", formatValue(t.uptime_ms, " ms"));
      safeText("memFree", formatValue(t.mem_free));
      safeText("memAlloc", formatValue(t.mem_alloc));
      safeText("loadPct", formatValue(t.load_pct, "%"));
      safeText("loopLag", formatValue(t.loop_lag_ms, " ms"));
      safeText(
        "i2cDevices",
        Array.isArray(i2c.devices) && i2c.devices.length ? i2c.devices.join(", ") : "(none)"
      );
      safeText("bleConnCount", formatValue(ble.conn_count ?? 0));
  
      const summary = $("connectionSummary");
      if (summary) {
        summary.textContent = ble.connected ? "BLE Connected" : "Disconnected";
        summary.dataset.connected = ble.connected ? "true" : "false";
      }
    }
  
    function renderImu() {
      const imu = state.imu || {};
      safeText("imu_ax", formatValue(imu.ax));
      safeText("imu_ay", formatValue(imu.ay));
      safeText("imu_az", formatValue(imu.az));
      safeText("imu_gx", formatValue(imu.gx));
      safeText("imu_gy", formatValue(imu.gy));
      safeText("imu_gz", formatValue(imu.gz));
      safeText("imu_temp", formatValue(imu.temp));
  
      window.dispatchEvent(new CustomEvent("zebra-imu-update", { detail: imu }));
    }
  
    function renderPorts() {
      const table = $("portsTable");
      if (!table) return;
  
      ensureDefaultPorts();
      table.innerHTML = "";
  
      ["Port", "Status", "I2C", "Value"].forEach(function (header) {
        const cell = document.createElement("div");
        cell.className = "ports-header";
        cell.textContent = header;
        table.appendChild(cell);
      });
  
      for (let i = 1; i <= 6; i += 1) {
        const p = state.ports[i];
        [String(i), p.status || "--", p.i2c || "--", p.value || "--"].forEach(function (value) {
          const cell = document.createElement("div");
          cell.className = "ports-cell";
          cell.textContent = value;
          table.appendChild(cell);
        });
      }
    }
  
    function renderPaths() {
      safeText("firmwarePath", state.firmwarePath || "No firmware selected");
      safeText("projectPath", state.projectRoot || "No project root selected");
  
      const portEl = $("serialPort");
      if (portEl && document.activeElement !== portEl) {
        portEl.value = String(state.serialPort || "");
      }
  
      const baudEl = $("baudRate");
      if (baudEl && document.activeElement !== baudEl) {
        baudEl.value = String(state.baudRate || "");
      }
    }
  
    function updateDriveLabels() {
      safeText("throttleValue", String(Number($("throttle")?.value || 0)));
      safeText("turnValue", String(Number($("turn")?.value || 0)));
      safeText("steerValue", String(Number($("steer")?.value || 90)));
    }
  
    function pushDriveState() {
      const throttle = Number($("throttle")?.value || 0);
      const turn = Number($("turn")?.value || 0);
      const steer = Number($("steer")?.value || 90);
      callApi("setDrive", throttle, turn, steer);
    }
  
    function bindClick(id, fn) {
      const el = $(id);
      if (!el) {
        appendLog(`Missing button: ${id}`);
        return;
      }
      el.addEventListener("click", fn);
    }
  
    function wireButtons() {
      bindClick("btnScanBle", function () { callApi("scanBle"); });
      bindClick("btnDisconnectBle", function () { callApi("disconnectBle"); });
      bindClick("btnStopRobot", function () { callApi("stopRobot"); });
      bindClick("btnCenterSteering", function () { callApi("centerSteering"); });
      bindClick("btnImuOn", function () { callApi("sendRobotLine", "IMU ON"); });
      bindClick("btnImuOff", function () { callApi("sendRobotLine", "IMU OFF"); });
      bindClick("btnPickFirmware", function () { callApi("pickFirmware"); });
      bindClick("btnPickProjectRoot", function () { callApi("pickProjectRoot"); });
      bindClick("btnFlashFirmware", function () { callApi("flashFirmware"); });
      bindClick("btnDeployProject", function () { callApi("deployProject"); });
      bindClick("btnFlashAndDeploy", function () { callApi("flashAndDeploy"); });
      bindClick("btnSendRaw", sendRawCommand);
      bindClick("btnClearLog", function () {
        const box = $("logBox");
        if (box) box.textContent = "";
      });
  
      ["throttle", "turn", "steer"].forEach(function (id) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", function () {
          updateDriveLabels();
          pushDriveState();
        });
      });
  
      const raw = $("rawCommand");
      if (raw) {
        raw.addEventListener("keydown", function (event) {
          if (event.key === "Enter") {
            event.preventDefault();
            sendRawCommand();
          }
        });
      }
  
      const port = $("serialPort");
      if (port) {
        port.addEventListener("change", function () {
          const val = String(port.value || "").trim();
          state.serialPort = val;
          callApi("setSerialPort", val);
        });
      }
  
      const baud = $("baudRate");
      if (baud) {
        baud.addEventListener("change", function () {
          const val = Number(baud.value || 0);
          if (Number.isFinite(val) && val > 0) {
            state.baudRate = val;
          }
          callApi("setBaudRate", val);
        });
      }
  
      window.addEventListener("keydown", function (event) {
        const target = event.target;
        const typing = target && (
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable
        );
        if (typing) return;
  
        if (event.code === "Space") {
          event.preventDefault();
          callApi("stopRobot");
        }
        if (event.key === "c" || event.key === "C") {
          event.preventDefault();
          callApi("centerSteering");
        }
      });
    }
  
    function wireSignals() {
      if (!api) return;
  
      api.logMessage.connect(function (msg) {
        appendLog(String(msg));
      });
  
      api.bleStatusChanged.connect(function (msg) {
        safeText("bleStatus", String(msg));
        appendLog(`BLE: ${msg}`);
      });
  
      api.devicesChanged.connect(function (payload) {
        state.devices = parseJson(payload, []);
        if (state.selectedDeviceIndex >= state.devices.length) {
          state.selectedDeviceIndex = -1;
        }
        renderDevices();
      });
  
      api.telemetryChanged.connect(function (payload) {
        state.telemetry = parseJson(payload, state.telemetry);
        renderTelemetry();
      });
  
      api.imuChanged.connect(function (payload) {
        state.imu = parseJson(payload, state.imu);
        renderImu();
      });
  
      api.portStateChanged.connect(function (payload) {
        const obj = parseJson(payload, null);
        if (obj && obj.port != null) {
          state.ports[obj.port] = obj;
          renderPorts();
        }
      });
  
      if (api.firmwarePathChanged) {
        api.firmwarePathChanged.connect(function (path) {
          state.firmwarePath = String(path || "");
          renderPaths();
        });
      }
  
      if (api.projectRootChanged) {
        api.projectRootChanged.connect(function (path) {
          state.projectRoot = String(path || "");
          renderPaths();
        });
      }
  
      if (api.serialPortChanged) {
        api.serialPortChanged.connect(function (port) {
          state.serialPort = String(port || "");
          renderPaths();
        });
      }
  
      if (api.baudRateChanged) {
        api.baudRateChanged.connect(function (baud) {
          state.baudRate = Number(baud || 0) || state.baudRate;
          renderPaths();
        });
      }
  
      if (api.frontendReadyChanged) {
        api.frontendReadyChanged.connect(function (ready) {
          state.frontendReady = Boolean(ready);
          appendLog(`Frontend ready changed: ${state.frontendReady}`);
        });
      }
    }
  
    function sendRawCommand() {
      const input = $("rawCommand");
      if (!input) return;
      const line = String(input.value || "").trim();
      if (!line) return;
      callApi("sendRobotLine", line);
      input.value = "";
    }
  
    function publishGlobalHooks() {
      window.zebraIde = window.zebraIde || {};
      window.zebraIde.api = api;
      window.zebraIde.state = state;
      window.zebraIde.appendLog = appendLog;
      window.zebraIde.renderDevices = renderDevices;
      window.zebraIde.renderTelemetry = renderTelemetry;
      window.zebraIde.renderImu = renderImu;
      window.zebraIde.renderPorts = renderPorts;
      window.zebraIde.renderPaths = renderPaths;
    }
  
    function initWebChannel() {
      if (typeof QWebChannel === "undefined") {
        appendLog("QWebChannel missing.");
        return;
      }
      if (typeof qt === "undefined" || !qt.webChannelTransport) {
        appendLog("qt.webChannelTransport missing.");
        return;
      }
  
      new QWebChannel(qt.webChannelTransport, function (channel) {
        api = channel.objects.api;
  
        if (!api) {
          appendLog("No api object registered from Python.");
          return;
        }
  
        appendLog("Qt bridge connected.");
        wireSignals();
        publishGlobalHooks();
  
        if (typeof api.syncFrontend === "function") {
          callApi("syncFrontend");
        } else {
          appendLog("api.syncFrontend() not found.");
        }
  
        if (typeof api.notifyPageLoaded === "function") {
          callApi("notifyPageLoaded");
        } else {
          appendLog("api.notifyPageLoaded() not found.");
        }
      });
    }
  
    document.addEventListener("DOMContentLoaded", function () {
      ensureDefaultPorts();
      renderDevices();
      renderTelemetry();
      renderImu();
      renderPorts();
      renderPaths();
      updateDriveLabels();
      wireButtons();
      publishGlobalHooks();
      initWebChannel();
    });
  })();