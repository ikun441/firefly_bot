/* ==========================================================================
   流萤 Firefly · 周期报告视图
   --------------------------------------------------------------------------
   两大核心模块：
     A. 财务税务整体状况
     B. 财务税务优化方向
   含：周期自定义、生成动画、结果逐块揭晓、图片渲染与协议端推送。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const el = FF.el;
  const api = window.FFApi.api;
  const withFallback = window.FFApi.withFallback;
  const Mock = window.FFMock;
  const Views = window.FFViews;
  const utils = window.FFViewUtils;

  /* ============================================================
     状态
     ============================================================ */
  const state = {
    period: 'monthly',
    customDays: 30,
    payload: null,
    reportId: null,
    reports: [],
    isMock: false,
  };

  /* ============================================================
     生成步骤（动画用）
     ============================================================ */
  const STEPS = [
    { key: 'fetch', label: '汇总本期交易流水', icon: '📥' },
    { key: 'structure', label: '计算收支结构与预算执行', icon: '🧮' },
    { key: 'attribution', label: '进行环比归因分析', icon: '🪜' },
    { key: 'tax', label: '执行税务风险自检', icon: '🧾' },
    { key: 'ai', label: 'AI 生成优化方向', icon: '🧠' },
    { key: 'render', label: '渲染可视化结果', icon: '🎨' },
  ];

  /* ============================================================
     渲染入口
     ============================================================ */
  Views.report = {
    async render(container) {
      container.appendChild(
        el('div.page-head', {}, [
          el('h1.page-title', {}, [el('span', { text: '📊' }), el('span', { text: '周期财务税务报告' })]),
          el('p.page-subtitle', {
            text: '自定义统计周期，自动汇总收支、预算与税务风险，输出整体状况与优化方向两大模块。',
          }),
        ])
      );

      /* ---- 控制条 ---- */
      const periodSelect = el('select.select', { style: { maxWidth: '190px' } });
      [
        { value: 'daily', text: '日报（今日）' },
        { value: 'weekly', text: '周报（近 7 天）' },
        { value: 'biweekly', text: '双周报（近 14 天）' },
        { value: 'monthly', text: '月报（近 30 天）' },
        { value: 'quarterly', text: '季报（近 90 天）' },
        { value: 'yearly', text: '年报（近 365 天）' },
        { value: 'custom', text: '自定义周期' },
      ].forEach(function (opt) {
        periodSelect.appendChild(el('option', { value: opt.value, text: opt.text, selected: opt.value === state.period }));
      });

      const customDaysField = el('div.field', { style: { marginBottom: '0', display: 'none' } }, [
        el('label.field-label', { text: '周期天数' }),
        el('input.input', {
          type: 'number',
          min: '1',
          max: '365',
          value: String(state.customDays),
          style: { maxWidth: '110px' },
          on: {
            input: function (e) {
              state.customDays = Number(e.target.value) || 30;
            },
          },
        }),
      ]);

      periodSelect.addEventListener('change', function () {
        state.period = periodSelect.value;
        customDaysField.style.display = state.period === 'custom' ? 'flex' : 'none';
      });

      const generateBtn = el('button.btn.btn-primary', {
        html: '⚡ 生成报告',
        on: {
          click: function () {
            generate(container);
          },
        },
      });

      const pushBtn = el('button.btn', {
        html: '📤 推送到协议端',
        disabled: true,
        on: {
          click: async function () {
            if (!state.reportId) {
              FF.toast('请先生成报告', 'warning');
              return;
            }

            // 先弹选项，再发请求：让用户在点下去的那一刻就能决定
            // 发文字还是图片、要不要附 AI 解读，而不是改完配置再回来。
            let options = null;
            if (window.FFPushOptions) {
              options = await window.FFPushOptions.open();
              if (!options) return; // 用户取消
              window.FFPushOptions.save(options);
            }

            pushBtn.classList.add('is-loading');
            try {
              const result = await withFallback(
                function () {
                  return api.pushReport(state.reportId, options || {});
                },
                function () {
                  return { ok: true, message: 'mock' };
                }
              );
              if (result.mock) {
                FF.toast('后端未连接，推送为模拟操作', 'warning');
                return;
              }
              // 关键：外层 HTTP 成功 != 业务推送成功。
              // 后端即使没配推送目标、或协议端未连接，也会返回
              // HTTP 200 + { success: true, data: { text_sent: false,
              // image_sent: false, errors: [...] } }。
              // 只看 HTTP 会得到「提示成功、实际没发出去」的假象。
              const data = (result.data && result.data.data) || result.data || {};
              const sent = !!(data.text_sent || data.image_sent);
              if (sent) {
                const parts = [];
                if (data.image_sent) parts.push('报告图片');
                if (data.text_sent) parts.push('文字摘要');
                if (data.ai_engine === 'llm') parts.push('AI 解读');
                let msg = '已推送到协议端（' + parts.join(' + ') + '）';
                if (data.ai_elapsed_ms) {
                  msg += '，AI 总结耗时 ' + (data.ai_elapsed_ms / 1000).toFixed(1) + 's';
                }
                FF.toast(msg, 'success', 5200);
              } else {
                const why =
                  (data.errors && data.errors[0]) || '协议端未连接或未配置推送目标';
                FF.toast('推送未发出：' + why, 'error', 5200);
              }
            } catch (err) {
              FF.toast('推送失败：' + (err.message || '未知错误'), 'error');
            } finally {
              pushBtn.classList.remove('is-loading');
            }
          },
        },
      });

      const imageBtn = el('button.btn', {
        html: '🖼️ 渲染图片',
        disabled: true,
        on: {
          click: async function () {
            if (!state.reportId) return;
            imageBtn.classList.add('is-loading');
            try {
              const result = await withFallback(
                function () {
                  return api.renderReport(state.reportId);
                },
                function () {
                  return { image_path: null };
                }
              );
              if (result.mock || !result.data.image_path) {
                FF.toast('后端未连接，无法渲染图片', 'warning');
              } else {
                FF.toast('图片已渲染：' + result.data.image_path, 'success');
              }
            } catch (err) {
              FF.toast('渲染失败：' + (err.message || '未知错误'), 'error');
            } finally {
              imageBtn.classList.remove('is-loading');
            }
          },
        },
      });

      const printBtn = el('button.btn.btn-ghost', {
        html: '🖨️ 打印 / 导出 PDF',
        disabled: true,
        on: {
          click: function () {
            window.print();
          },
        },
      });

      container.appendChild(
        el('div.card.no-print', {}, [
          el('div', {
            style: { display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'flex-end' },
          }, [
            el('div.field', { style: { marginBottom: '0' } }, [
              el('label.field-label', { text: '统计周期' }),
              periodSelect,
            ]),
            customDaysField,
            el('div.btn-group', { style: { marginLeft: 'auto' } }, [
              generateBtn,
              imageBtn,
              pushBtn,
              printBtn,
            ]),
          ]),
        ])
      );

      const stage = el('div.report-stage');
      container.appendChild(stage);

      container._stage = stage;
      container._buttons = { generateBtn, imageBtn, pushBtn, printBtn };

      /* ---- 首次进入自动生成 ---- */
      await generate(container);
    },
  };

  /* ============================================================
     生成报告
     ============================================================ */
  async function generate(container) {
    const stage = container && container._stage;
    if (!stage || !container.isConnected || container._generating) return;
    const generateBtn = container._buttons.generateBtn;
    const period = state.period;
    const customDays = state.customDays;
    container._generating = true;
    generateBtn.disabled = true;
    generateBtn.classList.add('is-loading');

    FF.clear(stage);
    const scan = renderScanning();
    stage.appendChild(scan.node);

    const startedAt = performance.now();

    // 动画与请求并行
    const requestPromise = withFallback(
      function () {
        return api.generateReport({ period: period, custom_days: customDays });
      },
      async function () {
        // mock：模拟生成耗时
        await FF.sleep(1400);
        const payload = Mock.buildReportPayload();
        payload.period = period;
        const range = FF.periodRange(period, new Date(), customDays);
        payload.period_start = range.start.toISOString();
        payload.period_end = range.end.toISOString();
        payload.title = FF.formatDate(range.start, 'MM-DD') + ' ~ ' + FF.formatDate(range.end, 'MM-DD') + ' 财务税务分析报告';
        return { id: 'rpt-mock-' + Date.now(), payload: payload, status: 'completed' };
      }
    );

    const animationPromise = runScanAnimation(scan);

    let result = null;
    let error = null;

    try {
      const settled = await Promise.all([requestPromise, animationPromise]);
      result = settled[0];
    } catch (err) {
      error = err;
      await animationPromise.catch(function () {});
    }

    container._generating = false;
    generateBtn.disabled = false;
    generateBtn.classList.remove('is-loading');
    if (!container.isConnected) return;

    if (error) {
      FF.clear(stage);
      stage.appendChild(
        el('div.card', {}, [
          el('div.empty', {}, [
            el('div.empty-icon', { text: '💥' }),
            el('div.empty-title', { text: '报告生成失败' }),
            el('div.empty-desc', { text: error.message || '未知错误，请检查后端服务与数据。' }),
            el('button.btn.btn-primary', {
              text: '重试',
              style: { marginTop: '8px' },
              on: {
                click: function () {
                  generate(container);
                },
              },
            }),
          ]),
        ])
      );
      return;
    }

    const data = result.data || {};
    // 归一化：后端 payload 是嵌套结构，mock 是扁平结构，
    // 统一成视图侧好用的扁平形状（见 views.js: normalizeReportPayload）。
    const rawPayload = data.payload || data;
    const payload = window.FFViewUtils
      ? window.FFViewUtils.normalizeReportPayload(rawPayload)
      : rawPayload;

    state.payload = payload;
    state.reportId = data.id || data.report_id || null;
    state.isMock = result.mock;

    FF.clear(stage);
    renderReport(stage, payload, state.isMock);

    // 启用按钮
    ['imageBtn', 'pushBtn', 'printBtn'].forEach(function (key) {
      container._buttons[key].disabled = false;
    });

    const elapsed = Math.round(performance.now() - startedAt);
    if (!container.closest('[hidden]')) FF.toast('报告生成完成，用时 ' + elapsed + 'ms', 'success');
  }

  /* ============================================================
     扫描动画
     ============================================================ */
  function renderScanning() {
    const stepNodes = STEPS.map(function (step) {
      const node = el('div.scan-step', { dataset: { key: step.key } }, [
        el('span.scan-step-icon', { text: step.icon }),
        el('span', { text: step.label }),
      ]);
      return { step: step, node: node };
    });

    const barFill = el('div.scan-bar-fill');

    const node = el('div.report-scanning', {}, [
      el('div.scan-orb', {}, [el('div.scan-orb-core', { text: '🪄' })]),
      el('div.scan-title', { text: '正在为你生成周期报告…' }),
      el('div.scan-steps', {}, stepNodes.map(function (s) { return s.node; })),
      el('div.scan-bar', {}, [barFill]),
    ]);

    return { node: node, steps: stepNodes, barFill: barFill };
  }

  function runScanAnimation(scan) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      scan.steps.forEach(function (step) { step.node.classList.add('is-done'); });
      scan.barFill.style.width = '100%';
      return Promise.resolve();
    }
    return new Promise(function (resolve) {
      const total = scan.steps.length;
      let index = 0;

      function next() {
        if (!scan.node.isConnected || scan.node.closest('[hidden]')) { resolve(); return; }
        if (index > 0) {
          scan.steps[index - 1].node.classList.remove('is-active');
          scan.steps[index - 1].node.classList.add('is-done');
        }

        if (index >= total) {
          scan.barFill.style.width = '100%';
          window.setTimeout(resolve, 380);
          return;
        }

        scan.steps[index].node.classList.add('is-active');
        scan.barFill.style.width = ((index + 1) / total) * 100 + '%';
        index += 1;

        window.setTimeout(next, 260 + Math.random() * 180);
      }

      next();
    });
  }

  /* ============================================================
     报告渲染
     ============================================================ */
  function renderReport(root, payload, isMock) {
    const o = payload.overview || {};

    /* ---- 报告头部 ---- */
    root.appendChild(
      el('div.card.reveal', {}, [
        el('div', {
          style: { display: 'flex', justifyContent: 'space-between', gap: '18px', flexWrap: 'wrap', alignItems: 'flex-start' },
        }, [
          el('div', {}, [
            el('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px', flexWrap: 'wrap' } }, [
              el('h2', { text: payload.title || '周期财务税务分析报告', style: { fontSize: '21px' } }),
              isMock ? el('span.tag.is-warning', { text: '示例数据' }) : null,
            ].filter(Boolean)),
            el('div.field-hint', {
              text:
                '统计区间：' + FF.formatDate(payload.period_start, 'YYYY-MM-DD') + ' 至 ' + FF.formatDate(payload.period_end, 'YYYY-MM-DD') +
                ' · 生成时间：' + FF.formatDate(payload.generated_at, 'YYYY-MM-DD HH:mm'),
            }),
          ]),
          el('div', { style: { textAlign: 'right' } }, [
            el('div', { style: { fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }, text: '本期净利' }),
            el('div', {
              text: FF.formatMoney(o.net || 0),
              style: {
                fontSize: '26px',
                fontWeight: '700',
                color: (o.net || 0) >= 0 ? 'var(--success)' : 'var(--danger)',
                fontVariantNumeric: 'tabular-nums',
              },
            }),
          ]),
        ]),
        el('div.grid', { style: { gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '14px', marginTop: '22px' } },
          [
            { label: '营业收入', value: o.income, tone: 'income' },
            { label: '总支出', value: o.expense, tone: 'expense' },
            { label: '毛利率', value: o.gross_margin, tone: 'firefly', isPercent: true },
            { label: '交易笔数', value: o.transaction_count, tone: 'violet', isCount: true },
            { label: '有票占比', value: o.invoice_ratio, tone: 'sky', isPercent: true },
          ].map(function (m) {
            const valueNode = el('div', {
              text: '—',
              style: { fontSize: '20px', fontWeight: '700', fontVariantNumeric: 'tabular-nums' },
            });
            const box = el('div', {
              style: { padding: '14px 16px', borderRadius: '12px', border: '1px solid var(--border)', background: 'var(--bg-elevated)' },
            }, [
              el('div.field-hint', { text: m.label, style: { marginBottom: '5px' } }),
              valueNode,
            ]);
            const value = Number(m.value) || 0;
            if (m.isCount) FF.animateCount(valueNode, value, { duration: 1000, digits: 0, prefix: '' });
            else if (m.isPercent) FF.animateCount(valueNode, value, { duration: 1000, digits: 1, prefix: '', suffix: '%' });
            // prefix 显式写全：animateCount 只有在 compact 时才默认 '¥'，
            // 这里依赖隐式默认会在函数默认值变化时静默丢掉货币符号。
            else FF.animateCount(valueNode, value, { duration: 1100, digits: 2, prefix: '¥' });
            return box;
          })
        ),
      ])
    );

    /* ================================================================
       模块 A：财务税务整体状况
       ================================================================ */
    const sectionA = el('div', { style: { marginTop: '30px' } }, [
      el('div', { style: { display: 'flex', alignItems: 'center', gap: '13px', marginBottom: '18px' } }, [
        el('div', {
          text: 'A',
          style: {
            width: '36px',
            height: '36px',
            borderRadius: '10px',
            display: 'grid',
            placeItems: 'center',
            fontWeight: '700',
            fontSize: '17px',
            background: 'var(--firefly-soft)',
            color: 'var(--firefly)',
            border: '1px solid var(--border-glow)',
          },
        }),
        el('div', {}, [
          el('h2', { text: '财务税务整体状况', style: { fontSize: '19px' } }),
          el('div.field-hint', { text: '收支结构、预算执行、环比归因与税务风险全貌' }),
        ]),
      ]),
    ]);

    /* ---- A1 高亮指标 ---- */
    if (payload.highlights && payload.highlights.length) {
      sectionA.appendChild(
        el('div.highlight-strip', {}, payload.highlights.map(function (h, i) {
          return el('div.highlight-item', { dataset: { tone: h.tone }, style: { animationDelay: i * 60 + 'ms' } }, [
            el('div.highlight-label', { text: h.label }),
            el('div.highlight-value', { text: h.value }),
            el('div.highlight-text', { text: h.text }),
          ]);
        }))
      );
    }

    /* ---- A2 图表区 ---- */
    const trendCanvas = el('canvas.chart-canvas');
    const attrCanvas = el('canvas.chart-canvas');
    const structureCanvas = el('canvas.chart-canvas');

    sectionA.appendChild(
      el('div.grid.grid-2', { style: { marginTop: '14px' } }, [
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '📈' }), el('span', { text: '支出走势' })]),
          ]),
          el('div.chart-box', { style: { minHeight: '300px' } }, [trendCanvas]),
        ]),
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🪜' }), el('span', { text: '支出变化归因' })]),
            el('span.card-desc', { text: '涨在哪里，一目了然' }),
          ]),
          el('div.chart-box', { style: { minHeight: '300px' } }, [attrCanvas]),
        ]),
      ])
    );

    sectionA.appendChild(
      el('div.grid.grid-sidebar', { style: { marginTop: '20px' } }, [
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🗂️' }), el('span', { text: '类别结构明细' })]),
          ]),
          buildCategoryBars(payload.categories || []),
        ]),
        el('div.card.reveal', {}, [
          el('div.card-header', {}, [
            el('div.card-title', {}, [el('span.icon', { text: '🍩' }), el('span', { text: '占比分布' })]),
          ]),
          el('div.chart-box', { style: { minHeight: '260px' } }, [structureCanvas]),
        ]),
      ])
    );

    /* ---- A3 预算执行 ---- */
    const budgetCard = el('div.card.reveal', { style: { marginTop: '20px' } }, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '🎯' }), el('span', { text: '预算 vs 实际' })]),
        el('span.card-desc', { text: '超支与临界类别优先关注' }),
      ]),
      buildBudgetTable(payload.budgets || []),
    ]);
    sectionA.appendChild(budgetCard);

    /* ---- A4 税务风险自检 ---- */
    const taxCard = el('div.card.reveal', { style: { marginTop: '20px' } }, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '🧾' }), el('span', { text: '税务风险自检' })]),
        el('span.tag.is-firefly', { text: (payload.tax_checks || []).length + ' 项检查' }),
      ]),
      buildTaxChecks(payload.tax_checks || []),
    ]);
    sectionA.appendChild(taxCard);

    /* ---- A5 AI 总述 ---- */
    const summaryNode = el('div.typewriter');
    const summaryCard = el('div.card.reveal', { style: { marginTop: '20px' } }, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📝' }), el('span', { text: '总体状况综述' })]),
        el('span.tag.is-info', { text: 'AI 生成' }),
      ]),
      summaryNode,
    ]);
    sectionA.appendChild(summaryCard);

    root.appendChild(sectionA);

    /* ================================================================
       模块 B：财务税务优化方向
       ================================================================ */
    const sectionB = el('div', { style: { marginTop: '34px' } }, [
      el('div', { style: { display: 'flex', alignItems: 'center', gap: '13px', marginBottom: '18px' } }, [
        el('div', {
          text: 'B',
          style: {
            width: '36px',
            height: '36px',
            borderRadius: '10px',
            display: 'grid',
            placeItems: 'center',
            fontWeight: '700',
            fontSize: '17px',
            background: 'rgba(244,114,182,0.14)',
            color: 'var(--sakura)',
            border: '1px solid rgba(244,114,182,0.35)',
          },
        }),
        el('div', {}, [
          el('h2', { text: '财务税务优化方向', style: { fontSize: '19px' } }),
          el('div.field-hint', { text: '按优先级排序可执行动作，附预期效果与政策依据' }),
        ]),
      ]),
    ]);

    const actions = payload.actions || [];
    if (actions.length) {
      const highCount = actions.filter(function (a) { return a.priority === 'high'; }).length;
      sectionB.appendChild(
        el('div.grid.grid-3', { style: { marginBottom: '20px' } }, [
          buildStatBox('待处理行动', actions.length + ' 项', '按优先级排序', 'var(--firefly)'),
          buildStatBox('高优先事项', highCount + ' 项', '建议本周期内完成', 'var(--danger)'),
          buildStatBox(
            '预计可优化',
            FF.formatMoney(
              actions.filter(function (a) { return a.priority !== 'low'; }).length * 3500,
              { digits: 0 }
            ),
            '含税负规避与扣除增加',
            'var(--success)'
          ),
        ])
      );
    }

    const actionList = el('div');
    actions.forEach(function (a, i) {
      const priorityText = a.priority === 'high' ? '高优先' : a.priority === 'medium' ? '中优先' : '可延后';
      const tagClass = a.priority === 'high' ? 'is-danger' : a.priority === 'medium' ? 'is-warning' : 'is-info';

      actionList.appendChild(
        el('div.action-item', { dataset: { priority: a.priority }, style: { animationDelay: i * 50 + 'ms' } }, [
          el('div.action-priority'),
          el('div.action-body', {}, [
            el('div.action-title', {}, [
              el('span', { text: a.title }),
              el('span.tag.' + tagClass, { text: priorityText }),
            ]),
            el('div.action-detail', { text: a.detail }),
            el('div.action-do', { text: a.action }),
            a.impact ? el('div.field-hint', { text: '预期效果：' + a.impact, style: { marginTop: '7px' } }) : null,
          ].filter(Boolean)),
        ])
      );
    });

    sectionB.appendChild(
      el('div.card.reveal', {}, [
        el('div.card-header', {}, [
          el('div.card-title', {}, [el('span.icon', { text: '✨' }), el('span', { text: '优化行动清单' })]),
        ]),
        actionList,
      ])
    );

    sectionB.appendChild(
      el('div.card.reveal', {
        style: {
          marginTop: '20px',
          fontSize: '11.5px',
          color: 'var(--text-muted)',
          lineHeight: '1.75',
        },
      }, [
        el('div', { style: { fontWeight: '600', marginBottom: '7px', color: 'var(--text-secondary)' }, text: '免责声明' }),
        el('div', {
          text:
            '本报告由「流萤」依据用户录入的交易数据自动生成，所引用的税收政策口径为通用参考，' +
            '未考虑企业具体情况与地方执行差异，不构成正式税务鉴证或纳税申报意见。' +
            '涉及申报与扣除调整的事项，请以主管税务机关口径为准，必要时咨询专业税务顾问。',
        }),
      ])
    );

    root.appendChild(sectionB);

    /* ---- 动画渲染 ---- */
    window.requestAnimationFrame(function () {
      FF.drawLineChart(trendCanvas, {
        labels: (payload.trend || []).map(function (t) {
          return FF.formatDate(t.date, 'MM-DD');
        }),
        series: [
          {
            name: '支出',
            color: FF.cssVar('--firefly', '#5eead4'),
            values: (payload.trend || []).map(function (t) {
              return t.expense;
            }),
          },
        ],
      });

      FF.drawWaterfallChart(attrCanvas, payload.attribution || [], {
        startLabel: '上期支出',
        // 归一化时已把后端的两根总额柱拆出来，优先用它们；
        // mock 数据没有这两个字段，回退到 overview 的环比值。
        startValue:
          payload.attribution_start_value !== undefined
            ? payload.attribution_start_value
            : o.prev_expense || 0,
        endValue:
          payload.attribution_end_value !== undefined
            ? payload.attribution_end_value
            : o.expense || 0,
      });

      const palette = ['#f472b6', '#38bdf8', '#5eead4', '#a78bfa', '#fbbf24', '#fb923c', '#60a5fa', '#4ade80'];
      FF.drawDonutChart(
        structureCanvas,
        (payload.categories || []).slice(0, 8).map(function (c, i) {
          return { value: c.amount, color: palette[i % palette.length] };
        }),
        {
          centerValue: FF.formatMoney(o.expense || 0, { symbol: '', digits: 0, compact: true }).trim(),
          centerLabel: '总支出',
        }
      );

      // 类别条动画
      FF.$$('.bar-fill[data-target]').forEach(function (bar, i) {
        window.setTimeout(function () {
          bar.style.width = bar.dataset.target + '%';
          bar.style.backgroundColor = bar.dataset.color || 'var(--firefly)';
        }, 200 + i * 70);
      });

      // 预算条动画
      FF.$$('.progress-bar[data-target]').forEach(function (bar, i) {
        window.setTimeout(function () {
          bar.style.width = bar.dataset.target + '%';
        }, 200 + i * 70);
      });
    });

    // 综述完整呈现，便于阅读与打印
    if (summaryNode) {
      renderSummary(summaryNode, payload.summary_text || '暂无综述内容。');
    }

    utils.observeReveal(root);
  }

  function buildStatBox(label, value, hint, color) {
    return el('div', {
      style: {
        padding: '16px 18px',
        borderRadius: '14px',
        border: '1px solid var(--border)',
        background: 'var(--bg-card)',
      },
    }, [
      el('div.field-hint', { text: label, style: { marginBottom: '6px' } }),
      el('div', { text: value, style: { fontSize: '21px', fontWeight: '700', color: color } }),
      el('div.field-hint', { text: hint, style: { marginTop: '5px' } }),
    ]);
  }

  function buildCategoryBars(categories) {
    if (!categories.length) {
      return el('div.empty', {}, [
        el('div.empty-icon', { text: '📊' }),
        el('div.empty-title', { text: '本期暂无支出记录' }),
      ]);
    }

    const maxAmount = categories[0] ? categories[0].amount : 1;
    const box = el('div.bar-list');

    categories.slice(0, 10).forEach(function (c, i) {
      const ratio = maxAmount > 0 ? (c.amount / maxAmount) * 100 : 0;
      const color = c.color || ['#f472b6', '#38bdf8', '#5eead4', '#a78bfa', '#fbbf24'][i % 5];

      box.appendChild(
        el('div.bar-item', { style: { animationDelay: i * 60 + 'ms' } }, [
          el('div.bar-head', {}, [
            el('div.bar-name', {}, [
              el('span', { text: c.icon || '📦' }),
              el('span', { text: c.name }),
            ]),
            el('div.bar-amount', {
              html:
                '<strong>' + FF.formatMoney(c.amount, { digits: 0 }) + '</strong> · ' +
                FF.formatPercent(c.ratio) + ' · ' + c.count + ' 笔',
            }),
          ]),
          el('div.bar-track', {}, [
            el('div.bar-fill', {
              style: { backgroundColor: color },
              dataset: { target: ratio, color: color },
            }),
          ]),
        ])
      );
    });

    return box;
  }

  function buildBudgetTable(budgets) {
    // 后端 budget.items 的形状是 {budget_id, name, icon, budget, actual,
    // usage_ratio, state}，与表格渲染需要的字段名不同，这里统一归一化。
    const rows = budgets
      .map(function (b) {
        return {
          id: b.budget_id !== undefined ? b.budget_id : b.id,
          category_name: b.category_name || b.name || '未命名类别',
          icon: b.icon || '📦',
          budget: Number(b.budget !== undefined ? b.budget : b.amount) || 0,
          used: Number(b.actual !== undefined ? b.actual : b.used) || 0,
          ratio:
            b.ratio !== undefined && b.ratio !== null
              ? Number(b.ratio)
              : b.usage_ratio !== undefined && b.usage_ratio !== null
              ? Number(b.usage_ratio)
              : b.budget > 0
              ? (Number(b.actual || 0) / Number(b.budget)) * 100
              : 0,
          status: b.status || b.state,
        };
      })
      // 过滤「未设预算的类别」（budget_id 为 null、state 为 unbudgeted）：
      // 它们不是用户设定的预算，混进表里只会出现一排 ¥0/¥0 的行。
      .filter(function (b) {
        return b.id !== undefined && b.id !== null && b.status !== 'unbudgeted';
      });

    if (!rows.length) {
      return el('div.empty', {}, [
        el('div.empty-icon', { text: '🎯' }),
        el('div.empty-title', { text: '尚未设置预算' }),
        el('div.empty-desc', { text: '前往「预算」页面为常用类别设定额度，流萤会在达到 80% 时预警。' }),
      ]);
    }

    const table = el('table.data-table');
    table.appendChild(
      el('thead', {}, [
        el('tr', {}, [
          el('th', { text: '类别' }),
          el('th', { text: '预算', style: { textAlign: 'right' } }),
          el('th', { text: '实际', style: { textAlign: 'right' } }),
          el('th', { text: '执行率' }),
          el('th', { text: '状态' }),
        ]),
      ])
    );

    const tbody = el('tbody');
    rows.forEach(function (b) {
      const statusClass = b.status === 'over' ? 'is-over' : b.status === 'warn' ? 'is-warn' : 'is-ok';
      const tagClass = b.status === 'over' ? 'is-danger' : b.status === 'warn' ? 'is-warning' : 'is-success';
      const tagText = b.status === 'over' ? '已超支' : b.status === 'warn' ? '接近上限' : '正常';
      const barClass = b.status === 'over' ? 'is-over' : b.status === 'warn' ? 'is-warn' : '';

      tbody.appendChild(
        el('tr', { class: statusClass }, [
          el('td', {}, [
            el('span', { text: (b.icon || '📦') + ' ' + b.category_name }),
          ]),
          el('td.num', { text: FF.formatMoney(b.budget, { digits: 0 }) }),
          el('td.num', { text: FF.formatMoney(b.used, { digits: 0 }), style: { fontWeight: '600' } }),
          el('td', { style: { minWidth: '150px' } }, [
            el('div.progress', {}, [
              el('div.progress-bar.' + barClass, { style: { width: '0%' }, dataset: { target: Math.min(b.ratio, 100) } }),
            ]),
            el('div.field-hint', { text: FF.formatPercent(b.ratio), style: { marginTop: '5px' } }),
          ]),
          el('td', {}, [el('span.tag.' + tagClass, { text: tagText })]),
        ])
      );
    });

    table.appendChild(tbody);
    return el('div.table-wrap', {}, [table]);
  }

  function buildTaxChecks(checks) {
    if (!checks.length) {
      return el('div.empty', {}, [
        el('div.empty-icon', { text: '✅' }),
        el('div.empty-title', { text: '未发现税务风险项' }),
      ]);
    }

    // 高风险优先
    const order = { high: 0, medium: 1, low: 2, info: 3 };
    const sorted = checks.slice().sort(function (a, b) {
      return (order[a.level] === undefined ? 9 : order[a.level]) - (order[b.level] === undefined ? 9 : order[b.level]);
    });

    const box = el('div');
    sorted.forEach(function (c) {
      const levelTag = c.level === 'high' ? 'is-danger' : c.level === 'medium' ? 'is-warning' : 'is-info';
      const levelText = c.level === 'high' ? '高风险' : c.level === 'medium' ? '中风险' : '低风险';

      box.appendChild(
        el('div.check-item', { dataset: { level: c.level } }, [
          el('div.check-head', {}, [
            el('div.check-title', {}, [
              el('span', { text: c.level === 'high' ? '🚨' : c.level === 'medium' ? '⚠️' : 'ℹ️' }),
              el('span', { text: c.title }),
            ]),
            el('div', { style: { display: 'flex', alignItems: 'center', gap: '9px' } }, [
              c.actual_value !== undefined && c.actual_value !== null
                ? el('span.bar-amount', {
                    html: '实测 <strong>' + formatCheckValue(c) + '</strong>' +
                      (c.threshold_value ? ' / 参考 ' + FF.formatNumber(c.threshold_value, 2) + (c.unit || '') : ''),
                  })
                : null,
              el('span.tag.' + levelTag, { text: levelText }),
            ].filter(Boolean)),
          ]),
          el('div.check-desc', { text: c.description }),
          el('div.check-suggestion', { text: c.suggestion }),
          c.policy_basis ? el('div.check-policy', { text: '政策依据：' + c.policy_basis }) : null,
        ].filter(Boolean))
      );
    });

    return box;
  }

  function formatCheckValue(c) {
    const v = Number(c.actual_value) || 0;
    const unit = c.unit || '';
    if (unit === '%') return v.toFixed(2) + '%';
    return FF.formatNumber(v, 2) + unit;
  }

  /* ============================================================
     综述阅读区
     ============================================================ */
  function renderSummary(node, text) {
    // A report is a reading surface: show the full summary immediately instead
    // of keeping an observer and a five-second fallback timer for hidden pages.
    node.textContent = text;
  }

  /* ============================================================
     记账后提示刷新
     ============================================================ */
  window.addEventListener('firefly:transaction-created', function () {
    if (window.FFRouter && window.FFRouter.current() === 'report' && state.payload) {
      FF.toast('台账已更新，可点击「生成报告」刷新', 'info');
    }
  });
})();
