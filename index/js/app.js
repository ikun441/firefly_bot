/* ==========================================================================
   流萤 Firefly · 应用主控与路由
   --------------------------------------------------------------------------
   Hash 路由 + 视图生命周期管理 + 全局状态轮询。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const el = FF.el;
  const Views = window.FFViews;
  const api = window.FFApi.api;

  /* ============================================================
     路由表
     ============================================================ */
  const ROUTES = [
    { key: 'dashboard', hash: '#dashboard', label: '总览', icon: '🌌', view: 'dashboard' },
    { key: 'collector', hash: '#collector', label: '记账', icon: '✒️', view: 'collector' },
    { key: 'ledger', hash: '#ledger', label: '账本', icon: '📚', view: 'ledger' },
    { key: 'report', hash: '#report', label: '报告', icon: '📊', view: 'report' },
    { key: 'assistant', hash: '#assistant', label: '助手', icon: '🔮', view: 'assistant' },
    { key: 'budget', hash: '#budget', label: '预算', icon: '🎯', view: 'budget' },
    { key: 'settings', hash: '#settings', label: '设置', icon: '⚙️', view: 'settings' },
  ];

  const router = {
    currentKey: '',
    destroy: null,
    go: function (key) {
      const route = ROUTES.filter(function (r) {
        return r.key === key || r.hash === '#' + key;
      })[0];
      if (!route) {
        router.go('dashboard');
        return;
      }
      if (window.location.hash !== route.hash) {
        window.location.hash = route.hash;
      } else {
        render(route.key);
      }
    },
    current: function () {
      return router.currentKey;
    },
  };

  window.FFRouter = router;

  /* ============================================================
     导航构建
     ============================================================ */
  function buildNav() {
    const list = FF.$('#nav-links');
    if (!list) return;

    FF.clear(list);

    ROUTES.forEach(function (route) {
      const link = el('button.nav-link', {
        dataset: { key: route.key },
        html: '<span style="margin-right:6px">' + route.icon + '</span>' + route.label,
        on: {
          click: function () {
            router.go(route.key);
          },
        },
      });
      list.appendChild(el('li', {}, [link]));
    });
  }

  function markActive(key) {
    FF.$$('.nav-link').forEach(function (link) {
      link.classList.toggle('is-active', link.dataset.key === key);
    });

    const route = ROUTES.filter(function (r) {
      return r.key === key;
    })[0];
    if (route) document.title = route.label + ' · 流萤 Firefly';
  }

  /* ============================================================
     视图切换
     ============================================================ */
  async function render(key) {
    const route = ROUTES.filter(function (r) {
      return r.key === key;
    })[0];
    if (!route) return;

    // 卸载旧视图
    FF.$$('.page-view').forEach(function (node) {
      node.hidden = true;
    });

    let viewNode = document.getElementById('view-' + key);
    if (!viewNode) {
      viewNode = el('div.page-view', { id: 'view-' + key });
      viewNode.appendChild(el('div.container', { id: 'container-' + key }));
      FF.$('#app-main').appendChild(viewNode);
    }

    viewNode.hidden = false;

    // 清理旧的轮播 / 观察器
    if (typeof router.destroy === 'function') {
      try {
        router.destroy();
      } catch (err) {
        /* 忽略 */
      }
      router.destroy = null;
    }

    router.currentKey = key;
    markActive(key);

    const container = viewNode.firstChild;
    const view = Views[route.view];

    if (!view || typeof view.render !== 'function') {
      FF.clear(container);
      container.appendChild(
        el('div.card', {}, [
          el('div.empty', {}, [
            el('div.empty-icon', { text: '🚧' }),
            el('div.empty-title', { text: '该模块尚未实现' }),
            el('div.empty-desc', { text: '视图 ' + route.view + ' 不存在或未注册。' }),
          ]),
        ])
      );
      return;
    }

    // 仅首次渲染（避免重复请求）；记账页每次都刷新以保证数据新鲜
    if (container.dataset.rendered === '1' && key !== 'collector' && key !== 'report') {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    FF.clear(container);
    container.dataset.rendered = '1';

    try {
      await view.render(container);
    } catch (err) {
      FF.clear(container);
      container.appendChild(
        el('div.card', {}, [
          el('div.empty', {}, [
            el('div.empty-icon', { text: '💥' }),
            el('div.empty-title', { text: '页面渲染出错' }),
            el('div.empty-desc', { text: err && err.message ? err.message : '未知错误' }),
            el('button.btn.btn-primary', {
              text: '重新加载',
              style: { marginTop: '10px' },
              on: {
                click: function () {
                  container.dataset.rendered = '0';
                  render(key);
                },
              },
            }),
          ]),
        ])
      );
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* ============================================================
     连接状态指示
     ============================================================ */
  let statusNode = null;
  let statusLabel = null;

  function updateStatus(online, label) {
    if (!statusNode) return;
    statusNode.classList.remove('is-online', 'is-offline', 'is-mock');

    // 注意：状态胶囊里第一个 <span> 是圆点指示器，
    // 必须按 .status-label 取文本节点，否则永远改不到文字（一直显示「检测中」）。
    if (!statusLabel) statusLabel = statusNode.querySelector('.status-label');

    if (online === true) {
      statusNode.classList.add('is-online');
      if (statusLabel) statusLabel.textContent = label || '服务在线';
    } else if (online === false) {
      statusNode.classList.add('is-offline');
      if (statusLabel) statusLabel.textContent = label || '离线演示';
    } else {
      statusNode.classList.add('is-mock');
      if (statusLabel) statusLabel.textContent = label || '检测中';
    }
  }

  /* ============================================================
     元信息加载
     ============================================================ */
  async function loadMeta() {
    try {
      const meta = await api.meta();
      applyMeta(meta);
      updateStatus(true, '服务在线');
      return true;
    } catch (err) {
      updateStatus(false, '离线演示');
      return false;
    }
  }

  /**
   * 应用登录前的引导信息（/api/bootstrap）。
   *
   * 只处理**纯展示**字段：品牌名、版本、主题。
   * 不含业务配置，那些由 applyMeta 在登录后补齐。
   */
  function applyBootInfo(info) {
    if (!info) return;

    const brandName = FF.$('.brand-name');
    const brandSub = FF.$('.brand-sub');
    const footerName = FF.$('#footer-app-name');

    if (brandName && info.app_name) brandName.textContent = info.app_name;
    if (brandSub && info.subtitle) brandSub.textContent = info.subtitle;
    if (footerName && info.app_name) footerName.textContent = info.app_name;

    if (info.version) {
      const versionNode = FF.$('#footer-version');
      if (versionNode) versionNode.textContent = 'v' + info.version;
    }

    updateModeBadge(!!info.debug);
  }

  /** 应用登录后的完整元信息（/api/meta）。 */
  function applyMeta(meta) {
    if (!meta) return;

    const brandName = FF.$('.brand-name');
    const brandSub = FF.$('.brand-sub');
    const footerName = FF.$('#footer-app-name');

    if (brandName && meta.app_name) brandName.textContent = meta.app_name;
    if (brandSub && meta.subtitle) brandSub.textContent = meta.subtitle;
    if (footerName && meta.app_name) footerName.textContent = meta.app_name;

    if (meta.version) {
      const versionNode = FF.$('#footer-version');
      if (versionNode) versionNode.textContent = 'v' + meta.version;
    }

    // 演示模式：config.yaml 的 app.debug 决定数据来源（真实 / 模拟）
    // 必须在首屏预热数据之前生效，否则会先打一批真实请求。
    if (window.FFApi && typeof window.FFApi.setDebug === 'function') {
      window.FFApi.setDebug(!!meta.debug);
    }
    updateModeBadge(!!meta.debug);

    // 主题
    if (meta.ui) {
      if (meta.ui.theme) document.documentElement.dataset.theme = meta.ui.theme;
      if (meta.ui.particle_enabled === false && window.FireflyParticles) {
        window.FireflyParticles.disable();
      }
    }
  }

  /** 顶栏「演示模式」标记：让使用者一眼看出当前数据是模拟的。 */
  function updateModeBadge(isDebug) {
    const node = FF.$('#mode-badge');
    if (!node) return;
    if (isDebug) {
      node.hidden = false;
      // 文案与 index.html 的静态占位保持一致，避免闪现时跳字
      node.textContent = '演示模式 · 模拟数据';
      node.title = 'config.yaml 中 app.debug = true，前端不会请求真实数据';
      node.classList.add('is-warn');
    } else {
      node.hidden = true;
      node.classList.remove('is-warn');
    }
  }

  /* ============================================================
     主题切换
     ============================================================ */
  function setupThemeToggle() {
    const btn = FF.$('#theme-toggle');
    if (!btn) return;

    const saved = window.localStorage.getItem('firefly-theme');
    if (saved) document.documentElement.dataset.theme = saved;

    const updateIcon = function () {
      const isLight = document.documentElement.dataset.theme === 'firefly-light';
      btn.textContent = isLight ? '🌙' : '☀️';
      btn.title = isLight ? '切换到暗色主题' : '切换到亮色主题';
    };

    updateIcon();

    btn.addEventListener('click', function () {
      const isLight = document.documentElement.dataset.theme === 'firefly-light';
      const next = isLight ? 'firefly-dark' : 'firefly-light';
      document.documentElement.dataset.theme = next;
      window.localStorage.setItem('firefly-theme', next);
      updateIcon();
      FF.toast('已切换到' + (next === 'firefly-light' ? '亮色' : '暗色') + '主题', 'info', 1600);

      // 重绘当前页图表（颜色依赖 CSS 变量）
      const key = router.currentKey;
      router.currentKey = '';
      const container = document.getElementById('container-' + key);
      if (container) container.dataset.rendered = '0';
      render(key);
    });
  }

  /* ============================================================
     粒子开关
     ============================================================ */
  function setupParticleToggle() {
    const btn = FF.$('#particle-toggle');
    if (!btn) return;

    const saved = window.localStorage.getItem('firefly-particles');
    if (saved === 'off' && window.FireflyParticles) {
      window.FireflyParticles.disable();
      document.documentElement.dataset.particles = 'off';
    }

    const updateIcon = function () {
      const enabled = window.FireflyParticles && window.FireflyParticles.isEnabled();
      btn.textContent = enabled ? '✨' : '💤';
      btn.title = enabled ? '关闭萤火粒子' : '开启萤火粒子';
    };

    updateIcon();

    btn.addEventListener('click', function () {
      if (!window.FireflyParticles) return;
      const enabled = window.FireflyParticles.toggle();
      document.documentElement.dataset.particles = enabled ? 'on' : 'off';
      window.localStorage.setItem('firefly-particles', enabled ? 'on' : 'off');
      updateIcon();
      FF.toast(enabled ? '萤火已点亮' : '萤火已休眠', 'info', 1500);
    });
  }

  /* ============================================================
     键盘快捷键
     ============================================================ */
  function setupShortcuts() {
    const map = {
      Digit1: 'dashboard',
      Digit2: 'collector',
      Digit3: 'report',
      Digit4: 'assistant',
      Digit5: 'budget',
      Digit6: 'settings',
    };

    document.addEventListener('keydown', function (e) {
      // 输入框内不触发
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

      if (e.altKey && map[e.code]) {
        e.preventDefault();
        router.go(map[e.code]);
      }
    });
  }

  /* ============================================================
     启动
     ============================================================ */
  /** 从 hash 解析出合法路由键，非法或为空时回落到 dashboard。 */
  function validInitialRoute() {
    const initial = (window.location.hash || '#dashboard').replace('#', '');
    const valid = ROUTES.some(function (r) {
      return r.key === initial;
    });
    return valid ? initial : 'dashboard';
  }

  /** 清空所有视图的渲染缓存并立刻重渲染当前页。 */
  function forceRerender() {
    document.querySelectorAll('.view').forEach(function (node) {
      node.dataset.rendered = '0';
    });
    render(validInitialRoute());
  }

  /**
   * 注册登录 / 登出相关的事件监听。
   *
   * **必须在 boot() 的鉴权分支之前调用**：未登录时 boot() 会提前
   * return，若把监听器写在后面就永远不会挂上，用户登录成功后
   * 界面会停在骨架屏（看起来像登录没生效）。
   */
  function setupAuthListeners() {
    // ---- 路由监听 ----
    // 同样必须早于鉴权分支注册：hashchange 是导航的唯一入口
    // （nav 按钮走 router.go -> 改 hash）。若在未登录时漏注册，
    // 登录后整个导航都是死的 —— 点任何菜单都停在首页。
    window.addEventListener('hashchange', function () {
      const key = (window.location.hash || '#dashboard').replace('#', '');
      render(key);
    });

    // 登录成功：把之前因 401 而没拉到的数据补齐，并强制重渲染当前页
    window.addEventListener('firefly:authenticated', async function () {
      updateStatus(true, '服务在线');
      try {
        const utils = window.FFViewUtils;
        if (utils) {
          await utils.loadCategories();
          await utils.loadTransactions(60);
        }
      } catch (err) {
        /* 静默：预热失败不影响已经建立的登录态 */
      }
      // 登录前 boot() 没有 render 过，这里补上首次渲染
      forceRerender();
    });

    // 登出后清空页面数据，避免「已经退出了还能看到账本」
    window.addEventListener('firefly:logged-out', function () {
      updateStatus(false, '待登录');
      forceRerender();
    });
  }

  async function boot() {
    buildNav();
    setupThemeToggle();
    setupParticleToggle();
    setupShortcuts();

    statusNode = document.getElementById('connection-status');
    updateStatus(null, '检测中');

    // ---- 事件监听必须先于鉴权分支注册 ----
    // 踩过的坑：这两个监听器原本写在 boot() 末尾，而未登录时
    // boot() 会在鉴权检查处提前 return，导致监听器从未挂上 ——
    // 用户登录成功后界面停在骨架屏，看起来像「登录没生效」。
    // 监听器是纯声明，提前注册没有副作用。
    setupAuthListeners();

    // ---- 引导信息：必须是全局最早的请求 ----
    // /api/bootstrap 是免鉴权的纯展示配置，它带回 app.debug。
    // 必须在鉴权之前拿到：演示模式下要**完全跳过登录**（本地数据
    // 本来就不需要后端），而正式模式才需要显示登录遮罩。
    // 若把它放到鉴权之后，未登录时永远拿不到 debug，
    // 就会出现「配置里开着演示模式、页面却在打真实接口」的错位。
    let bootInfo = null;
    try {
      bootInfo = await api.bootstrap();
      applyBootInfo(bootInfo);
    } catch (err) {
      /* 后端没起来也继续：下面 loadMeta 失败会把界面置为离线态 */
    }

    // ---- 演示模式：跳过登录 ----
    // 演示模式下所有数据都来自本地 mock，压根不碰后端业务接口，
    // 此时再拦一道登录既无意义、又会让答辩演示多一步操作。
    if (window.FFApi && window.FFApi.isDebug()) {
      // 连接状态胶囊此时**不再**显示「演示模式」——
      // 顶栏已经有专门的 #mode-badge 承担这个提示，
      // 两处都写会重复（见 index.html 的 topbar-actions）。
      updateStatus(true, '服务在线');
      if (window.FFBotStatus) {
        window.FFBotStatus.init();
      }
      render(validInitialRoute());
      FF.toast('演示模式已开启：当前展示随机生成的模拟数据', 'warning', 4200);
      return;
    }

    // ---- 登录鉴权：必须在拉数据之前 ----
    // 顺序很关键：若先 loadMeta() 再问鉴权，未登录时会先打一串 401，
    // 控制台一片红，且 loadMeta 的失败会把状态误标成「离线」。
    if (window.FFAuth) {
      const authState = await window.FFAuth.init();
      if (authState.required && !authState.authenticated) {
        // 未登录：**故意不调用 render()、也不 loadMeta()**。
        // 两者都会打受保护接口而收到 401（各视图在 render 时
        // 会立刻拉业务数据），白白在控制台刷一串红字。
        // 保持骨架屏即可 —— 登录成功后 firefly:authenticated
        // 事件会强制重渲染，界面照样正常。
        updateStatus(false, '待登录');
        return;
      }
    }

    // 元信息（同时用于健康检查）
    const online = await loadMeta();

    // 协议端状态按钮（顶栏 🤖）
    if (window.FFBotStatus) {
      window.FFBotStatus.init();
    }

    // 首屏数据预热
    if (online) {
      try {
        const utils = window.FFViewUtils;
        await utils.loadCategories();
        await utils.loadTransactions(60);
      } catch (err) {
        /* 静默 */
      }
    }

    // 路由
    const initial = validInitialRoute();
    render(initial);
    // 周期性健康检查（30 秒）
    window.setInterval(async function () {
      try {
        await api.health();
        updateStatus(true, '服务在线');
      } catch (err) {
        updateStatus(false, '离线演示');
      }
    }, 30000);

    // 连接状态变化
    window.addEventListener('firefly:connection', function (e) {
      updateStatus(e.detail.online, e.detail.online ? '服务在线' : '离线演示');
    });

    // 欢迎提示
    if (!online) {
      FF.toast('后端未连接，当前展示示例数据', 'warning', 4200);
    } else {
      FF.toast('流萤已就绪', 'success', 2000);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
