/* ==========================================================================
   流萤 Firefly · 视图层
   --------------------------------------------------------------------------
   每个视图是一个对象：render(container) 渲染，可返回 destroy 清理函数。
   视图内部状态使用闭包保存，避免全局污染。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const api = window.FFApi.api;
  const withFallback = window.FFApi.withFallback;
  const Mock = window.FFMock;

  const Views = {};

  /* ============================================================
     共享状态
     ============================================================ */
  const store = {
    categories: [],
    transactions: [],
    budgets: [],
    accounts: [],
    lastReport: null,
    collectorDraft: {
      direction: 'expense',
      category_id: 'c-food',
      amount: '',
      memo: '',
      trade_time: null,
      has_invoice: true,
    },
    mockMode: false,
  };

  window.FFStore = store;

  /* ============================================================
     数据加载（带降级）
     ============================================================ */
  async function loadCategories() {
    const result = await withFallback(
      function () {
        return api.categories.list();
      },
      function () {
        return Mock.CATEGORIES;
      }
    );
    store.categories = normalizeList(result.data);
    store.mockMode = store.mockMode || result.mock;
    return store.categories;
  }

  async function loadTransactions(limit) {
    const result = await withFallback(
      function () {
        return api.transactions.list({ limit: limit || 60 });
      },
      function () {
        return Mock.TRANSACTIONS.slice(0, limit || 60);
      }
    );
    store.transactions = normalizeList(result.data);
    return store.transactions;
  }

  async function loadBudgets() {
    const result = await withFallback(
      function () {
        return api.budgets.list();
      },
      function () {
        return Mock.BUDGETS;
      }
    );
    store.budgets = normalizeList(result.data);
    return store.budgets;
  }

  function normalizeList(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.items)) return data.items;
    if (data && Array.isArray(data.data)) return data.data;
    return [];
  }

  /* ============================================================
     工具：SVG 图标
     ============================================================ */
  function icon(name) {
    const icons = {
      plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
      sparkle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>',
      chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg>',
      trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>',
      check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
      warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>',
      refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
      download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>',
      send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/></svg>',
    };
    return icons[name] || '';
  }

  /* ============================================================
     视图：概览仪表盘
     ============================================================ */
  Views.dashboard = {
    async render(container) {
      container.appendChild(
        el('section.overview-hero', { 'aria-label': '财务总览' }, [
          el('div.overview-eyebrow', { text: 'FIREFLY · FINANCIAL WORKSPACE' }),
          el('h1', { text: '每一笔记录，都让经营更清晰。' }),
          el('p', { text: '收支、预算与风险，在这里一目了然。让流萤陪你看见数字背后的方向。' }),
          el('div.overview-actions', {}, [
            el('a.btn.btn-primary', { href: '#collector', html: '<span aria-hidden="true">＋</span> 记一笔流水' }),
            el('a.btn.btn-ghost', { href: '#report', html: '查看周期报告 <span aria-hidden="true">↗</span>' }),
          ]),
          el('div.overview-orbit', { 'aria-hidden': 'true', html:
            '<svg viewBox="0 0 240 240" fill="none"><circle cx="120" cy="120" r="83" stroke="currentColor" stroke-opacity=".1"/><circle cx="120" cy="120" r="60" stroke="currentColor" stroke-opacity=".1" stroke-dasharray="2 7"/>' +
            '<g class="orbit-track"><ellipse cx="120" cy="120" rx="103" ry="37" transform="rotate(-35 120 120)" stroke="currentColor" stroke-opacity=".38"/><circle cx="200" cy="72" r="4" fill="currentColor"/><circle cx="53" cy="166" r="2" fill="currentColor"/></g>' +
            '<circle class="orbit-core" cx="120" cy="120" r="29" fill="currentColor" fill-opacity=".06" stroke="currentColor" stroke-opacity=".25"/><path d="m120 103 4.5 12.5L137 120l-12.5 4.5L120 137l-4.5-12.5L103 120l12.5-4.5z" fill="currentColor" fill-opacity=".7"/><text x="88" y="221">FIREFLY</text></svg>'
          }),
        ])
      );

      const body = el('div.stack');
      container.appendChild(body);

      body.appendChild(skeletonBlock());

      let payload = null;

      // 正式模式下任何一步失败都要让错误冒到界面上，
      // 不能悄悄换成假数据（见 api.js 中 withFallback 的说明）。
      try {
        const [reportResult] = await Promise.all([
          withFallback(
            function () {
              return api.reports.list({ limit: 1 });
            },
            function () {
              return Mock.REPORTS.slice(0, 1);
            }
          ),
          loadCategories(),
        ]);

        const reports = normalizeList(reportResult.data);

        if (reports.length) {
          const detail = await withFallback(
            function () {
              return api.reports.get(reports[0].id);
            },
            function () {
              return { payload: Mock.buildReportPayload() };
            }
          );
          const detailData = detail.data || {};
          payload = detailData.payload || null;
          // 后端把综述文本放在报告实体上，而图表数据在 payload 里，
          // 这里合并进去，避免报告页综述显示为空。
          if (payload && detailData.summary_text && !payload.summary_text) {
            payload = Object.assign({}, payload, { summary_text: detailData.summary_text });
          }
          if (payload) {
            if (!window.FFApi.isDebug()) {
              const currentBudget = await api.raw('/budget-status', { query: { period: 'monthly' } });
              if (currentBudget.success === false) throw new Error(currentBudget.message || '预算读取失败');
              payload = Object.assign({}, payload, { budget: window.FFApi.unwrap(currentBudget), budgets: undefined });
            }
            payload = normalizeReportPayload(payload);
          }
        }
      } catch (err) {
        FF.clear(body);
        body.appendChild(
          errorBlock('总览数据加载失败', err && err.message ? err.message : '请稍后重试')
        );
        return;
      }

      // 后端还没有任何报告时**不再伪造数据**，而是把用户引导到记账页。
      // 以前这里会悄悄塞一份 Mock.buildReportPayload()，结果新用户
      // 一进首页就看到一组漂亮的假数字，还以为是自己记的账。
      if (!payload) {
        store.lastReport = null;
        FF.clear(body);
        body.appendChild(emptyReportBlock());
        return;
      }

      store.lastReport = payload;

      FF.clear(body);
      renderDashboard(body, payload, store.mockMode);
    },
  };

  /** 通用错误块：正式模式下接口失败时替换掉骨架屏。 */
  function errorBlock(title, detail) {
    return el('div.card', {}, [
      el('div.empty', {}, [
        el('div.empty-icon', { text: '⚠️' }),
        el('div.empty-title', { text: title }),
        el('div.empty-desc', { text: detail }),
        el('button.btn.btn-primary', {
          text: '重新加载',
          style: { marginTop: '16px' },
          on: {
            click: function () {
              window.location.reload();
            },
          },
        }),
      ]),
    ]);
  }

  /** 「还没有报告」的空状态：引导用户先去记账或生成报告。 */
  function emptyReportBlock() {
    return el('div.card', {}, [
      el('div.empty', {}, [
        el('div.empty-icon', { text: '🪄' }),
        el('div.empty-title', { text: '还没有周期报告' }),
        el('div.empty-desc', {
          text: '先在「记账」页录入几笔流水，再到「报告」页生成第一份财税报告。',
        }),
        el('button.btn.btn-primary', {
          text: '去记账',
          style: { marginTop: '16px' },
          on: {
            click: function () {
              window.location.hash = '#collector';
            },
          },
        }),
      ]),
    ]);
  }

  function skeletonBlock() {
    return el('div.stack', {}, [
      el('div.metric-grid', {}, [
        el('div.skeleton', { style: { height: '118px', borderRadius: '14px' } }),
        el('div.skeleton', { style: { height: '118px', borderRadius: '14px' } }),
        el('div.skeleton', { style: { height: '118px', borderRadius: '14px' } }),
        el('div.skeleton', { style: { height: '118px', borderRadius: '14px' } }),
      ]),
      el('div.skeleton', { style: { height: '300px', borderRadius: '20px' } }),
    ]);
  }

  function renderDashboard(root, payload, isMock) {
    const o = payload.overview;

    /* ---- 高亮结论条 ---- */
    root.appendChild(
      el('div.highlight-strip', {}, [
        el('div.highlight-item', { dataset: { tone: 'info' } }, [
          el('div.highlight-label', { text: '报告周期' }),
          el('div.highlight-value', {
            text: FF.formatDate(payload.period_start, 'MM/DD') + ' - ' + FF.formatDate(payload.period_end, 'MM/DD'),
            style: { fontSize: '17px' },
          }),
          el('div.highlight-text', { text: payload.title }),
        ]),
      ].concat(
        payload.highlights.map(function (h, i) {
          return el(
            'div.highlight-item',
            { dataset: { tone: h.tone }, style: { animationDelay: i * 60 + 'ms' } },
            [
              el('div.highlight-label', { text: h.label }),
              el('div.highlight-value', { text: h.value }),
              el('div.highlight-text', { text: h.text }),
            ]
          );
        })
      ))
    );

    /* ---- 核心指标 ---- */
    const metrics = [
      { label: '营业收入', icon: '💰', value: o.income, prev: o.prev_income, tone: 'income', invert: true },
      { label: '总支出', icon: '💸', value: o.expense, prev: o.prev_expense, tone: 'expense', invert: false },
      { label: '净利润', icon: '📈', value: o.net, prev: o.prev_net, tone: 'firefly', invert: true },
      { label: '账户余额', icon: '🏦', value: o.balance, prev: null, tone: 'violet', invert: true },
    ];

    const metricGrid = el('div.metric-grid');
    const counters = [];

    metrics.forEach(function (m) {
      const valueNode = el('div.metric-value', {}, [
        el('span', { dataset: { value: '0' } }),
      ]);

      const card = el('div.metric-card', { dataset: { tone: m.tone } }, [
        el('div.metric-label', {}, [el('span', { text: m.icon }), el('span', { text: m.label })]),
        valueNode,
      ]);

      if (m.prev !== null && m.prev !== undefined) {
        const d = FF.delta(m.value, m.prev);
        // 支出上升是坏消息，颜色语义反转
        let tone = d.tone;
        if (!m.invert && tone !== 'flat') tone = tone === 'up' ? 'up' : 'down';
        card.appendChild(
          el('div.metric-delta.is-' + tone, {}, [
            el('span', { text: d.text }),
            el('span.text-muted', { text: ' 环比' }),
          ])
        );
      } else {
        card.appendChild(el('div.metric-delta.is-flat', {}, [el('span', { text: '实时余额' })]));
      }

      metricGrid.appendChild(card);
      counters.push({
        node: valueNode.firstChild,
        value: m.value,
        opts: { prefix: '¥', digits: 2, duration: 1300, compact: m.value >= 1000000 },
      });
    });

    root.appendChild(metricGrid);
    FF.animateCounts(counters, 1300);

    /* ---- 趋势 + 结构 ---- */
    const trendCanvas = el('canvas.chart-canvas');
    const donutCanvas = el('canvas.chart-canvas');

    root.appendChild(
      el('div.grid.grid-2', {}, [
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '📉' }), el('span', { text: '支出趋势' })]),
            el('span.card-desc', { text: '按日聚合' }),
          ]),
          el('div.chart-box', { style: { minHeight: '300px' } }, [trendCanvas]),
        ]),
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🍩' }), el('span', { text: '支出结构' })]),
            el('span.card-desc', { text: 'Top 6 类别' }),
          ]),
          el('div.chart-box', { style: { minHeight: '300px' } }, [donutCanvas]),
        ]),
      ])
    );

    // 图表绘制（延后一帧等待布局）
    window.requestAnimationFrame(function () {
      FF.drawLineChart(trendCanvas, {
        labels: payload.trend.map(function (t) {
          return FF.formatDate(t.date, 'MM/DD');
        }),
        series: [
          {
            name: '支出',
            color: FF.cssVar('--firefly', '#5eead4'),
            values: payload.trend.map(function (t) {
              return t.expense;
            }),
          },
        ],
      });

      const palette = ['#f472b6', '#38bdf8', '#5eead4', '#a78bfa', '#fbbf24', '#fb923c'];
      FF.drawDonutChart(
        donutCanvas,
        payload.categories.slice(0, 6).map(function (c, i) {
          return { value: c.amount, color: palette[i % palette.length] };
        }),
        {
          centerValue: FF.formatMoney(o.expense, { symbol: '', digits: 0, compact: true }).trim(),
          centerLabel: '总支出',
        }
      );
    });

    /* ---- 归因 + 预算 ---- */
    const waterfallCanvas = el('canvas.chart-canvas');

    const budgetList = el('div');
    const budgets = (payload.budgets || []).slice(0, 6);

    if (!budgets.length) {
      budgetList.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '🎯' }),
          el('div.empty-title', { text: '尚未设置预算' }),
          el('div.empty-desc', { text: '在「预算」页面为常用类别设定月度额度，流萤会在达到 80% 时主动预警。' }),
        ])
      );
    } else {
      budgets.forEach(function (b) {
        const statusClass = b.status === 'over' ? 'is-over' : b.status === 'warn' ? 'is-warn' : 'is-ok';
        const barClass = b.status === 'over' ? 'is-over' : b.status === 'warn' ? 'is-warn' : '';
        const tagClass = b.status === 'over' ? 'is-danger' : b.status === 'warn' ? 'is-warning' : 'is-success';
        const tagText = b.status === 'unbudgeted' ? '未设预算' : b.status === 'over' ? '已超支' : b.status === 'warn' ? '接近上限' : '正常';

        budgetList.appendChild(
          el('div.budget-item.' + statusClass, {}, [
            el('div.budget-head', {}, [
              el('div.budget-title', {}, [
                el('span', { text: b.icon || '📦' }),
                el('span', { text: b.category_name }),
              ]),
              el('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } }, [
                el('span.tag.' + tagClass, { text: tagText }),
                el('span.bar-amount', {
                  html:
                    '<strong>' + FF.formatMoney(b.used, { digits: 0 }) + '</strong> / ' +
                    FF.formatMoney(b.budget, { digits: 0 }),
                }),
              ]),
            ]),
            el('div.progress', {}, [
              el('div.progress-bar.' + barClass, { style: { width: '0%' }, dataset: { target: Math.min(b.ratio, 100) } }),
            ]),
            el('div.field-hint', {
              text: b.status === 'unbudgeted' ? '此类别尚未设置预算，已支出 ' + FF.formatMoney(b.used) : '执行率 ' + FF.formatPercent(b.ratio) + (b.status === 'over'
                ? '，超出 ' + FF.formatMoney(b.used - b.budget, { digits: 0 })
                : '，剩余 ' + FF.formatMoney(b.budget - b.used, { digits: 0 })),
              style: { marginTop: '7px' },
            }),
          ])
        );
      });
    }

    root.appendChild(
      el('div.grid.grid-2', {}, [
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🪜' }), el('span', { text: '环比归因' })]),
            el('span.card-desc', { text: '较上期支出变化拆解' }),
          ]),
          el('div.chart-box', { style: { minHeight: '320px' } }, [waterfallCanvas]),
        ]),
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🎯' }), el('span', { text: '预算执行' })]),
            el('span.card-desc', { text: '当月额度使用情况' }),
          ]),
          budgetList,
        ]),
      ])
    );

    window.requestAnimationFrame(function () {
      FF.drawWaterfallChart(waterfallCanvas, payload.attribution, {
        startLabel: '上期支出',
        startValue:
          payload.attribution_start_value !== undefined
            ? payload.attribution_start_value
            : o.prev_expense,
        endValue:
          payload.attribution_end_value !== undefined
            ? payload.attribution_end_value
            : o.expense,
      });
      // 预算条动画
      FF.$$('.progress-bar[data-target]', root).forEach(function (bar, i) {
        window.setTimeout(function () {
          bar.style.width = bar.dataset.target + '%';
        }, 240 + i * 90);
      });
    });

    /* ---- 税务自检 + 行动清单 ---- */
    const checks = (payload.tax_checks || []).slice(0, 4);
    const checkList = el('div');
    checks.forEach(function (c) {
      const levelTag =
        c.level === 'high' ? 'is-danger' : c.level === 'medium' ? 'is-warning' : 'is-info';
      const levelText = c.level === 'high' ? '高风险' : c.level === 'medium' ? '中风险' : '低风险';

      checkList.appendChild(
        el('div.check-item', { dataset: { level: c.level } }, [
          el('div.check-head', {}, [
            el('div.check-title', {}, [el('span', { text: '⚠️' }), el('span', { text: c.title })]),
            el('span.tag.' + levelTag, { text: levelText }),
          ]),
          el('div.check-desc', { text: c.description }),
          el('div.check-suggestion', { text: c.suggestion }),
        ])
      );
    });

    const actionList = el('div');
    (payload.actions || []).slice(0, 4).forEach(function (a) {
      const priorityText = a.priority === 'high' ? '高优先' : a.priority === 'medium' ? '中优先' : '可延后';
      const tagClass = a.priority === 'high' ? 'is-danger' : a.priority === 'medium' ? 'is-warning' : 'is-info';

      actionList.appendChild(
        el('div.action-item', { dataset: { priority: a.priority } }, [
          el('div.action-priority'),
          el('div.action-body', {}, [
            el('div.action-title', {}, [
              el('span', { text: a.title }),
              el('span.tag.' + tagClass, { text: priorityText }),
            ]),
            el('div.action-detail', { text: a.detail }),
            el('div.action-do', { text: a.action }),
            a.impact
              ? el('div.field-hint', { text: '预期效果：' + a.impact, style: { marginTop: '6px' } })
              : null,
          ]),
        ].filter(Boolean))
      );
    });

    root.appendChild(
      el('div.grid.grid-sidebar', {}, [
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🧾' }), el('span', { text: '税务风险自检' })]),
            el('a', {
              href: '#report',
              text: '查看完整报告',
              className: 'card-desc',
              style: { textDecoration: 'none', color: 'var(--firefly)' },
              on: {
                click: function (e) {
                  e.preventDefault();
                  window.FFRouter && window.FFRouter.go('report');
                },
              },
            }),
          ]),
          checkList,
        ]),
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '✨' }), el('span', { text: '优化行动清单' })]),
          ]),
          actionList,
        ]),
      ])
    );

    /* ---- 披露 ---- */
    root.appendChild(
      el('div', {
        style: {
          fontSize: '11.5px',
          color: 'var(--text-muted)',
          lineHeight: '1.7',
          textAlign: 'center',
          padding: '8px 0',
        },
        text:
          '本报告由流萤基于用户录入数据自动生成，仅作经营参考，不构成正式税务鉴证意见。' +
          (isMock ? ' 当前展示为示例数据。' : ''),
      })
    );

    observeReveal(root);
  }

  /* 滚动渐显 */
  function observeReveal(root) {
    FF.observeReveal(root);
  }

  /* ============================================================
     工具：el 在本文件复用
     ============================================================ */
  const el = FF.el;

  window.FFViews = Views;

  /* 导出内部工具供其他视图文件使用 */
  /* ============================================================
     报告载荷归一化
     ------------------------------------------------------------
     后端 /reports/generate 返回的 payload 是嵌套的（唯一数据源，
     同时供前端与 htmlrender 消费），而内置 mock 用的是扁平结构。
     两边形状不一致会让「真实数据下图表空白、只报一个 map is not a function」,
     所以这里统一成视图侧好用的扁平形状，两种来源都能画。
     ============================================================ */
  function normalizeReportPayload(payload) {
    if (!payload || typeof payload !== 'object') return payload;

    const out = Object.assign({}, payload);
    const meta = payload.meta || {};
    const structure = payload.structure || {};
    const trend = payload.trend || {};
    const attribution = payload.attribution || {};
    const tax = payload.tax || {};
    const optimization = payload.optimization || {};
    const budget = payload.budget || {};

    // 报告元信息（周期、生成时间）后端放在 meta 下，视图按顶层字段读取
    if (!out.period_start) out.period_start = meta.period_start;
    if (!out.period_end) out.period_end = meta.period_end;
    if (!out.generated_at) out.generated_at = meta.generated_at;
    if (!out.period) out.period = meta.period;
    if (!out.period_label) out.period_label = meta.period_label;
    if (!out.title && meta.period_label) out.title = meta.period_label + ' 财税报告';

    // 支出类别明细
    if (!Array.isArray(out.categories)) {
      out.categories = Array.isArray(structure.expense_categories)
        ? structure.expense_categories
        : [];
    }

    // 日趋势 → [{date, expense}]
    if (!Array.isArray(out.trend)) {
      const daily = Array.isArray(trend.daily_expense) ? trend.daily_expense : [];
      out.trend = daily.map(function (item) {
        return { date: item.date, expense: item.amount };
      });
    }

    // 瀑布图
    // 后端 attribution.waterfall 的元素是 {name, value, type, color}，
    // 而绘图函数需要 {label, delta} —— 在这里统一翻译，避免图上出现 undefined。
    // 注意：后端的 waterfall **已经自带**「上期支出 / 本期支出」两根总额柱，
    // 所以要把它们剔除并转成绘图函数的 startValue/endValue，
    // 否则图上会多出两根空标签的重复柱。
    if (!Array.isArray(out.attribution)) {
      const waterfall = Array.isArray(attribution.waterfall) ? attribution.waterfall : [];
      const middle = waterfall.filter(function (item) {
        return item.type !== 'total';
      });
      out.attribution = middle.map(function (item) {
        const delta = Number(item.value) || 0;
        return {
          label: item.name || '',
          delta: delta,
          current: item.current,
          previous: item.previous,
          color: item.color,
          kind: delta >= 0 ? 'up' : 'down',
        };
      });
      out.attribution_summary = attribution.summary || '';
      out.attribution_start_value = Number(attribution.previous_total) || 0;
      out.attribution_end_value = Number(attribution.current_total) || 0;
    }

    // 税检清单
    if (!Array.isArray(out.tax_checks)) {
      out.tax_checks = Array.isArray(tax.checks) ? tax.checks : [];
    }

    // 优化行动项
    if (!Array.isArray(out.actions)) {
      out.actions = Array.isArray(optimization.action_items) ? optimization.action_items : [];
    }

    // 预算明细
    out.budgets = (Array.isArray(out.budgets) ? out.budgets : Array.isArray(budget.items) ? budget.items : []).map(function (item) {
      return Object.assign({}, item, {
        category_name: item.name || item.category_name || '总预算',
        used: Number(item.actual !== undefined ? item.actual : item.used) || 0,
        ratio: item.usage_ratio !== undefined ? item.usage_ratio : item.ratio,
        status: item.state || item.status || 'ok',
      });
    });
    if (budget.has_budget && !out.budgets.some(function (b) { return b.budget > 0; })) {
      out.budgets.unshift({ category_name: '总预算', icon: '🎯', budget: budget.total_budget, used: budget.total_actual, ratio: budget.total_usage_ratio, status: budget.overall_state });
    }

    // 综述文本：后端在报告实体上，前端有时只拿到 payload
    if (!out.summary_text) {
      out.summary_text = payload.summary_text || '';
    }

    // 账户余额
    // 后端的 overview 只给 net_assets（净资产），而「账户余额」在页面上是一个独立指标。
    // 两者口径不同：净资产只累加 include_in_net 的账户，账户余额是全部账户之和。
    // 这里从 accounts 列表推出余额，缺失时退回 net_assets，避免页面恒显 ¥0.00。
    const accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    if (!Array.isArray(out.accounts)) out.accounts = accounts;
    if (out.overview && typeof out.overview === 'object') {
      const overview = Object.assign({}, out.overview);
      if (overview.balance === undefined || overview.balance === null) {
        overview.balance = accounts.length
          ? accounts.reduce(function (sum, a) {
              return sum + (Number(a.balance) || 0);
            }, 0)
          : Number(overview.net_assets) || 0;
      }
      out.overview = overview;
    }

    return out;
  }

  window.FFViewUtils = {
    icon: icon,
    store: store,
    loadCategories: loadCategories,
    loadTransactions: loadTransactions,
    loadBudgets: loadBudgets,
    normalizeList: normalizeList,
    normalizeReportPayload: normalizeReportPayload,
    observeReveal: observeReveal,
    skeletonBlock: skeletonBlock,
    el: el,
  };
})();
