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
    density: 0.00009, // 每平方像素的粒子数
    maxParticles: 110, // 上限，避免大屏卡顿
    minParticles: 26,
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

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
  }

  /* ------------------------------------------------------------ 粒子构建 */
  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  function makeParticle() {
    const color = CFG.palette[Math.floor(Math.random() * CFG.palette.length)];
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
      // 明灭相位与速率，模拟萤火虫闪烁
      phase: rand(0, Math.PI * 2),
      phaseSpeed: rand(0.008, 0.026),
      alpha: 0,
      // 漂移扰动
      wobble: rand(0, Math.PI * 2),
      wobbleSpeed: rand(0.004, 0.012),
    };
  }

  function buildParticles() {
    const target = Math.max(
      CFG.minParticles,
      Math.min(CFG.maxParticles, Math.floor(width * height * CFG.density))
    );
    particles = [];
    for (let i = 0; i < target; i++) particles.push(makeParticle());
  }

  /* ------------------------------------------------------------ 更新 */
  function update() {
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];

      // 飘浮漂移
      p.wobble += p.wobbleSpeed;
      p.x += p.vx + Math.cos(p.wobble) * 0.16;
      p.y += p.vy + Math.sin(p.wobble * 0.7) * 0.11;

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
          p.x += (dx / dist) * force;
          p.y += (dy / dist) * force;
        }
      }

      // 明灭
      p.phase += p.phaseSpeed;
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
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius * 5.2);
        g.addColorStop(0, 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (alpha * 0.5).toFixed(3) + ')');
        g.addColorStop(0.45, 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (alpha * 0.14).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',0)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius * 5.2, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.fillStyle =
        'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + Math.min(1, alpha + 0.3).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /* ------------------------------------------------------------ 主循环 */
  function loop() {
    update();
    draw();
    rafId = window.requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    running = true;
    loop();
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
  let enabled = true;

  const FireflyParticles = {
    enable: function () {
      enabled = true;
      canvas.style.display = '';
      if (!running) start();
    },
    disable: function () {
      enabled = false;
      stop();
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

  if (prefersReducedMotion) {
    // 尊重系统偏好：只静态绘制一帧
    draw();
    enabled = false;
  } else {
    start();
  }
})();
