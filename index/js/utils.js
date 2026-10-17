/* ==========================================================================
   流萤 Firefly · 通用工具层
   --------------------------------------------------------------------------
   DOM 操作、格式化、Toast、确认框、计数动画、打字机、轻量 Canvas 图表。
   全局挂载为 window.FF。
   ========================================================================== */

(function () {
  'use strict';

  /* ============================================================ DOM */
  const $ = function (selector, scope) {
    return (scope || document).querySelector(selector);
  };

  const $$ = function (selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  };

  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const countJobs = new WeakMap();
  const textJobs = new WeakMap();
  const chartEntries = new Map();
  const revealTargets = new Set();
  const revealObserver = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      revealObserver.unobserve(entry.target);
      revealTargets.delete(entry.target);
    });
  }, { threshold: .06, rootMargin: '0px 0px -16px 0px' }) : null;

  function observeReveal(root) {
    $$('.reveal:not(.is-visible)', root).forEach(function (node, i) {
      if (revealTargets.has(node)) return;
      if (!revealObserver || motionPreference.matches) { node.classList.add('is-visible'); return; }
      node.style.setProperty('--reveal-delay', Math.min(i * 35, 140) + 'ms');
      revealTargets.add(node);
      revealObserver.observe(node);
    });
  }

  function releaseVisuals(root) {
    revealTargets.forEach(function (node) {
      if (!root.contains(node)) return;
      revealObserver.unobserve(node);
      revealTargets.delete(node);
    });
    chartEntries.forEach(function (entry, parent) {
      if (!root.contains(entry.canvas)) return;
      if (chartObserver) chartObserver.unobserve(parent);
      chartEntries.delete(parent);
    });
  }

  let chartFrame = null;
  const chartObserver = 'ResizeObserver' in window ? new ResizeObserver(function (entries) {
    entries.forEach(function (item) {
      const entry = chartEntries.get(item.target);
      if (entry && item.contentRect.width > 0 && (Math.abs(entry.width - item.contentRect.width) > 1 || Math.abs(entry.height - item.contentRect.height) > 1)) entry.dirty = true;
    });
    if (chartFrame !== null) return;
    chartFrame = requestAnimationFrame(function () {
      chartFrame = null;
      chartEntries.forEach(function (entry) { if (entry.dirty) paintChart(entry); });
    });
  }) : null;

  function paintChart(entry) {
    if (!entry.canvas.isConnected || entry.canvas.closest('[hidden]')) return;
    entry.dirty = false;
    entry.draw.apply(null, [entry.canvas].concat(entry.args));
    entry.width = entry.canvas.parentNode.clientWidth;
    entry.height = entry.canvas.parentNode.clientHeight;
  }

  function responsiveChart(draw) {
    return function (canvas) {
      if (!canvas || !canvas.parentNode || !canvas.isConnected) return;
      const parent = canvas.parentNode;
      const entry = { canvas: canvas, draw: draw, args: Array.prototype.slice.call(arguments, 1), width: 0, height: 0 };
      chartEntries.set(parent, entry);
      paintChart(entry);
      if (chartObserver) chartObserver.observe(parent);
    };
  }

  function refreshView(root) {
    if (!root) return;
    observeReveal(root);
    requestAnimationFrame(function () {
      chartEntries.forEach(function (entry) {
        if (root.contains(entry.canvas)) paintChart(entry);
      });
    });
  }

  /**
   * 创建元素
   * @param {string} tag 标签名，支持 'div.cls#id'
   * @param {object} [attrs] 属性；class/className、text、html、style、dataset、on 事件
   * @param {(Node|string)[]} [children] 子节点
   */
  function el(tag, attrs, children) {
    const parts = String(tag).split(/([.#])/);
    const name = parts[0] || 'div';
    const node = document.createElement(name);

    let mode = null;
    for (let i = 1; i < parts.length; i += 2) {
      mode = parts[i];
      const value = parts[i + 1];
      if (!value) continue;
      if (mode === '.') node.classList.add(value);
      else if (mode === '#') node.id = value;
    }

    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        const value = attrs[key];
        if (value === undefined || value === null || value === false) return;

        if (key === 'text') {
          node.textContent = String(value);
        } else if (key === 'html') {
          node.innerHTML = value;
        } else if (key === 'class' || key === 'className') {
          String(value)
            .split(/\s+/)
            .filter(Boolean)
            .forEach(function (c) {
              node.classList.add(c);
            });
        } else if (key === 'style' && typeof value === 'object') {
          Object.keys(value).forEach(function (prop) {
            node.style.setProperty(prop.replace(/[A-Z]/g, function (m) {
              return '-' + m.toLowerCase();
            }), value[prop]);
          });
        } else if (key === 'dataset' && typeof value === 'object') {
          Object.keys(value).forEach(function (prop) {
            node.dataset[prop] = value[prop];
          });
        } else if (key === 'on' && typeof value === 'object') {
          Object.keys(value).forEach(function (evt) {
            if (typeof value[evt] === 'function') node.addEventListener(evt, value[evt]);
          });
        } else if (key in node && key !== 'list' && typeof value !== 'object') {
          try {
            node[key] = value;
          } catch (err) {
            node.setAttribute(key, String(value));
          }
        } else {
          node.setAttribute(key, String(value));
        }
      });
    }

    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (child) {
        if (child === undefined || child === null || child === false) return;
        node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
      });
    }

    return node;
  }

  function clear(node) {
    if (!node) return node;
    releaseVisuals(node);
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  /** 简易 HTML 转义，展示用户输入时使用 */
  function escapeHtml(text) {
    if (text === undefined || text === null) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function debounce(fn, wait) {
    let timer = null;
    return function () {
      const self = this;
      const args = arguments;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        fn.apply(self, args);
      }, wait || 220);
    };
  }

  function throttle(fn, wait) {
    let last = 0;
    return function () {
      const now = Date.now();
      if (now - last < (wait || 120)) return;
      last = now;
      fn.apply(this, arguments);
    };
  }

  /* ============================================================ 格式化 */
  const CN_NUM = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

  /**
   * 金额格式化
   * @param {number} value
   * @param {{symbol?:string, digits?:number, sign?:boolean, compact?:boolean}} [opts]
   */
  function formatMoney(value, opts) {
    const options = Object.assign(
      { symbol: '¥', digits: 2, sign: false, compact: false },
      opts || {}
    );
    let num = Number(value);
    if (!isFinite(num)) num = 0;

    const negative = num < 0;
    num = Math.abs(num);

    let body;
    if (options.compact && num >= 10000) {
      if (num >= 100000000) body = (num / 100000000).toFixed(2) + ' 亿';
      else body = (num / 10000).toFixed(2) + ' 万';
    } else {
      body = num.toLocaleString('zh-CN', {
        minimumFractionDigits: options.digits,
        maximumFractionDigits: options.digits,
      });
    }

    let prefix = options.symbol;
    if (negative) prefix = '-' + options.symbol;
    else if (options.sign) prefix = '+' + options.symbol;

    return prefix + body;
  }

  /** 数字千分位 */
  function formatNumber(value, digits) {
    const num = Number(value) || 0;
    return num.toLocaleString('zh-CN', {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits === undefined ? 2 : digits,
    });
  }

  /** 百分比 */
  function formatPercent(value, digits) {
    const num = Number(value) || 0;
    return num.toFixed(digits === undefined ? 1 : digits) + '%';
  }

  function pad2(num) {
    return String(num).padStart(2, '0');
  }

  /** 日期格式化：支持 Date / ISO 字符串 / 时间戳 */
  function formatDate(value, pattern) {
    if (!value) return '—';
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return String(value);

    const p = pattern || 'YYYY-MM-DD';
    return p
      .replace('YYYY', String(date.getFullYear()))
      .replace('MM', pad2(date.getMonth() + 1))
      .replace('DD', pad2(date.getDate()))
      .replace('HH', pad2(date.getHours()))
      .replace('mm', pad2(date.getMinutes()))
      .replace('ss', pad2(date.getSeconds()));
  }

  /** 相对时间：3 分钟前 */
  function relativeTime(value) {
    if (!value) return '—';
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return String(value);

    const diff = Date.now() - date.getTime();
    const abs = Math.abs(diff);
    const suffix = diff >= 0 ? '前' : '后';

    if (abs < 60000) return '刚刚';
    if (abs < 3600000) return Math.floor(abs / 60000) + ' 分钟' + suffix;
    if (abs < 86400000) return Math.floor(abs / 3600000) + ' 小时' + suffix;
    if (abs < 2592000000) return Math.floor(abs / 86400000) + ' 天' + suffix;
    return formatDate(date);
  }

  /** 周期标签 */
  function periodLabel(period, customDays) {
    const map = {
      daily: '日',
      weekly: '周',
      biweekly: '双周',
      monthly: '月',
      quarterly: '季度',
      yearly: '年度',
    };
    if (period === 'custom') return (customDays || 30) + ' 天自定义周期';
    return (map[period] || period || '月') + '度';
  }

  /** 生成周期区间 */
  function periodRange(period, refDate, customDays) {
    const end = refDate ? new Date(refDate) : new Date();
    end.setHours(23, 59, 59, 999);
    const start = new Date(end);

    switch (period) {
      case 'daily':
        start.setHours(0, 0, 0, 0);
        break;
      case 'weekly':
        start.setDate(end.getDate() - 6);
        start.setHours(0, 0, 0, 0);
        break;
      case 'biweekly':
        start.setDate(end.getDate() - 13);
        start.setHours(0, 0, 0, 0);
        break;
      case 'quarterly':
        start.setDate(end.getDate() - 89);
        start.setHours(0, 0, 0, 0);
        break;
      case 'yearly':
        start.setDate(end.getDate() - 364);
        start.setHours(0, 0, 0, 0);
        break;
      case 'custom':
        start.setDate(end.getDate() - (Number(customDays) || 30) + 1);
        start.setHours(0, 0, 0, 0);
        break;
      case 'monthly':
      default:
        start.setDate(end.getDate() - 29);
        start.setHours(0, 0, 0, 0);
        break;
    }

    return { start: start, end: end };
  }

  /** 变化率计算，返回 {value, text, tone} */
  function delta(current, previous) {
    const cur = Number(current) || 0;
    const prev = Number(previous) || 0;

    if (prev === 0) {
      if (cur === 0) return { value: 0, text: '持平', tone: 'flat' };
      return { value: null, text: '新增', tone: 'up' };
    }

    const rate = ((cur - prev) / Math.abs(prev)) * 100;
    const tone = Math.abs(rate) < 1 ? 'flat' : rate > 0 ? 'up' : 'down';
    const arrow = tone === 'flat' ? '' : rate > 0 ? '▲' : '▼';
    return {
      value: rate,
      text: arrow + ' ' + Math.abs(rate).toFixed(1) + '%',
      tone: tone,
    };
  }

  function uid(prefix) {
    return (prefix || 'id') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function sum(list, pick) {
    return list.reduce(function (acc, item) {
      return acc + (Number(pick ? pick(item) : item) || 0);
    }, 0);
  }

  function groupBy(list, keyFn) {
    return list.reduce(function (acc, item) {
      const key = keyFn(item);
      if (!acc[key]) acc[key] = [];
      acc[key].push(item);
      return acc;
    }, {});
  }

  function sortDesc(list, pick) {
    return list.slice().sort(function (a, b) {
      return (Number(pick(b)) || 0) - (Number(pick(a)) || 0);
    });
  }

  /* ============================================================ Toast */
  let toastContainer = null;

  function getToastContainer() {
    if (!toastContainer || !document.body.contains(toastContainer)) {
      toastContainer = el('div.toast-container#toast-container');
      document.body.appendChild(toastContainer);
    }
    return toastContainer;
  }

  const TOAST_ICON = {
    success: '✓',
    error: '✕',
    warning: '⚠',
    info: 'ℹ',
  };

  /**
   * 轻提示
   * @param {string} message
   * @param {'success'|'error'|'warning'|'info'} [type]
   * @param {number} [duration] 毫秒
   */
  function toast(message, type, duration) {
    const kind = type || 'info';
    const node = el('div.toast', { dataset: { type: kind } }, [
      el('span', { text: TOAST_ICON[kind] || 'ℹ' }),
      el('span', { text: String(message) }),
    ]);

    getToastContainer().appendChild(node);

    const timer = window.setTimeout(function () {
      node.classList.add('is-leaving');
      window.setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, 300);
    }, duration || 2600);

    node.addEventListener('click', function () {
      window.clearTimeout(timer);
      if (node.parentNode) node.parentNode.removeChild(node);
    });

    return node;
  }

  /**
   * 确认框（无原生 alert/confirm）
   * @returns {Promise<boolean>}
   */
  function confirmDialog(options) {
    const opts = Object.assign(
      { title: '确认操作', message: '', confirmText: '确定', cancelText: '取消', danger: false },
      options || {}
    );

    return new Promise(function (resolve) {
      const previousFocus = document.activeElement;
      const titleId = uid('confirm-title');
      const overlay = el('div', {
        style: {
          position: 'fixed',
          inset: '0',
          zIndex: '9998',
          display: 'grid',
          placeItems: 'center',
          background: 'rgba(5, 8, 16, 0.66)',
          backdropFilter: 'blur(6px)',
          padding: '20px',
        },
      });

      const box = el(
        'div.card.confirm-dialog',
        {
          role: 'dialog',
          'aria-modal': 'true',
          'aria-labelledby': titleId,
          style: {
            maxWidth: '430px',
            width: '100%',
          },
        },
        [
          el('h3', {
            id: titleId,
            text: opts.title,
            style: { fontSize: '17px', marginBottom: '12px' },
          }),
          el('p', {
            text: opts.message,
            style: { fontSize: '13.5px', color: 'var(--text-secondary)', lineHeight: '1.7', marginBottom: '22px' },
          }),
          el('div.btn-group', { style: { justifyContent: 'flex-end' } }, [
            el('button.btn', { text: opts.cancelText, on: { click: function () { close(false); } } }),
            el(
              'button.btn' + (opts.danger ? '.btn-danger' : '.btn-primary'),
              { text: opts.confirmText, on: { click: function () { close(true); } } }
            ),
          ]),
        ]
      );

      overlay.appendChild(box);

      function close(result) {
        document.removeEventListener('keydown', onKey);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        if (previousFocus && previousFocus.isConnected) previousFocus.focus();
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') close(false);
        if (e.key === 'Tab') {
          const buttons = box.querySelectorAll('button');
          if (e.shiftKey && document.activeElement === buttons[0]) { e.preventDefault(); buttons[buttons.length - 1].focus(); }
          else if (!e.shiftKey && document.activeElement === buttons[buttons.length - 1]) { e.preventDefault(); buttons[0].focus(); }
        }
      }

      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close(false);
      });
      document.addEventListener('keydown', onKey);

      document.body.appendChild(overlay);

      const confirmBtn = box.querySelector('.btn-primary, .btn-danger');
      if (confirmBtn) confirmBtn.focus();
    });
  }

  /* ============================================================ 数字滚动动画 */
  /**
   * 数字自增动画
   * @param {HTMLElement} node 目标元素
   * @param {number} target 目标值
   * @param {{duration?:number, digits?:number, prefix?:string, suffix?:string, compact?:boolean}} [opts]
   */
  function animateCount(node, target, opts) {
    if (!node) return Promise.resolve();
    if (countJobs.has(node)) countJobs.get(node)();
    const options = Object.assign(
      { duration: 1200, digits: 2, prefix: null, suffix: '', compact: false },
      opts || {}
    );
    // prefix 默认值取决于调用场景：带 compact 或显式传了 format 的通常是金额，
    // 沿用 '¥' 保持既有行为；否则按普通计数处理（不带货币符号）。
    // 早先 prefix 无条件默认 '¥'，导致「账本数 3 个」渲染成「¥3.00 个」——
    // 数值类指标被套上货币符号，且调用方很难想到要去覆盖一个没传过的参数。
    if (options.prefix === null) {
      options.prefix = options.compact ? '¥' : '';
    }

    const from = Number(node.dataset.value) || 0;
    const to = Number(target) || 0;
    node.dataset.value = String(to);

    // 尊重减少动效偏好
    function format(value) {
      return options.prefix + (options.compact && Math.abs(value) >= 10000
        ? formatMoney(value, { symbol: '', digits: 2, compact: true }).trim()
        : formatNumber(value, options.digits)) + options.suffix;
    }
    if (motionPreference.matches || options.duration <= 0) {
      node.textContent = format(to);
      return Promise.resolve();
    }

    return new Promise(function (resolve) {
      const startTime = performance.now();
      let frame = null;
      countJobs.set(node, function () { cancelAnimationFrame(frame); countJobs.delete(node); resolve(); });

      function step(now) {
        const finish = !node.isConnected || document.hidden || node.closest('.page-view[hidden]') || motionPreference.matches;
        const progress = finish ? 1 : clamp((now - startTime) / options.duration, 0, 1);
        // easeOutExpo，收尾更顺滑
        const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
        const current = from + (to - from) * eased;

        node.textContent = format(current);

        if (progress < 1) frame = window.requestAnimationFrame(step);
        else { countJobs.delete(node); resolve(); }
      }

      frame = window.requestAnimationFrame(step);
    });
  }

  /** 批量数字动画（同一帧启动，保证节奏一致） */
  function animateCounts(nodes, duration) {
    return Promise.all(
      nodes.map(function (item) {
        return animateCount(item.node, item.value, Object.assign({ duration: duration || 1200 }, item.opts));
      })
    );
  }

  /* ============================================================ 打字机 */
  /**
   * 逐字显示文本
   * @param {HTMLElement} node
   * @param {string} text
   * @param {{speed?:number, cursor?:boolean}} [opts]
   * @returns {Promise<void>}
   */
  function typewriter(node, text, opts) {
    if (!node) return Promise.resolve();
    if (textJobs.has(node)) textJobs.get(node)();
    const options = Object.assign({ speed: 18, cursor: true }, opts || {});
    const content = String(text || '');

    clear(node);

    if (motionPreference.matches || !content) {
      node.textContent = content;
      return Promise.resolve();
    }

    const textSpan = el('span');
    node.appendChild(textSpan);

    let cursor = null;
    if (options.cursor) {
      cursor = el('span.typewriter-cursor');
      node.appendChild(cursor);
    }

    return new Promise(function (resolve) {
      const startedAt = performance.now();
      const duration = Math.min(5000, content.length * Math.max(1, options.speed));
      let frame = null;
      function finish() {
        cancelAnimationFrame(frame);
        textSpan.textContent = content;
        if (cursor && cursor.parentNode) cursor.remove();
        textJobs.delete(node);
        resolve();
      }
      textJobs.set(node, finish);
      function step(now) {
        if (!node.isConnected || document.hidden || node.closest('.page-view[hidden]') || motionPreference.matches) { finish(); return; }
        const index = Math.ceil(content.length * Math.min(1, (now - startedAt) / duration));
        textSpan.textContent = content.slice(0, index);
        if (index >= content.length) finish();
        else frame = requestAnimationFrame(step);
      }
      frame = requestAnimationFrame(step);
    });
  }

  /* ============================================================ 图表（纯 Canvas） */

  function setupCanvas(canvas) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(canvas.parentNode.clientWidth || rect.width || 320, 1);
    const height = canvas.parentNode.clientHeight || 280;

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, width: width, height: height, dpr: dpr };
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return value && value.trim() ? value.trim() : fallback;
  }

  function roundRect(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + h - radius);
    ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    ctx.lineTo(x + radius, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  /**
   * 折线图
   * @param {HTMLCanvasElement} canvas
   * @param {{labels:string[], series:{name:string,color:string,values:number[],fill?:boolean}[], height?:number}} data
   */
  function drawLineChart(canvas, data) {
    if (!canvas) return;
    const env = setupCanvas(canvas);
    const ctx = env.ctx;
    const W = env.width;
    const H = env.height;

    const labels = data.labels || [];
    const series = data.series || [];
    if (!labels.length || !series.length) return;

    const padding = { top: 22, right: 20, bottom: 38, left: 62 };
    const plotW = W - padding.left - padding.right;
    const plotH = H - padding.top - padding.bottom;

    let maxValue = 0;
    series.forEach(function (s) {
      s.values.forEach(function (v) {
        maxValue = Math.max(maxValue, Number(v) || 0);
      });
    });
    maxValue = maxValue > 0 ? maxValue * 1.15 : 1;

    const xAt = function (i) {
      return padding.left + (labels.length === 1 ? plotW / 2 : (plotW * i) / (labels.length - 1));
    };
    const yAt = function (v) {
      return padding.top + plotH - (Math.max(0, Number(v) || 0) / maxValue) * plotH;
    };

    ctx.clearRect(0, 0, W, H);

    // 网格与 Y 轴刻度
    const gridLines = 4;
    ctx.strokeStyle = 'rgba(148,163,184,0.12)';
    ctx.fillStyle = cssVar('--text-muted', '#64748b');
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1;

    for (let i = 0; i <= gridLines; i++) {
      const value = (maxValue / gridLines) * i;
      const y = yAt(value);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
      ctx.fillText(formatNumber(value, value >= 10000 ? 0 : value >= 100 ? 0 : 1), padding.left - 9, y);
    }

    // X 轴标签
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const labelCount = Math.max(2, Math.floor(plotW / 64));
    const labelStep = Math.max(1, Math.ceil((labels.length - 1) / (labelCount - 1)));
    labels.forEach(function (label, i) {
      if (i % labelStep !== 0 && i !== labels.length - 1) return;
      if (i !== labels.length - 1 && i !== 0 && labels.length - 1 - i < labelStep * .75) return;
      ctx.fillText(String(label), xAt(i), padding.top + plotH + 11);
    });

    // 序列
    series.forEach(function (s) {
      const color = s.color || cssVar('--firefly', '#5eead4');
      const points = s.values.map(function (v, i) {
        return { x: xAt(i), y: yAt(v) };
      });

      if (s.fill !== false && series.length === 1) {
        const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
        gradient.addColorStop(0, hexToRgba(color, 0.28));
        gradient.addColorStop(1, hexToRgba(color, 0.01));
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.moveTo(points[0].x, padding.top + plotH);
        points.forEach(function (p) {
          ctx.lineTo(p.x, p.y);
        });
        ctx.lineTo(points[points.length - 1].x, padding.top + plotH);
        ctx.closePath();
        ctx.fill();
      }

      ctx.strokeStyle = color;
      ctx.lineWidth = 2.2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.beginPath();
      points.forEach(function (p, i) {
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();

      // 数据点
      points.forEach(function (p) {
        ctx.fillStyle = cssVar('--bg-base', '#0f1524');
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      });
    });
  }

  /**
   * 柱状图（支持分组）
   * @param {HTMLCanvasElement} canvas
   * @param {{labels:string[], series:{name:string,color:string,values:number[]}[], horizontal?:boolean}} data
   */
  function drawBarChart(canvas, data) {
    if (!canvas) return;
    const env = setupCanvas(canvas);
    const ctx = env.ctx;
    const W = env.width;
    const H = env.height;

    const labels = data.labels || [];
    const series = data.series || [];
    if (!labels.length || !series.length) return;

    const horizontal = !!data.horizontal;
    const padding = horizontal
      ? { top: 16, right: 62, bottom: 26, left: 96 }
      : { top: 20, right: 20, bottom: 42, left: 62 };
    const plotW = W - padding.left - padding.right;
    const plotH = H - padding.top - padding.bottom;

    let maxValue = 0;
    series.forEach(function (s) {
      s.values.forEach(function (v) {
        maxValue = Math.max(maxValue, Number(v) || 0);
      });
    });
    maxValue = maxValue > 0 ? maxValue * 1.12 : 1;

    ctx.clearRect(0, 0, W, H);
    ctx.font = '11px system-ui, sans-serif';

    const groupSize = horizontal ? plotH / labels.length : plotW / labels.length;
    const barCount = series.length;
    const barFull = groupSize * 0.64;
    const barWidth = barFull / barCount;

    // 网格
    ctx.strokeStyle = 'rgba(148,163,184,0.12)';
    ctx.lineWidth = 1;
    const gridLines = 4;
    for (let i = 0; i <= gridLines; i++) {
      const ratio = i / gridLines;
      ctx.beginPath();
      if (horizontal) {
        const x = padding.left + plotW * ratio;
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + plotH);
      } else {
        const y = padding.top + plotH * (1 - ratio);
        ctx.moveTo(padding.left, y);
        ctx.lineTo(padding.left + plotW, y);
      }
      ctx.stroke();
    }

    // 轴标签
    ctx.fillStyle = cssVar('--text-muted', '#64748b');
    if (horizontal) {
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      labels.forEach(function (label, i) {
        const y = padding.top + groupSize * i + groupSize / 2;
        ctx.fillText(truncate(String(label), 7), padding.left - 9, y);
      });
      ctx.textAlign = 'left';
      for (let i = 0; i <= gridLines; i++) {
        const value = (maxValue / gridLines) * i;
        ctx.fillText(
          formatNumber(value, 0),
          padding.left + plotW * (i / gridLines) + 6,
          padding.top + plotH
        );
      }
    } else {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      labels.forEach(function (label, i) {
        const x = padding.left + groupSize * i + groupSize / 2;
        ctx.fillText(truncate(String(label), 6), x, padding.top + plotH + 12);
      });
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let i = 0; i <= gridLines; i++) {
        const value = (maxValue / gridLines) * i;
        ctx.fillText(
          formatNumber(value, 0),
          padding.left - 9,
          padding.top + plotH * (1 - i / gridLines)
        );
      }
    }

    // 柱体
    series.forEach(function (s, si) {
      const color = s.color || cssVar('--firefly', '#5eead4');
      s.values.forEach(function (rawValue, i) {
        const value = Math.max(0, Number(rawValue) || 0);
        const ratio = value / maxValue;
        let x;
        let y;
        let w;
        let h;

        if (horizontal) {
          w = Math.max(1, plotW * ratio);
          h = Math.max(2, barWidth * 0.82);
          x = padding.left;
          y = padding.top + groupSize * i + (barFull - barWidth * barCount) / 2 + barWidth * si + (barWidth - h) / 2;
        } else {
          w = Math.max(2, barWidth * 0.82);
          h = Math.max(1, plotH * ratio);
          x = padding.left + groupSize * i + (groupSize - barFull) / 2 + barWidth * si + (barWidth - w) / 2;
          y = padding.top + plotH - h;
        }

        const gradient = horizontal
          ? ctx.createLinearGradient(x, 0, x + w, 0)
          : ctx.createLinearGradient(0, y, 0, y + h);
        gradient.addColorStop(0, hexToRgba(color, 0.95));
        gradient.addColorStop(1, hexToRgba(color, 0.55));

        ctx.fillStyle = gradient;
        roundRect(ctx, x, y, w, h, Math.min(5, w / 2, h / 2));
        ctx.fill();
      });
    });
  }

  /**
   * 环形图
   * @param {HTMLCanvasElement} canvas
   * @param {{value:number,color:string}[]} segments
   * @param {{centerLabel?:string, centerValue?:string, thickness?:number}} [opts]
   */
  function drawDonutChart(canvas, segments, opts) {
    if (!canvas) return;
    const options = Object.assign({ centerLabel: '', centerValue: '', thickness: 0.34 }, opts || {});
    const env = setupCanvas(canvas);
    const ctx = env.ctx;
    const W = env.width;
    const H = env.height;

    const cx = W / 2;
    const cy = H / 2;
    const radius = Math.min(W, H) / 2 - 12;
    const inner = radius * (1 - options.thickness);

    const total = segments.reduce(function (acc, s) {
      return acc + (Number(s.value) || 0);
    }, 0);

    ctx.clearRect(0, 0, W, H);

    if (total <= 0) {
      ctx.strokeStyle = 'rgba(148,163,184,0.16)';
      ctx.lineWidth = radius - inner;
      ctx.beginPath();
      ctx.arc(cx, cy, (radius + inner) / 2, 0, Math.PI * 2);
      ctx.stroke();
      return;
    }

    let angle = -Math.PI / 2;
    segments.forEach(function (seg) {
      const value = Number(seg.value) || 0;
      if (value <= 0) return;
      const sweep = (value / total) * Math.PI * 2;

      ctx.strokeStyle = seg.color || cssVar('--firefly', '#5eead4');
      ctx.lineWidth = radius - inner;
      ctx.lineCap = 'butt';
      ctx.beginPath();
      ctx.arc(cx, cy, (radius + inner) / 2, angle + 0.012, angle + sweep - 0.012);
      ctx.stroke();

      angle += sweep;
    });

    if (options.centerValue) {
      ctx.fillStyle = cssVar('--text-primary', '#e8eefc');
      ctx.font = '700 21px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(options.centerValue, cx, cy - 7);

      if (options.centerLabel) {
        ctx.fillStyle = cssVar('--text-muted', '#64748b');
        ctx.font = '11.5px system-ui, sans-serif';
        ctx.fillText(options.centerLabel, cx, cy + 15);
      }
    }
  }

  /**
   * 瀑布图（归因分析）
   * @param {HTMLCanvasElement} canvas
   * @param {{label:string, delta:number}[]} items
   * @param {{startLabel?:string, startValue?:number, endValue?:number}} opts
   */
  function drawWaterfallChart(canvas, items, opts) {
    if (!canvas) return;
    const options = Object.assign({ startLabel: '上期', startValue: 0, endValue: 0 }, opts || {});
    const env = setupCanvas(canvas);
    const ctx = env.ctx;
    const W = env.width;
    const H = env.height;

    const padding = { top: 24, right: 18, bottom: 46, left: 62 };
    const plotW = W - padding.left - padding.right;
    const plotH = H - padding.top - padding.bottom;
    const plotBottom = padding.top + plotH;

    const vertices = [options.startValue];
    let cursor = options.startValue;
    items.forEach(function (item) {
      cursor += Number(item.delta) || 0;
      vertices.push(cursor);
    });
    vertices.push(options.endValue || cursor);

    const maxValue = Math.max.apply(null, vertices.concat([0])) * 1.14 || 1;

    const columns = [
      { label: options.startLabel, from: 0, to: options.startValue, kind: 'total' },
    ];
    let running = options.startValue;
    items.forEach(function (item) {
      const delta = Number(item.delta) || 0;
      const next = running + delta;
      columns.push({
        label: item.label,
        from: Math.min(running, next),
        to: Math.max(running, next),
        kind: delta >= 0 ? 'up' : 'down',
        delta: delta,
      });
      running = next;
    });
    columns.push({ label: '本期', from: 0, to: running, kind: 'total' });

    const slot = plotW / columns.length;
    const barW = Math.min(56, slot * 0.56);

    const yAt = function (v) {
      return plotBottom - (Math.max(0, v) / maxValue) * plotH;
    };

    ctx.clearRect(0, 0, W, H);

    // 网格
    ctx.strokeStyle = 'rgba(148,163,184,0.12)';
    ctx.lineWidth = 1;
    ctx.fillStyle = cssVar('--text-muted', '#64748b');
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
      const value = (maxValue / 4) * i;
      const y = yAt(value);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
      ctx.fillText(formatNumber(value, 0), padding.left - 9, y);
    }

    const successColor = cssVar('--success', '#4ade80');
    const dangerColor = cssVar('--danger', '#f87171');
    const firefly = cssVar('--firefly', '#5eead4');

    columns.forEach(function (col, i) {
      const x = padding.left + slot * i + (slot - barW) / 2;
      const yTop = yAt(col.to);
      const yBottom = yAt(col.from);
      const h = Math.max(2, yBottom - yTop);

      const color = col.kind === 'total' ? firefly : col.kind === 'up' ? dangerColor : successColor;
      const gradient = ctx.createLinearGradient(0, yTop, 0, yTop + h);
      gradient.addColorStop(0, hexToRgba(color, 0.95));
      gradient.addColorStop(1, hexToRgba(color, 0.45));

      ctx.fillStyle = gradient;
      roundRect(ctx, x, yTop, barW, h, 4);
      ctx.fill();

      // 标签
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = cssVar('--text-secondary', '#94a3b8');
      ctx.fillText(truncate(col.label, slot < 60 ? 4 : 6), x + barW / 2, plotBottom + 12, slot - 3);

      // 数值
      ctx.fillStyle = color;
      ctx.textBaseline = 'bottom';
      const moneyOptions = slot < 85 ? { symbol: '', digits: 0, compact: true } : { digits: 0 };
      const text =
        col.kind === 'total'
          ? formatMoney(col.to, moneyOptions)
          : (col.delta >= 0 ? '+' : '') + formatMoney(col.delta, moneyOptions);
      ctx.fillText(text, x + barW / 2, yTop - 5, Math.max(16, slot - 3));

      // 连接虚线
      if (i < columns.length - 1) {
        const nextX = padding.left + slot * (i + 1) + (slot - barW) / 2;
        ctx.strokeStyle = 'rgba(148,163,184,0.3)';
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x + barW, yAt(col.to));
        ctx.lineTo(nextX, yAt(col.to));
        ctx.stroke();
        ctx.setLineDash([]);
      }
    });
  }

  function hexToRgba(hex, alpha) {
    const value = String(hex || '').trim();
    if (value.startsWith('rgb')) {
      return value.replace(/rgba?\(([^)]+)\)/, function (m, inner) {
        const parts = inner.split(',').map(function (p) {
          return p.trim();
        });
        return 'rgba(' + parts[0] + ',' + parts[1] + ',' + parts[2] + ',' + alpha + ')';
      });
    }
    let hexStr = value.replace('#', '');
    if (hexStr.length === 3) {
      hexStr = hexStr
        .split('')
        .map(function (c) {
          return c + c;
        })
        .join('');
    }
    const num = parseInt(hexStr, 16);
    if (isNaN(num)) return 'rgba(94,234,212,' + alpha + ')';
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function truncate(text, max) {
    const str = String(text);
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
  }

  /* ============================================================ 导出 */
  window.FF = {
    $: $,
    $$: $$,
    el: el,
    clear: clear,
    escapeHtml: escapeHtml,
    debounce: debounce,
    throttle: throttle,

    formatMoney: formatMoney,
    formatNumber: formatNumber,
    formatPercent: formatPercent,
    formatDate: formatDate,
    relativeTime: relativeTime,
    periodLabel: periodLabel,
    periodRange: periodRange,
    delta: delta,
    uid: uid,
    sleep: sleep,
    clamp: clamp,
    sum: sum,
    groupBy: groupBy,
    sortDesc: sortDesc,

    toast: toast,
    confirm: confirmDialog,

    animateCount: animateCount,
    animateCounts: animateCounts,
    typewriter: typewriter,
    observeReveal: observeReveal,
    refreshView: refreshView,

    cssVar: cssVar,
    hexToRgba: hexToRgba,
    drawLineChart: responsiveChart(drawLineChart),
    drawBarChart: responsiveChart(drawBarChart),
    drawDonutChart: responsiveChart(drawDonutChart),
    drawWaterfallChart: responsiveChart(drawWaterfallChart),
  };
})();
