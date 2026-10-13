/* ==========================================================================
   流萤 Firefly · 登录鉴权模块
   --------------------------------------------------------------------------
   职责：
     1. 首次加载时询问后端「本部署是否需要登录」（/api/auth/status）；
     2. 需要登录且未登录时，用全屏遮罩挡住主界面并显示登录卡片；
     3. 提供全局的 onUnauthorized 钩子，供 api.js 在收到 401 时回调，
        把用户送回登录页；
     4. 在顶栏渲染当前登录用户与退出按钮。

   设计要点：
     - **遮罩而非跳页**：本项目是单页应用（hash 路由），
       若跳到独立 login.html，登录成功后还要把用户送回原来那个 hash，
       状态管理复杂且容易丢失；用全屏遮罩可以原地登录、原地恢复。
     - **登录态以服务端为准**：前端不自己记「我登录过」，
       每次刷新都问 /api/auth/status + /api/auth/me，
       避免用 localStorage 记住一个其实已经过期的状态。
   ========================================================================== */

(function () {
  'use strict';

  const state = {
    required: false, // 本部署是否启用鉴权
    authenticated: false, // 当前是否已登录
    username: '',
    checking: false,
    lockedUntil: 0, // 本地记录的锁定到期时间戳（仅用于按钮倒计时）
  };

  const MASK_ID = 'auth-mask';

  /* ============================================================ 工具 */

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === 'text') {
          node.textContent = attrs[key];
        } else if (key === 'html') {
          node.innerHTML = attrs[key];
        } else if (key.indexOf('on') === 0 && typeof attrs[key] === 'function') {
          node.addEventListener(key.slice(2).toLowerCase(), attrs[key]);
        } else if (attrs[key] !== null && attrs[key] !== undefined) {
          node.setAttribute(key, attrs[key]);
        }
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function toast(message, tone, duration) {
    if (window.FF && typeof window.FF.toast === 'function') {
      window.FF.toast(message, tone, duration);
    }
  }

  /* ============================================================ 登录遮罩 */

  function buildMask() {
    const existing = document.getElementById(MASK_ID);
    if (existing) existing.remove();

    const usernameInput = el('input', {
      type: 'text',
      id: 'auth-username',
      class: 'auth-input',
      placeholder: '请输入用户名',
      autocomplete: 'username',
      value: '',
    });

    const passwordInput = el('input', {
      type: 'password',
      id: 'auth-password',
      class: 'auth-input',
      placeholder: '请输入口令',
      autocomplete: 'current-password',
    });

    const rememberBox = el('input', { type: 'checkbox', id: 'auth-remember' });

    const loginBtn = el('button', {
      type: 'button',
      class: 'auth-submit',
      id: 'auth-submit',
      text: '进 入 流 萤',
    });

    const errorBox = el('div', { class: 'auth-error', id: 'auth-error', role: 'alert' });
    errorBox.style.display = 'none';

    const hintBox = el('div', { class: 'auth-hint', id: 'auth-hint' });

    const card = el('div', { class: 'auth-card' }, [
      // ---- 品牌区 ----
      el('div', { class: 'auth-brand' }, [
        el('div', {
          class: 'auth-logo',
          html:
            '<svg viewBox="0 0 48 48" width="52" height="52">' +
            '<defs><radialGradient id="auth-glow" cx="50%" cy="50%" r="50%">' +
            '<stop offset="0%" stop-color="#a7f3d0"/>' +
            '<stop offset="55%" stop-color="#5eead4"/>' +
            '<stop offset="100%" stop-color="#14b8a6"/>' +
            '</radialGradient></defs>' +
            '<circle cx="24" cy="24" r="13" fill="url(#auth-glow)" opacity="0.92"/>' +
            '<circle cx="24" cy="24" r="18" fill="none" stroke="#5eead4" stroke-width="1.1" opacity="0.45"/>' +
            '<circle cx="24" cy="24" r="22" fill="none" stroke="#5eead4" stroke-width="0.7" opacity="0.22"/>' +
            '<ellipse cx="24" cy="24" rx="20" ry="8" fill="none" stroke="#a78bfa" stroke-width="1" opacity="0.5" transform="rotate(-28 24 24)"/>' +
            '</svg>',
        }),
        el('h1', { class: 'auth-title', text: '流萤 Firefly' }),
        el('p', { class: 'auth-subtitle', text: '财报总结优化 · 财税智能助手' }),
      ]),

      // ---- 表单区 ----
      el('div', { class: 'auth-form' }, [
        el('label', { class: 'auth-label', for: 'auth-username', text: '用户名' }),
        usernameInput,
        el('label', { class: 'auth-label', for: 'auth-password', text: '口令' }),
        passwordInput,
        el('label', { class: 'auth-remember' }, [rememberBox, el('span', { text: '记住我（7 天内免登录）' })]),
        errorBox,
        loginBtn,
        hintBox,
      ]),

      // ---- 页脚 ----
      el('div', { class: 'auth-footer' }, [
        el('span', { text: '流萤 Firefly · 金融科技应用毕业设计' }),
      ]),
    ]);

    const mask = el('div', { id: MASK_ID, class: 'auth-mask' }, [card]);

    document.body.appendChild(mask);

    // ---- 事件绑定 ----
    function submit() {
      doLogin(usernameInput.value.trim(), passwordInput.value, rememberBox.checked);
    }

    loginBtn.addEventListener('click', submit);
    [usernameInput, passwordInput].forEach(function (input) {
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          submit();
        }
      });
    });

    // 默认聚焦到用户名（若已填过上次的用户名则聚焦到口令框）
    window.setTimeout(function () {
      if (usernameInput.value) {
        passwordInput.focus();
      } else {
        usernameInput.focus();
      }
    }, 120);

    return mask;
  }

  function setError(message) {
    const box = document.getElementById('auth-error');
    if (!box) return;
    if (!message) {
      box.style.display = 'none';
      box.textContent = '';
      return;
    }
    box.textContent = message;
    box.style.display = 'block';
  }

  function setSubmitting(loading) {
    const btn = document.getElementById('auth-submit');
    if (!btn) return;
    btn.disabled = !!loading;
    btn.textContent = loading ? '登 录 中 …' : '进 入 流 萤';
    btn.classList.toggle('is-loading', !!loading);
  }

  function showMask() {
    let mask = document.getElementById(MASK_ID);
    if (!mask) mask = buildMask();
    mask.classList.add('is-visible');
    // 锁住页面滚动，避免遮罩下面的内容被滚动到
    document.body.classList.add('auth-locked');
  }

  function hideMask() {
    const mask = document.getElementById(MASK_ID);
    if (mask) mask.classList.remove('is-visible');
    document.body.classList.remove('auth-locked');
  }

  /* ============================================================ 登录流程 */

  async function doLogin(username, password, remember) {
    if (!username || !password) {
      setError('请填写用户名与口令');
      return false;
    }

    setError('');
    setSubmitting(true);

    try {
      const envelope = await window.FFApi.api.login(username, password, remember);
      const data = window.FFApi.unwrap(envelope) || {};

      if (!envelope || envelope.success === false) {
        setError((envelope && envelope.message) || '登录失败');

        // 被锁定：在按钮上显示倒计时，避免用户反复点击
        if (data.locked && data.retry_after > 0) {
          state.lockedUntil = Date.now() + data.retry_after * 1000;
          startLockCountdown(data.retry_after);
        } else if (typeof data.remaining_attempts === 'number' && data.remaining_attempts > 0) {
          setError(
            ((envelope && envelope.message) || '登录失败') +
              '（还可尝试 ' +
              data.remaining_attempts +
              ' 次）'
          );
        }
        return false;
      }

      state.authenticated = true;
      state.username = data.username || username;
      state.lockedUntil = 0;

      // 记住用户名（**不存口令**）：只为了下次少打几个字
      try {
        window.localStorage.setItem('firefly-last-user', state.username);
      } catch (err) {
        /* 隐私模式下 localStorage 可能不可用，忽略 */
      }

      hideMask();
      renderTopbarUser();
      toast('欢迎回来，' + state.username, 'success', 2200);

      if (data.using_default_password) {
        window.setTimeout(function () {
          toast('当前仍在使用默认口令，建议尽快在「设置」页修改', 'warning', 6000);
        }, 1200);
      }

      // 重新拉取一次元信息并刷新当前视图，让数据在登录后才加载
      window.dispatchEvent(new CustomEvent('firefly:authenticated'));
      return true;
    } catch (err) {
      // 网络层失败（后端没起来）与业务失败要分开提示，
      // 否则用户会以为是自己密码打错了。
      if (err && err.offline) {
        setError('无法连接后端服务，请确认服务已启动');
      } else {
        setError((err && err.message) || '登录失败，请稍后重试');
      }
      return false;
    } finally {
      setSubmitting(false);
    }
  }

  function startLockCountdown(seconds) {
    const btn = document.getElementById('auth-submit');
    if (!btn) return;

    let remain = Math.max(0, Math.floor(seconds));
    btn.disabled = true;

    const timer = window.setInterval(function () {
      remain -= 1;
      if (remain <= 0) {
        window.clearInterval(timer);
        btn.disabled = false;
        btn.textContent = '进 入 流 萤';
        setError('');
        return;
      }
      btn.textContent = '已锁定 ' + remain + ' 秒';
    }, 1000);
  }

  async function doLogout() {
    try {
      await window.FFApi.api.logout();
    } catch (err) {
      /* 登出失败也要清本地状态，否则用户以为自己没退出来 */
    }
    state.authenticated = false;
    state.username = '';
    renderTopbarUser();

    // 清掉页面上的数据展示，避免已登出还能看到刚才的账本
    window.dispatchEvent(new CustomEvent('firefly:logged-out'));

    if (state.required) {
      showMask();
    } else {
      toast('已退出登录', 'success', 1800);
    }
  }

  /* ============================================================ 状态查询 */

  async function checkStatus() {
    if (state.checking) return state;
    state.checking = true;

    try {
      const envelope = await window.FFApi.api.authStatus();
      const data = window.FFApi.unwrap(envelope) || {};
      state.required = !!data.enabled;
      state.username = data.username || '';
    } catch (err) {
      // 后端不可达：不弹登录框，让页面按离线模式走 mock 数据，
      // 否则「后端没起来」会被误报成「你要登录」。
      state.required = false;
    } finally {
      state.checking = false;
    }

    if (state.required) {
      try {
        const meEnvelope = await window.FFApi.api.me();
        const me = window.FFApi.unwrap(meEnvelope) || {};
        state.authenticated = !!me.authenticated;
        if (me.username) state.username = me.username;
      } catch (err) {
        // 未登录时 /api/auth/me 返回 401，这是**预期路径**而不是故障：
        // 它正是「没有有效会话」的判定依据。浏览器控制台会为此留下一条
        // 红色 401 记录，属正常现象，不是 bug。
        state.authenticated = false;
      }
    } else {
      state.authenticated = true;
    }

    return state;
  }

  /* ============================================================ 顶栏用户 */

  function renderTopbarUser() {
    let box = document.getElementById('auth-user-box');
    // 顶栏操作区的类名以 HTML 为准（.nav-actions）。
    // 这里保留 .topbar-actions 作为回退，避免以后改版再次
    // 出现「选择器失配 → 用户信息框静默消失」这种无声故障。
    const actions =
      document.querySelector('.nav-actions') || document.querySelector('.topbar-actions');
    if (!actions) return;

    if (!state.required || !state.authenticated) {
      if (box) box.remove();
      return;
    }

    if (!box) {
      box = el('div', { id: 'auth-user-box', class: 'auth-user-box' });
      actions.insertBefore(box, actions.firstChild);
    }

    box.innerHTML = '';
    box.appendChild(
      el('span', { class: 'auth-user-name', title: '当前登录用户', text: '👤 ' + (state.username || '已登录') })
    );
    box.appendChild(
      el('button', {
        class: 'auth-logout-btn',
        type: 'button',
        title: '退出登录',
        text: '退出',
        onClick: doLogout,
      })
    );
  }

  /* ============================================================ 401 钩子 */

  function onUnauthorized() {
    // 已经是未登录状态时不再重复弹遮罩，避免多个并发请求同时 401
    // 导致遮罩被反复重建（会打断用户正在输入的密码）。
    if (!state.required || !state.authenticated) {
      if (state.required) showMask();
      return;
    }

    state.authenticated = false;
    renderTopbarUser();
    showMask();
    setError('登录已过期，请重新登录');
    toast('登录已过期，请重新登录', 'warning', 3200);
  }

  /* ============================================================ 初始化 */

  async function init() {
    await checkStatus();

    if (state.required && !state.authenticated) {
      // 回填上次使用的用户名，减少输入
      const mask = buildMask();
      const saved = safeGet('firefly-last-user');
      if (saved) {
        const input = document.getElementById('auth-username');
        if (input) input.value = saved;
      }
      const hint = document.getElementById('auth-hint');
      if (hint) {
        hint.textContent = '连续输错 ' + 5 + ' 次将锁定账号，请谨慎输入';
      }
      mask.classList.add('is-visible');
      document.body.classList.add('auth-locked');
      window.setTimeout(function () {
        const pw = document.getElementById('auth-password');
        if (pw && document.getElementById('auth-username').value) pw.focus();
        else document.getElementById('auth-username').focus();
      }, 150);
    } else if (state.required) {
      renderTopbarUser();
    }

    return state;
  }

  function safeGet(key) {
    try {
      return window.localStorage.getItem(key) || '';
    } catch (err) {
      return '';
    }
  }

  /**
   * 采纳一枚由后端换发的新令牌（改口令后调用）。
   *
   * 本项目**有意不在 JS 里保存令牌**：令牌只存在于 HttpOnly Cookie 中，
   * 脚本读不到，因此 XSS 也偷不走。后端换发令牌时已经把新 Cookie
   * 写进响应头，浏览器会自动替换，前端无需做任何存储。
   * 这里只更新内存中的登录态，避免界面误判为未登录。
   */
  function adoptToken(token) {
    if (!token) return;
    state.authenticated = true;
  }

  window.FFAuth = {
    init: init,
    checkStatus: checkStatus,
    isRequired: function () {
      return state.required;
    },
    isAuthenticated: function () {
      return state.authenticated;
    },
    adoptToken: adoptToken,
    logout: doLogout,
    onUnauthorized: onUnauthorized,
    state: state,
  };
})();
