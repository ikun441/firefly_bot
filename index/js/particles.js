/* ==========================================================================
   流萤 Firefly · 萤火粒子背景
   --------------------------------------------------------------------------
   二次元夜空中的流萤：柔光点缓慢飘浮 + 呼吸明灭 + 相近粒子连线。
   使用 Canvas 2D 绘制；粒子数按视口面积自适应，移动端自动降级。
   ========================================================================== */

(function () {
  'use strict';

  const canvas = document.getElementById('firefly-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return;

  /* ------------------------------------------------------------ 配置 */
  const CFG = {
    density: 0.000045, // 每平方像素的粒子数
    maxParticles: 64, // 上限，避免大屏卡顿
    minParticles: 16,
    baseRadius: [0.9, 2.6],
    baseSpeed: 0.28, // 像素/帧（约 17px/s）
    linkDistance: 132, // 连线阈值
    linkOpacity: 0.13,
    glow: true,
    // 萤火青 / 樱粉 / 紫罗兰 / 天蓝
    palette: [
      [94, 234, 212],
      [244, 114, 182],
      [167, 139, 250],
      [56, 189, 248],
      [74, 222, 128],
    ],
  };

  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let enabled = localStorage.getItem('firefly-particles') !== 'off';
  let lastFrame = 0;
  // Cache five glow textures instead of creating radial gradients every frame.
  const glows = CFG.palette.map(function (color) {
    const sprite = document.createElement('canvas');
    sprite.width = sprite.height = 64;
    const brush = sprite.getContext('2d');
    const gradient = brush.createRadialGradient(32, 32, 0, 32, 32, 32);
    gradient.addColorStop(0, 'rgba(' + color.join(',') + ',.65)');
    gradient.addColorStop(.45, 'rgba(' + color.join(',') + ',.14)');
    gradient.addColorStop(1, 'rgba(' + color.join(',') + ',0)');
    brush.fillStyle = gradient;
    brush.fillRect(0, 0, 64, 64);
    return sprite;
  });

  let particles = [];
  let width = 0;
  let height = 0;
  let dpr = 1;
  let rafId = null;
  let running = false;
  const pointer = { x: -9999, y: -9999, active: false };

  /* ------------------------------------------------------------ 尺寸 */
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.clientWidth || window.innerWidth;
    height = canvas.clientHeight || window.innerHeight;

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    buildParticles();
    if (enabled) draw();
  }

  /* ------------------------------------------------------------ 粒子构建 */
  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  function makeParticle() {
    const colorIndex = Math.floor(Math.random() * CFG.palette.length);
    const color = CFG.palette[colorIndex];
    const radius = rand(CFG.baseRadius[0], CFG.baseRadius[1]);
    const angle = rand(0, Math.PI * 2);
    const speed = CFG.baseSpeed * rand(0.35, 1.25);

    return {
      x: rand(0, width),
      y: rand(0, height),
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      radius,
      color,
      colorIndex,
      // 明灭相位与速率，模拟萤火虫闪烁
      phase: rand(0, Math.PI * 2),
      phaseSpeed: rand(0.008, 0.026),
      alpha: .4,
      // 漂移扰动
      wobble: rand(0, Math.PI * 2),
      wobbleSpeed: rand(0.004, 0.012),
    };
  }

  function buildParticles() {
    const target = Math.max(
      CFG.minParticles,
      Math.min(width < 760 ? 26 : CFG.maxParticles, Math.floor(width * height * CFG.density))
    );
    particles = [];
    for (let i = 0; i < target; i++) particles.push(makeParticle());
  }

  /* ------------------------------------------------------------ 更新 */
  function update(delta) {
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];

      // 飘浮漂移
      p.wobble += p.wobbleSpeed * delta;
      p.x += (p.vx + Math.cos(p.wobble) * 0.16) * delta;
      p.y += (p.vy + Math.sin(p.wobble * 0.7) * 0.11) * delta;

      // 边界回绕（留出余量避免边缘突现）
      const margin = 24;
      if (p.x < -margin) p.x = width + margin;
      else if (p.x > width + margin) p.x = -margin;
      if (p.y < -margin) p.y = height + margin;
      else if (p.y > height + margin) p.y = -margin;

      // 指针轻推：靠近鼠标时被柔和排斥
      if (pointer.active) {
        const dx = p.x - pointer.x;
        const dy = p.y - pointer.y;
        const dist2 = dx * dx + dy * dy;
        const range = 118;
        if (dist2 < range * range && dist2 > 0.01) {
          const dist = Math.sqrt(dist2);
          const force = (1 - dist / range) * 0.5;
          p.x += (dx / dist) * force * delta;
          p.y += (dy / dist) * force * delta;
        }
      }

      // 明灭
      p.phase += p.phaseSpeed * delta;
      p.alpha = 0.34 + Math.sin(p.phase) * 0.3;
    }
  }

  /* ------------------------------------------------------------ 绘制 */
  function draw() {
    ctx.clearRect(0, 0, width, height);

    // 连线（先画，避免盖住粒子本体）
    ctx.lineWidth = 1;
    for (let i = 0; i < particles.length; i++) {
      const a = particles[i];
      for (let j = i + 1; j < particles.length; j++) {
        const b = particles[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 > CFG.linkDistance * CFG.linkDistance) continue;

        const dist = Math.sqrt(dist2);
        const opacity = (1 - dist / CFG.linkDistance) * CFG.linkOpacity * a.alpha;
        if (opacity <= 0.004) continue;

        ctx.strokeStyle =
          'rgba(' + a.color[0] + ',' + a.color[1] + ',' + a.color[2] + ',' + opacity.toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    // 粒子
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      const c = p.color;
      const alpha = Math.max(0, Math.min(1, p.alpha));

      if (CFG.glow) {
        const size = p.radius * 10.4;
        ctx.globalAlpha = alpha;
        ctx.drawImage(glows[p.colorIndex], p.x - size / 2, p.y - size / 2, size, size);
        ctx.globalAlpha = 1;
      }

      ctx.fillStyle =
        'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + Math.min(1, alpha + 0.3).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /* ------------------------------------------------------------ 主循环 */
  function loop(now) {
    if (!running) return;
    const interval = width < 760 ? 1000 / 24 : 1000 / 30;
    if (!lastFrame || now - lastFrame >= interval) {
      update(Math.min(lastFrame ? now - lastFrame : interval, 64) / (1000 / 60));
      draw();
      lastFrame = now;
    }
    rafId = window.requestAnimationFrame(loop);
  }

  function start() {
    if (running || !enabled || document.hidden || motionPreference.matches) return;
    running = true;
    lastFrame = 0;
    rafId = window.requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  /* ------------------------------------------------------------ 可见性优化 */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else if (enabled) start();
  });

  /* ------------------------------------------------------------ 交互 */
  window.addEventListener('pointermove', function (e) {
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    pointer.active = true;
  });

  window.addEventListener('pointerleave', function () {
    pointer.active = false;
  });

  /* ------------------------------------------------------------ 外部控制 */

  const FireflyParticles = {
    enable: function () {
      enabled = true;
      document.documentElement.dataset.particles = 'on';
      canvas.style.display = '';
      draw();
      start();
    },
    disable: function () {
      enabled = false;
      stop();
      document.documentElement.dataset.particles = 'off';
      canvas.style.display = 'none';
    },
    toggle: function () {
      if (enabled) FireflyParticles.disable();
      else FireflyParticles.enable();
      return enabled;
    },
    isEnabled: function () {
      return enabled;
    },
    rebuild: resize,
  };

  window.FireflyParticles = FireflyParticles;

  /* ------------------------------------------------------------ 启动 */
  let resizeTimer = null;
  window.addEventListener('resize', function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resize, 180);
  });

  resize();

  motionPreference.addEventListener('change', function () {
    stop();
    if (enabled) { draw(); start(); }
  });
  if (enabled) FireflyParticles.enable();
  else FireflyParticles.disable();
})();
