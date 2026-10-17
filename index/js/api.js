/* ==========================================================================
   流萤 Firefly · API 客户端
   --------------------------------------------------------------------------
   统一封装 fetch：同源调用后端 /api/*。

   数据来源由 config.yaml 的 app.debug 决定（后端在 /api/meta 里以 debug 字段下发）：
     debug = false → 使用真实后端数据，后端不可达时才降级到 mock；
     debug = true  → 强制使用模拟数据，全部请求直接走 mock，不打网络。
   这样答辩现场可以在「真实数据」与「稳定演示」之间一键切换，
   也便于前端脱机开发。
   ========================================================================== */

(function () {
  'use strict';

  const API_BASE = '/api';
  const state = {
    online: null, // null 未知 / true 在线 / false 离线
    lastCheck: 0,
    pending: 0,
    debug: false, // 由 /api/meta 下发：是否强制模拟数据
  };

  /* ============================================================ 底层请求 */

  /**
   * @param {string} path 以 / 开头
   * @param {{method?:string, body?:any, query?:object, timeout?:number, silent?:boolean}} [opts]
   */
  async function request(path, opts) {
    const options = Object.assign({ method: 'GET', timeout: 20000, silent: false }, opts || {});

    let url = API_BASE + path;
    if (options.query) {
      const params = new URLSearchParams();
      Object.keys(options.query).forEach(function (key) {
        const value = options.query[key];
        if (value === undefined || value === null || value === '') return;
        params.append(key, value);
      });
      const qs = params.toString();
      if (qs) url += (url.indexOf('?') >= 0 ? '&' : '?') + qs;
    }

    const init = {
      method: options.method,
      headers: Object.assign({ Accept: 'application/json' }, options.headers || {}),
    };

    if (options.body !== undefined && options.body !== null) {
      if (options.body instanceof FormData) {
        init.body = options.body;
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(options.body);
      }
    }

    const controller = new AbortController();
    init.signal = controller.signal;
    const timer = window.setTimeout(function () {
      controller.abort();
    }, options.timeout);

    state.pending += 1;

    try {
      const response = await fetch(url, init);
      window.clearTimeout(timer);

      const text = await response.text();
      let payload = null;
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          payload = { raw: text };
        }
      }

      if (!response.ok) {
        const message =
          (payload && (payload.detail || payload.message || payload.error)) ||
          'HTTP ' + response.status + ' ' + response.statusText;
        const error = new Error(typeof message === 'string' ? message : JSON.stringify(message));
        error.status = response.status;
        error.payload = payload;

        // ---- 401 统一拦截 ----
        // 会话过期 / 未登录时，后端所有受保护接口都返回 401。
        // 在这里集中处理（而不是让每个视图各判一次），
        // 保证「任何一次请求发现掉线」都能把用户送回登录页。
        // 注意：登录接口自身失败也会返回 400 而非 401，不会误触发这里。
        if (response.status === 401) {
          error.unauthorized = true;
          // 抛给 auth 模块处理，避免 api.js 反向依赖登录视图
          if (window.FFAuth && typeof window.FFAuth.onUnauthorized === 'function') {
            window.FFAuth.onUnauthorized();
          }
        }

        throw error;
      }

      markOnline(true);
      return payload;
    } catch (err) {
      window.clearTimeout(timer);

      if (err.name === 'AbortError') {
        markOnline(false);
        const timeoutError = new Error('请求超时，请检查后端服务是否运行');
        timeoutError.offline = true;
        throw timeoutError;
      }

      // 网络层失败（后端未启动）→ 标记离线，交由调用方降级
      if (!err.status) {
        markOnline(false);
        err.offline = true;
      }

      if (!options.silent) {
        // 交给调用方决定是否提示，避免重复弹窗
      }
      throw err;
    } finally {
      state.pending -= 1;
    }
  }

  function markOnline(value) {
    if (state.online === value) return;
    state.online = value;
    state.lastCheck = Date.now();
    window.dispatchEvent(
      new CustomEvent('firefly:connection', { detail: { online: value } })
    );
  }

  /* ============================================================ 健康检查 */
  async function health() {
    return request('/health', { timeout: 6000, silent: true });
  }

  async function ping() {
    try {
      await health();
      markOnline(true);
      return true;
    } catch (err) {
      markOnline(false);
      return false;
    }
  }

  /* ============================================================ 通用 CRUD 生成器 */
  function resource(path, name) {
    const api = {};

    api.list = function (query) {
      return request(path, { query: query });
    };

    api.get = function (id) {
      return request(path + '/' + encodeURIComponent(id));
    };

    api.create = function (payload) {
      return request(path, { method: 'POST', body: payload });
    };

    api.update = function (id, payload) {
      return request(path + '/' + encodeURIComponent(id), { method: 'PUT', body: payload });
    };

    api.remove = function (id) {
      return request(path + '/' + encodeURIComponent(id), { method: 'DELETE' });
    };

    api._name = name || path;
    return api;
  }

  /* ============================================================ 业务接口 */
  const api = {
    raw: request,
    health: health,
    ping: ping,
    isOnline: function () {
      return state.online === true;
    },
    isOffline: function () {
      return state.online === false;
    },
    connectionState: state,

    /* -------- 元信息 -------- */
    /**
     * 登录前的引导信息（**免鉴权**）。
     *
     * 这是页面启动的**第一个**请求，且必须在登录之前发出 ——
     * 因为它决定了 `state.debug`，而 debug 决定了后续所有请求
     * 是打真实后端还是直接用本地模拟数据。
     * 早期版本错误地依赖 /api/meta 来设置 debug，但 /api/meta 受保护，
     * 未登录时返回 401，导致 debug 永远没被设上：
     * 配置里开着演示模式，页面却仍然去打真实接口（且全部 401）。
     */
    bootstrap: function () {
      return request('/bootstrap', { silent: true }).then(function (envelope) {
        const data = unwrap(envelope);
        if (data && typeof data === 'object') {
          setDebug(!!data.debug);
          if (data.ui && data.ui.theme) {
            document.documentElement.dataset.theme = data.ui.theme;
          }
        }
        return data;
      });
    },

    meta: function () {
      // meta 是登录后的完整元信息。这里同样顺手同步一次 debug，
      // 因为用户可能在设置页改掉 app.debug 后直接刷新当前页，
      // 此时以服务端最新值为准（bootstrap 的结果可能来自缓存）。
      return request('/meta').then(function (envelope) {
        const data = unwrap(envelope);
        if (data && typeof data === 'object') {
          setDebug(!!data.debug);
        }
        return data;
      });
    },

    /* -------- 交易 -------- */
    transactions: resource('/transactions', 'transactions'),
    transactionSummary: function (query) {
      return request('/stats', { query: query });
    },
    transactionTrend: function (query) {
      return request('/stats', { query: query });
    },

    /* -------- 类别 -------- */
    categories: resource('/categories', 'categories'),

    /* -------- 账户 -------- */
    accounts: resource('/accounts', 'accounts'),

    /* -------- 预算 -------- */
    budgets: resource('/budgets', 'budgets'),

    /* -------- 报告 -------- */
    reports: resource('/reports', 'reports'),
    generateReport: function (payload) {
      return request('/reports/generate', {
        method: 'POST',
        body: payload,
        timeout: 90000, // 报告生成可能调用 LLM，给足时间
      });
    },
    pushReport: function (id, options) {
      // 推送选项（content_type / ai_summary / ai_temperature / ai_max_tokens）
      // 由报告页的弹窗收集后传进来；不传则后端沿用 config.yaml 的 push 分组。
      //
      // 超时给到 180s：开启 AI 总结时这一条请求要等 LLM 返回，
      // 实测 DeepSeek 约 3~6s，但网络抖动或长报告会更久。
      // 设成 60s 会在弱网下把「正在生成」误报成失败。
      return request('/reports/' + encodeURIComponent(id) + '/push', {
        method: 'POST',
        body: options || {},
        timeout: 180000,
      });
    },
    renderReport: function (id) {
      return request('/reports/' + encodeURIComponent(id) + '/render', {
        method: 'POST',
        timeout: 60000,
      });
    },

    /* -------- 资金账户（账本） -------- */
    accountsList: function () {
      return request('/accounts');
    },
    accountGet: function (id) {
      return request('/accounts/' + encodeURIComponent(id));
    },
    accountCreate: function (payload) {
      return request('/accounts', { method: 'POST', body: payload });
    },
    accountUpdate: function (id, patch) {
      return request('/accounts/' + encodeURIComponent(id), {
        method: 'PATCH',
        body: patch,
      });
    },
    accountRemove: function (id) {
      return request('/accounts/' + encodeURIComponent(id), { method: 'DELETE' });
    },
    accountTransactions: function (id, query) {
      return request('/accounts/' + encodeURIComponent(id) + '/transactions', {
        query: query || {},
      });
    },

    /* -------- 数据备份与还原 -------- */
    backupList: function () {
      return request('/backup/list', { timeout: 15000 });
    },
    backupCreate: function () {
      // 备份要读全库，给足超时；大库（几万行）在这里也不会太慢，
      // 但默认 15s 可能不够，故显式放宽到 90s。
      return request('/backup/create', { method: 'POST', timeout: 90000 });
    },
    backupDelete: function (name) {
      return request('/backup/' + encodeURIComponent(name), {
        method: 'DELETE',
        timeout: 15000,
      });
    },
    backupInspect: function (formData) {
      // 与 importCsv 同一套写法：直接把 FormData 交给 request，
      // request 内部会识别 FormData 并跳过 Content-Type
      // （让浏览器自己加 multipart boundary）。
      return request('/backup/inspect', { method: 'POST', body: formData, timeout: 60000 });
    },
    backupRestore: function (filename) {
      // confirm 是后端强制的字段，前端在此统一带上，避免调用方漏传
      return request('/backup/restore', {
        method: 'POST',
        body: { filename: filename, confirm: 'RESTORE' },
        timeout: 120000,
      });
    },
    backupRestoreUpload: function (formData) {
      return request('/backup/restore-upload', {
        method: 'POST',
        body: formData,
        query: { confirm: 'RESTORE' },
        timeout: 120000,
      });
    },
    /** 备份文件的下载地址（直接给 <a href> 用，不走 fetch） */
    backupDownloadUrl: function (name) {
      return API_BASE + '/backup/download?name=' + encodeURIComponent(name);
    },

    /* -------- 推送与协议端 -------- */
    pushStatus: function () {
      return request('/push/status', { timeout: 12000, silent: true });
    },
    pushTest: function (payload) {
      return request('/push/test', {
        method: 'POST',
        body: payload || {},
        timeout: 60000,
      });
    },
    pushLatest: function () {
      return request('/push/latest', { method: 'POST', timeout: 120000 });
    },

    /* -------- 设置 -------- */
    getSettings: function () {
      return request('/settings');
    },
    updateSettings: function (patch) {
      return request('/settings', { method: 'PUT', body: patch });
    },
    settingsSchema: function () {
      return request('/settings/schema');
    },

    /* -------- AI -------- */
    nlParse: function (text) {
      // 后端 /ai/natural 默认只解析不落库，返回待确认卡片
      return request('/ai/natural', {
        method: 'POST',
        body: { text: text, auto_create: false },
        timeout: 45000,
      });
    },
    nlConfirm: function (items) {
      // 确认入账：把用户核对过的条目逐条落库
      return request('/ai/nl-confirm', { method: 'POST', body: { items: items }, timeout: 45000 });
    },
    classify: function (payload) {
      return request('/ai/classify', { method: 'POST', body: payload, timeout: 30000 });
    },
    anomalies: function (query) {
      return request('/anomalies', { query: query, timeout: 30000 });
    },
    ask: function (question) {
      return request('/ai/chat', { method: 'POST', body: { question: question }, timeout: 60000 });
    },
    suggestions: function (query) {
      return request('/audit/suggestions', { query: query });
    },
    updateSuggestion: function (id, patch) {
      return request('/audit/suggestions/' + encodeURIComponent(id), { method: 'PUT', body: patch });
    },

    /* -------- 税检 -------- */
    taxCheck: function (query) {
      return request('/tax-check', { query: query, timeout: 40000 });
    },

    /* -------- 订阅 -------- */
    subscriptions: resource('/subscriptions', 'subscriptions'),

    /* -------- 审计 -------- */
    auditLogs: function (query) {
      return request('/audit/logs', { query: query });
    },
    auditStats: function () {
      return request('/audit/stats');
    },

    /* -------- 导入（P2） -------- */
    importCsv: function (formData) {
      return request('/import/csv', { method: 'POST', body: formData, timeout: 90000 });
    },
    importOcr: function (formData) {
      return request('/import/ocr', { method: 'POST', body: formData, timeout: 120000 });
    },

    /* -------- 政策（P2） -------- */
    policies: function (query) {
      return request('/tax-check/policy', { query: query });
    },

    /* -------- 登录鉴权 -------- */
    authStatus: function () {
      return request('/auth/status', { timeout: 10000, silent: true });
    },
    login: function (username, password, remember) {
      // 登录失败是预期内的业务分支（口令错、被锁定），
      // 用 silent 抑制全局 toast，交给登录页自己展示提示。
      return request('/auth/login', {
        method: 'POST',
        body: { username: username, password: password, remember: !!remember },
        timeout: 20000,
        silent: true,
      });
    },
    logout: function () {
      return request('/auth/logout', { method: 'POST', timeout: 10000, silent: true });
    },
    me: function () {
      return request('/auth/me', { timeout: 10000, silent: true });
    },
    changePassword: function (oldPassword, newPassword) {
      return request('/auth/change-password', {
        method: 'POST',
        body: { old_password: oldPassword, new_password: newPassword },
        timeout: 30000,
        silent: true,
      });
    },
  };

  /* ============================================================ 降级包装 */
  /**
   * 调用 API，并按当前模式决定失败时是否降级到 mock。
   *
   * 这里有一条**必须守住的红线**：
   *
   * - ``debug = true``（演示模式）：直接用 mock，**不发任何业务请求**。
   *   数据每次随机生成（见 mock.js 的 ``randomize``），避免每次演示都
   *   看到一模一样的数字而被质疑是假数据。
   *
   * - ``debug = false``（正式模式）：**绝不返回 mock**。
   *   失败就如实抛出，由调用方展示错误/空状态。
   *   理由：财务数据一旦被假数字冒充，用户会照着错误结论做决策，
   *   这比"页面报错"严重得多。以前这里在任何异常（包括 500、
   *   参数错误、超时）时都静默 fallback，导致"接口 500、页面却
   *   显示漂亮图表"这种最致命的静默错误。
   *
   * @param {() => Promise<any>} caller
   * @param {() => any} fallback 仅演示模式使用；正式模式下会被忽略
   * @param {{silent?:boolean, forceMock?:boolean}} [opts]
   * @returns {Promise<{data:any, mock:boolean}>}
   */
  async function withFallback(caller, fallback, opts) {
    const options = Object.assign({ silent: true }, opts || {});
    const useMock = options.forceMock || state.debug;

    if (useMock && typeof fallback === 'function') {
      return { data: await fallback(), mock: true };
    }

    try {
      const envelope = await caller();
      // 后端统一包裹成 { success, data, ... }，这里统一剥一层，
      // 使调用方拿到的就是业务数据、且与 mock 分支形状一致。
      // 不剥的话各视图都要写 result.data.data，漏写一处就会
      // 出现「接口 200、页面全 0」这类最难的静默错误。
      return { data: unwrap(envelope), mock: false };
    } catch (err) {
      // ---- 正式模式：绝不降级到 mock ----
      // 只把错误原样抛出，让调用方渲染错误态。
      // 顺带把 offline 标记透出去，供上层区分"后端没起来"和"业务出错"。
      if (!state.debug) {
        if (!options.silent && err && err.offline) {
          window.FF && window.FF.toast('后端未连接，请检查服务是否已启动', 'error', 4200);
        }
        throw err;
      }

      // ---- 演示模式：兜底到 mock（此时 mock 本就是预期数据源）----
      if (typeof fallback === 'function') {
        return { data: await fallback(), mock: true };
      }
      throw err;
    }
  }

  /**
   * 演示模式下拉取数据；正式模式下原样调用 caller。
   *
   * 用于那些**没有 mock 兜底**的调用点（例如提交类操作）。
   */
  async function withStrict(caller) {
    return await caller();
  }

  /** 剥掉 {success, data} 外层；传入的已是业务数据时原样返回。 */
  function unwrap(envelope) {
    if (envelope && typeof envelope === 'object' && !Array.isArray(envelope)) {
      if ('success' in envelope && 'data' in envelope) {
        return envelope.data;
      }
    }
    return envelope;
  }

  /** 设置演示模式（由 app.js 在拿到 /api/meta 后调用）。 */
  function setDebug(value) {
    state.debug = !!value;
    return state.debug;
  }

  function isDebug() {
    return !!state.debug;
  }

  // 导出时把内层 api 的方法**展开**一层。
  //
  // 为什么这么做：api 里的方法（accountsList / nlParse / ...）原先只能通过
  // window.FFApi.api.xxx 访问，但很多人会顺手写成 window.FFApi.xxx。
  // 后者是 undefined，调用即抛 TypeError；如果外面恰好套了 try/catch
  // （collector.js 的 refreshAccounts 就是），异常会被静默吞掉，
  // 表现成「什么都没发生、请求根本没发出」，极难定位。
  //
  // 展开后两种写法等价。放在最前面是为了让下面的显式字段能覆盖同名项 ——
  // 万一 api 里将来出现 request/state 之类的同名方法，显式导出的版本优先。
  window.FFApi = Object.assign(
    {},
    api,
    {
      api: api,
      request: request,
      withFallback: withFallback,
      withStrict: withStrict,
      unwrap: unwrap,
      setDebug: setDebug,
      isDebug: isDebug,
      state: state,
    }
  );
})();
