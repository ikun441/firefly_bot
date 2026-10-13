/* ==========================================================================
   流萤 Firefly · 智能助手视图
   --------------------------------------------------------------------------
   对话式查询（函数调用式参数抽取，非裸 NL2SQL）+ 异常检测 + 建议采纳率。
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

  const state = {
    messages: [],
    asking: false,
    anomalies: [],
  };

  /* ============================================================
     渲染
     ============================================================ */
  Views.assistant = {
    async render(container) {
      container.appendChild(
        el('div.page-head', {}, [
          el('h1.page-title', {}, [el('span', { text: '🔮' }), el('span', { text: '智能助手' })]),
          el('p.page-subtitle', {
            text: '用自然语言查询账目、发现异常、获取建议。所有查询通过受控参数抽取执行，不会生成任意 SQL。',
          }),
        ])
      );

      const layout = el('div.grid.grid-sidebar');
      container.appendChild(layout);

      const mainColumn = el('div.stack');
      const sideColumn = el('div.stack');
      layout.appendChild(mainColumn);
      layout.appendChild(sideColumn);

      /* ---- 对话卡 ---- */
      mainColumn.appendChild(buildChatCard());

      /* ---- 异常检测卡 ---- */
      mainColumn.appendChild(await buildAnomalyCard());

      /* ---- 建议采纳率卡 ---- */
      sideColumn.appendChild(await buildAdoptionCard());

      /* ---- 能力说明卡 ---- */
      sideColumn.appendChild(buildCapabilityCard());
    },
  };

  /* ============================================================
     对话
     ============================================================ */
  function buildChatCard() {
    const messageList = el('div', {
      style: {
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        maxHeight: '460px',
        overflowY: 'auto',
        paddingRight: '6px',
        marginBottom: '16px',
      },
    });

    const input = el('input.input', {
      placeholder: '例如：上月餐饮花了多少？ 本月预算还剩多少？',
      on: {
        keydown: function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            send();
          }
        },
      },
    });

    const sendBtn = el('button.btn.btn-primary', {
      html: '发送',
      on: {
        click: function () {
          send();
        },
      },
    });

    const suggestions = el('div.quick-amounts', { style: { marginTop: '11px' } },
      [
        '上月餐饮花了多少？',
        '本期总支出是多少？',
        '有哪些异常支出？',
        '餐饮预算还剩多少？',
      ].map(function (text) {
        return el('button.quick-amount', {
          text: text,
          on: {
            click: function () {
              input.value = text;
              send();
            },
          },
        });
      })
    );

    // 欢迎语
    appendMessage(messageList, 'assistant', {
      answer:
        '你好，我是流萤。可以问我账目问题，也可以用受控查询帮你看预算、异常与趋势。\n试试点击下方的示例问题。',
      isWelcome: true,
    });

    async function send() {
      const question = input.value.trim();
      if (!question) {
        FF.toast('请输入问题', 'warning');
        return;
      }
      if (state.asking) return;

      state.asking = true;
      input.value = '';
      sendBtn.classList.add('is-loading');

      appendMessage(messageList, 'user', { answer: question });

      const loadingNode = appendMessage(messageList, 'assistant', { answer: '正在检索账目…', isLoading: true });
      messageList.scrollTop = messageList.scrollHeight;

      try {
        const result = await withFallback(
          function () {
            return api.ask(question);
          },
          function () {
            return Object.assign({}, Mock.ASK_RESPONSE, { question: question });
          }
        );

        loadingNode.remove();
        appendMessage(messageList, 'assistant', result.data, result.mock);
      } catch (err) {
        loadingNode.remove();
        appendMessage(messageList, 'assistant', {
          answer: '查询失败：' + (err.message || '未知错误') + '\n请检查后端服务与 LLM 配置。',
          isError: true,
        });
      } finally {
        state.asking = false;
        sendBtn.classList.remove('is-loading');
        input.focus();
        messageList.scrollTop = messageList.scrollHeight;
      }
    }

    return el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '💬' }), el('span', { text: '对话式查询' })]),
        el('span.tag.is-firefly', { text: '受控函数调用' }),
      ]),
      messageList,
      el('div', { style: { display: 'flex', gap: '10px' } }, [input, sendBtn]),
      suggestions,
    ]);
  }

  function appendMessage(list, role, data, isMock) {
    const isUser = role === 'user';

    const node = el('div', {
      style: {
        display: 'flex',
        gap: '12px',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
      },
    });

    const avatar = el('div', {
      text: isUser ? '👤' : '🪄',
      style: {
        flexShrink: '0',
        width: '34px',
        height: '34px',
        borderRadius: '9px',
        display: 'grid',
        placeItems: 'center',
        fontSize: '17px',
        background: isUser ? 'rgba(56,189,248,0.14)' : 'var(--firefly-soft)',
        border: '1px solid var(--border)',
      },
    });

    const bubble = el('div', {
      style: {
        flex: '1',
        minWidth: '0',
        padding: '13px 16px',
        borderRadius: '13px',
        border: '1px solid var(--border)',
        background: isUser ? 'rgba(56,189,248,0.07)' : 'var(--bg-elevated)',
        fontSize: '13.5px',
        lineHeight: '1.72',
      },
    });

    if (data.isLoading) {
      bubble.appendChild(
        el('div', { style: { display: 'flex', alignItems: 'center', gap: '9px', color: 'var(--text-muted)' } }, [
          el('span.scan-orb-core', { text: '🔍', style: { fontSize: '15px' } }),
          el('span', { text: data.answer }),
        ])
      );
    } else {
      const textNode = el('div', {
        text: data.answer,
        style: { whiteSpace: 'pre-wrap', color: data.isError ? 'var(--danger)' : 'var(--text-primary)' },
      });
      bubble.appendChild(textNode);

      // 结构化数据
      if (data.data && typeof data.data === 'object') {
        const entries = Object.keys(data.data)
          .filter(function (k) {
            const v = data.data[k];
            return v !== null && v !== undefined && typeof v !== 'object';
          })
          .slice(0, 4);

        if (entries.length) {
          const grid = el('div', {
            style: {
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
              gap: '9px',
              marginTop: '12px',
            },
          });

          entries.forEach(function (key) {
            const value = data.data[key];
            const label = LABELS[key] || key;
            const display =
              typeof value === 'number'
                ? key.indexOf('ratio') >= 0 || key.indexOf('rate') >= 0 || key.indexOf('delta') >= 0
                  ? FF.formatPercent(value)
                  : FF.formatNumber(value, 2)
                : String(value);

            grid.appendChild(
              el('div', {
                style: {
                  padding: '10px 12px',
                  borderRadius: '9px',
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                },
              }, [
                el('div.field-hint', { text: label, style: { marginBottom: '3px' } }),
                el('div', { text: display, style: { fontWeight: '700', fontSize: '15px' } }),
              ])
            );
          });

          bubble.appendChild(grid);
        }
      }

      // 命中意图
      if (data.intent) {
        bubble.appendChild(
          el('div', { style: { marginTop: '11px', display: 'flex', gap: '7px', flexWrap: 'wrap' } }, [
            el('span.tag.is-info', { text: '意图：' + (INTENT_LABELS[data.intent] || data.intent) }),
            data.params
              ? el('span.tag', {
                  text: '参数：' + Object.keys(data.params).map(function (k) {
                    return k + '=' + data.params[k];
                  }).join(', '),
                })
              : null,
            isMock ? el('span.tag.is-warning', { text: '示例数据' }) : null,
          ].filter(Boolean))
        );
      }
    }

    node.appendChild(avatar);
    node.appendChild(bubble);
    list.appendChild(node);
    return node;
  }

  const LABELS = {
    amount: '金额',
    count: '笔数',
    ratio: '占比',
    delta: '环比',
    balance: '余额',
    total: '合计',
  };

  const INTENT_LABELS = {
    query_category_expense: '查询类别支出',
    query_total_expense: '查询总支出',
    query_budget_remaining: '查询预算余额',
    query_anomalies: '查询异常支出',
    query_trend: '查询趋势',
  };

  /* ============================================================
     异常检测
     ============================================================ */
  async function buildAnomalyCard() {
    const body = el('div');

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '🚨' }), el('span', { text: '异常检测' })]),
        el('span.tag.is-firefly', { text: 'Z-Score' }),
      ]),
      body,
    ]);

    body.appendChild(el('div.skeleton', { style: { height: '120px', borderRadius: '12px' } }));

    const result = await withFallback(
      function () {
        return api.anomalies({ limit: 10 });
      },
      function () {
        return Mock.TRANSACTIONS.filter(function (t) {
          return t.is_anomaly;
        });
      }
    );

    const list = utils.normalizeList(result.data);
    FF.clear(body);

    if (!list.length) {
      body.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '✅' }),
          el('div.empty-title', { text: '本期未检测到异常' }),
          el('div.empty-desc', { text: '系统按类别计算历史均值与标准差，单笔偏离超过阈值时才会提示。' }),
        ])
      );
      return card;
    }

    list.forEach(function (a) {
      const severity = a.severity || (a.is_personal_mixed ? 'high' : 'medium');
      const levelTag = severity === 'high' ? 'is-danger' : severity === 'medium' ? 'is-warning' : 'is-info';

      body.appendChild(
        el('div.check-item', { dataset: { level: severity } }, [
          el('div.check-head', {}, [
            el('div.check-title', {}, [
              el('span', { text: a.is_personal_mixed ? '👤' : '📈' }),
              el('span', {
                text: FF.formatDate(a.trade_time, 'MM-DD HH:mm') + ' · ' + (a.category_name || '未分类'),
              }),
            ]),
            el('div', { style: { display: 'flex', alignItems: 'center', gap: '9px' } }, [
              el('span.bar-amount', {
                html: '<strong>' + FF.formatMoney(a.amount || 0, { digits: 0 }) + '</strong>',
              }),
              el('span.tag.' + levelTag, {
                text: a.is_personal_mixed ? '疑似个人消费' : severity === 'high' ? '高异常' : '中异常',
              }),
            ]),
          ]),
          el('div.check-desc', { text: a.anomaly_reason || a.reason || '金额显著偏离该类别历史水平' }),
          a.memo ? el('div.field-hint', { text: '备注：' + a.memo }) : null,
          el('div.check-suggestion', {
            text: a.suggestion || '建议核实该笔支出的业务关联性，补充凭证或做相应税务调整。',
          }),
        ].filter(Boolean))
      );
    });

    return card;
  }

  /* ============================================================
     建议采纳率
     ============================================================ */
  async function buildAdoptionCard() {
    const body = el('div');

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📈' }), el('span', { text: 'AI 建议采纳率' })]),
        el('span.tag.is-iberty', { text: '评测指标' }),
      ]),
      body,
    ]);

    const result = await withFallback(
      function () {
        return api.auditStats();
      },
      function () {
        return Mock.AUDIT_STATS;
      }
    );

    const stats = result.data || {};

    const mainValue = el('div', {
      text: '0.0%',
      style: { fontSize: '38px', fontWeight: '700', color: 'var(--firefly)', fontVariantNumeric: 'tabular-nums' },
    });

    body.appendChild(
      el('div', { style: { textAlign: 'center', padding: '12px 0 18px' } }, [
        mainValue,
        el('div.field-hint', { text: '建议被采纳的比例', style: { marginTop: '5px' } }),
      ])
    );

    FF.animateCount(mainValue, Number(stats.adoption_rate) || 0, {
      duration: 1200,
      digits: 1,
      prefix: '',
      suffix: '%',
    });

    // 对照组对比（论文可量化指标）
    if (stats.with_ai_rate !== undefined) {
      body.appendChild(
        el('div', {
          style: {
            padding: '14px 16px',
            borderRadius: '11px',
            border: '1px solid var(--border)',
            background: 'var(--bg-elevated)',
            marginBottom: '16px',
          },
        }, [
          el('div.field-hint', { text: '对照组实验（有 AI 建议 vs 无）', style: { marginBottom: '11px' } }),
          buildCompareBar('有 AI 建议', stats.with_ai_rate, 'var(--firefly)'),
          buildCompareBar('无 AI 建议', stats.without_ai_rate, 'var(--text-muted)'),
        ])
      );
    }

    // 分类别采纳率
    const byKind = stats.by_kind || [];
    if (byKind.length) {
      const kindBox = el('div');
      byKind.forEach(function (k) {
        kindBox.appendChild(
          el('div', { style: { marginBottom: '13px' } }, [
            el('div.bar-head', {}, [
              el('div.bar-name', { text: k.label }),
              el('div.bar-amount', {
                html: '<strong>' + (Number(k.rate) || 0).toFixed(1) + '%</strong> · ' +
                  k.adopted + '/' + k.total,
              }),
            ]),
            el('div.progress', {}, [
              el('div.progress-bar', { style: { width: '0%' }, dataset: { target: Number(k.rate) || 0 } }),
            ]),
          ])
        );
      });
      body.appendChild(kindBox);

      window.requestAnimationFrame(function () {
        FF.$$('.progress-bar[data-target]', body).forEach(function (bar, i) {
          window.setTimeout(function () {
            bar.style.width = bar.dataset.target + '%';
          }, 220 + i * 90);
        });
      });
    }

    // 汇总
    const summary = [
      { label: '建议总数', value: stats.total_suggestions || 0 },
      { label: '已采纳', value: stats.adopted || 0 },
      { label: '已拒绝', value: stats.rejected || 0 },
      { label: '待处理', value: stats.pending || 0 },
    ];

    body.appendChild(
      el('div', {
        style: {
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '9px',
          marginTop: '4px',
        },
      }, summary.map(function (s) {
        return el('div', {
          style: {
            padding: '11px 13px',
            borderRadius: '10px',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
          },
        }, [
          el('div.field-hint', { text: s.label, style: { marginBottom: '3px' } }),
          el('div', { text: String(s.value), style: { fontWeight: '700', fontSize: '17px' } }),
        ]);
      }))
    );

    body.appendChild(
      el('div.field-hint', {
        text: '采纳率由审计日志自动统计，是衡量 AI 建议有效性的核心指标。',
        style: { marginTop: '14px', lineHeight: '1.7' },
      })
    );

    return card;
  }

  function buildCompareBar(label, value, color) {
    const bar = el('div', {
      style: {
        height: '100%',
        width: '0%',
        borderRadius: '999px',
        backgroundColor: color,
        transition: 'width 900ms cubic-bezier(0.4,0,0.2,1)',
        dataset: { target: Math.min(Number(value) || 0, 100) },
      },
    });

    window.setTimeout(function () {
      bar.style.width = bar.dataset.target + '%';
    }, 200);

    return el('div', { style: { marginBottom: '11px' } }, [
      el('div', {
        style: { display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '5px' },
      }, [
        el('span', { text: label, style: { color: 'var(--text-secondary)' } }),
        el('span', { text: (Number(value) || 0).toFixed(1) + '%', style: { fontWeight: '600' } }),
      ]),
      el('div', { style: { height: '7px', borderRadius: '999px', background: 'rgba(148,163,184,0.13)', overflow: 'hidden' } }, [bar]),
    ]);
  }

  /* ============================================================
     能力说明
     ============================================================ */
  function buildCapabilityCard() {
    const capabilities = [
      { icon: '🎯', title: '受控查询', desc: '问题被抽取为结构化参数，再走受控查询，不生成任意 SQL' },
      { icon: '🏷️', title: '智能分类', desc: 'LLM 冷启动 + 用户纠正 few-shot，越用越准' },
      { icon: '🚨', title: '异常检测', desc: '按类别计算均值与标准差，识别大额与重复记账' },
      { icon: '🧾', title: '税务风险', desc: '税负率、招待费限额、无票支出等 6 项自检' },
      { icon: '📊', title: '归因分析', desc: '瀑布图拆解支出变化，回答「涨在哪里」' },
      { icon: '🔔', title: '主动预警', desc: '单日超限、预算临界、余额不足时推送提醒' },
    ];

    return el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '🧩' }), el('span', { text: '助手能力' })]),
      ]),
      el('div', { style: { display: 'flex', flexDirection: 'column', gap: '13px' } },
        capabilities.map(function (c) {
          return el('div', { style: { display: 'flex', gap: '12px', alignItems: 'flex-start' } }, [
            el('span', { text: c.icon, style: { fontSize: '17px', flexShrink: '0' } }),
            el('div', {}, [
              el('div', { text: c.title, style: { fontWeight: '600', fontSize: '13.5px', marginBottom: '2px' } }),
              el('div.field-hint', { text: c.desc, style: { lineHeight: '1.6' } }),
            ]),
          ]);
        })
      ),
    ]);
  }
})();
