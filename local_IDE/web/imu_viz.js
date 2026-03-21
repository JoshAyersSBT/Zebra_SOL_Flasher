/* global THREE */

(function () {
    "use strict";
  
    if (typeof THREE === "undefined") {
      console.warn("Three.js unavailable; IMU visualization disabled.");
      return;
    }
  
    const canvas = document.getElementById("imuCanvas");
    if (!canvas) return;
  
    const renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      alpha: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  
    const scene = new THREE.Scene();
  
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.set(3.5, 2.5, 4.5);
  
    const ambient = new THREE.AmbientLight(0xffffff, 1.2);
    scene.add(ambient);
  
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.1);
    dirLight.position.set(4, 6, 3);
    scene.add(dirLight);
  
    const grid = new THREE.GridHelper(8, 16, 0x35506b, 0x223140);
    grid.position.y = -1.2;
    scene.add(grid);
  
    const axes = new THREE.AxesHelper(2.0);
    scene.add(axes);
  
    const accelArrow = new THREE.ArrowHelper(
      new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(0, 0, 0),
      1.8,
      0x63b3ff,
      0.35,
      0.2
    );
    scene.add(accelArrow);
  
    const gyroArrow = new THREE.ArrowHelper(
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 0, 0),
      1.4,
      0x59d18c,
      0.3,
      0.16
    );
    scene.add(gyroArrow);
  
    const originSphere = new THREE.Mesh(
      new THREE.SphereGeometry(0.08, 18, 18),
      new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x223344 })
    );
    scene.add(originSphere);
  
    const smoothAccel = new THREE.Vector3(0, 1, 0);
    const targetAccel = new THREE.Vector3(0, 1, 0);
    const smoothGyro = new THREE.Vector3(1, 0, 0);
    const targetGyro = new THREE.Vector3(1, 0, 0);
  
    function normalizeVector(x, y, z, fallback) {
      const v = new THREE.Vector3(x, y, z);
      if (v.lengthSq() < 1e-8) return fallback.clone();
      return v.normalize();
    }
  
    function resize() {
      const host = canvas.parentElement;
      if (!host) return;
      const width = Math.max(host.clientWidth, 100);
      const height = Math.max(host.clientHeight, 240);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
  
    function animate() {
      requestAnimationFrame(animate);
  
      smoothAccel.lerp(targetAccel, 0.12);
      smoothGyro.lerp(targetGyro, 0.12);
  
      accelArrow.setDirection(smoothAccel.clone().normalize());
      gyroArrow.setDirection(smoothGyro.clone().normalize());
  
      renderer.render(scene, camera);
    }
  
    window.addEventListener("zebra-imu-update", function (event) {
      const imu = event.detail || {};
  
      const ax = Number(imu.ax ?? 0);
      const ay = Number(imu.ay ?? 1);
      const az = Number(imu.az ?? 0);
  
      const gx = Number(imu.gx ?? 1);
      const gy = Number(imu.gy ?? 0);
      const gz = Number(imu.gz ?? 0);
  
      targetAccel.copy(normalizeVector(ax, ay, az, new THREE.Vector3(0, 1, 0)));
      targetGyro.copy(normalizeVector(gx, gy, gz, new THREE.Vector3(1, 0, 0)));
  
      const accelLen = 1.2 + Math.min(1.8, Math.abs(az) * 0.06);
      const gyroLen = 1.0 + Math.min(1.2, Math.abs(gz) * 0.03);
  
      accelArrow.setLength(accelLen, 0.35, 0.2);
      gyroArrow.setLength(gyroLen, 0.3, 0.16);
    });
  
    window.addEventListener("resize", resize);
    resize();
    animate();
  })();