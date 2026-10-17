/* ==========================================================================
   流萤 Firefly · 财务数据收集器视图
   --------------------------------------------------------------------------
   手动记账 + 自然语言记账 + 带筛选的交易台账。
   记账后广播 firefly:transaction-created，报告页据此提示刷新。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const el = FF.el;
  const api = window.FFApi.api;
  const withFallback = window.FFApi.withFallback;
  const Mock = window.FFMock;
  const Views = window.FFViews;

  /**
   * 把 id 统一成数字，无法转换时原样返回。
   *
   * 存在的理由：后端返回的 id 是数字，而 ``<select>`` / ``<option>``
   * 的 value 永远是字符串。两者用 ``===`` 比较永远为 false，
   * 于是「选了类别却提示未选」「回填时选中项显示不出来」这类问题
   * 会反复出现。与其在每个比较点各写一遍，不如在入口处统一。
   */
  function toId(value) {
    if (value === null || value === undefined || value === '') return '';
    const n = Number(value);
    return Number.isNaN(n) ? value : n;
  }

  /* ============================================================
     状态
     ============================================================ */
  const state = {
    direction: 'expense',
    category_id: '',
    amount: '',
    memo: '',
    trade_time: new Date().toISOString().slice(0, 16),
    // 关联账本（资金账户）。空字符串表示「尚未选定」，由 refreshAccounts()
    // 落到真实账户 id 上。
    //
    // 这里曾经硬编码成 'acc-main' —— 一个后端根本不存在的假 id。
    // 后果不只是这一笔记不上账：后端 _as_int('acc-main') 得到 None，
    // 流水落库时 account_id 为空，于是**永远不进任何账户的余额**，
    // 总览页的「账户余额」恒为 ¥0.00，而用户完全看不出哪里错了。
    // 默认值必须是后端真实存在的账户 id，取不到时宁可不选。
    account_id: '',
    has_invoice: true,
    categories: [],
    accounts: [],
    transactions: [],
    filter: { keyword: '', direction: '', category_id: '' },
    nlText: '',
    nlResult: null,
    submitting: false,
  };

  /* ============================================================
     渲染
     ============================================================ */
  Views.collector = {
    async render(container) {
      container.appendChild(
        el('div.page-head', {}, [
          el('h1.page-title', {}, [el('span', { text: '✒️' }), el('span', { text: '财务数据收集器' })]),
          el('p.page-subtitle', {
            text: '用一句话或一个表单完成记账。所有数据进入台账后将自动参与周期报告与税务分析。',
          }),
        ])
      );

      const layout = el('div.grid.grid-sidebar');
      container.appendChild(layout);

      const formColumn = el('div.stack');
      const ledgerColumn = el('div.stack');
      layout.appendChild(formColumn);
      layout.appendChild(ledgerColumn);

      /* ---- 自然语言记账卡 ---- */
      formColumn.appendChild(renderNLCard());

      /* ---- 手动记账卡 ---- */
      formColumn.appendChild(renderFormCard());

      /* ---- 台账卡 ---- */
      ledgerColumn.appendChild(renderLedgerCard());

      /* ---- 快捷统计卡 ---- */
      ledgerColumn.appendChild(renderQuickStatsCard());

      await refreshCategories();
      await refreshLedger();
    },
  };

  /* ============================================================
     自然语言记账
     ============================================================ */
  function renderNLCard() {
    const textarea = el('textarea.textarea', {
      placeholder: '例如：\n昨天打车 38，请客户吃饭 260\n本月房租 6800 已付\n收到客户回款 25000',
      rows: 4,
    });

    const resultBox = el('div', { style: { marginTop: '16px' } });

    const submitBtn = el('button.btn.btn-primary', {
      html: '✨ 智能解析',
      on: {
        click: async function () {
          const text = textarea.value.trim();
          if (!text) {
            FF.toast('请先输入一句话描述', 'warning');
            textarea.focus();
            return;
          }

          submitBtn.classList.add('is-loading');
          FF.clear(resultBox);
          resultBox.appendChild(renderParsingHint());

          try {
            // 正常流程下 refreshAccounts 早已完成；但若用户在页面刚打开、
            // 账户还没回来时就点了「解析」，这里会拿不到账本列表，
            // 确认区就会退化成「暂无可用账本」。等一次即可，失败不阻塞解析。
            if (!state.accounts.length) {
              await refreshAccounts();
            }

            const result = await withFallback(
              function () {
                return api.nlParse(text);
              },
              function () {
                return Mock.NL_RESPONSE;
              }
            );

            state.nlResult = result.data;
            FF.clear(resultBox);
            renderNLResult(resultBox, result.data, result.mock);

            if (result.mock) {
              FF.toast('后端未连接，展示示例解析结果', 'warning');
            } else {
              FF.toast('解析完成，请确认后入账', 'success');
            }
          } catch (err) {
            FF.clear(resultBox);
            resultBox.appendChild(
              el('div.check-item', { dataset: { level: 'high' } }, [
                el('div.check-title', { text: '解析失败' }),
                el('div.check-desc', { text: err.message || '请求出错，请稍后重试' }),
              ])
            );
            FF.toast('解析失败：' + (err.message || '未知错误'), 'error');
          } finally {
            submitBtn.classList.remove('is-loading');
          }
        },
      },
    });

    const examples = ['昨天打车 38，请客户吃饭 260', '本月房租 6800 已付', '收到项目回款 25000'];

    return el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '💬' }), el('span', { text: '自然语言记账' })]),
        el('span.tag.is-firefly', { text: 'AI 抽取' }),
      ]),
      el('p.card-desc', {
        text: '直接描述发生了什么，模型会抽取金额、类别、时间与对方，生成待确认条目。',
        style: { marginBottom: '14px', display: 'block' },
      }),
      textarea,
      el('div.quick-amounts', { style: { marginTop: '10px' } },
        examples.map(function (text) {
          return el('button.quick-amount', {
            text: text,
            on: {
              click: function () {
                textarea.value = text;
                textarea.focus();
              },
            },
          });
        })
      ),
      el('div.btn-group', { style: { marginTop: '16px' } }, [
        submitBtn,
        el('button.btn.btn-ghost', {
          text: '清空',
          on: {
            click: function () {
              textarea.value = '';
              FF.clear(resultBox);
              state.nlResult = null;
            },
          },
        }),
      ]),
      resultBox,
    ]);
  }

  function renderParsingHint() {
    return el('div', {
      style: { display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)', fontSize: '13px' },
    }, [
      el('span.scan-orb-core', { text: '🧠', style: { fontSize: '20px' } }),
      el('span', { text: '正在理解你的描述…' }),
    ]);
  }

  function renderNLResult(box, result, isMock) {
    const items = (result && (result.items || result.transactions)) || [];

    if (!items.length) {
      box.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '🤔' }),
          el('div.empty-title', { text: '未能识别出记账条目' }),
          el('div.empty-desc', { text: '试着补充金额和用途，例如「打车 38 元」。' }),
        ])
      );
      return;
    }

    box.appendChild(
      el('div.card-header', { style: { marginTop: '18px', marginBottom: '12px' } }, [
        el('div.card-title', { style: { fontSize: '14.5px' } }, [
          el('span', { text: '待确认条目' }),
          el('span.tag.is-info', { text: items.length + ' 条' }),
        ]),
        isMock ? el('span.tag.is-warning', { text: '示例数据' }) : null,
      ].filter(Boolean))
    );

    const list = el('div');

    items.forEach(function (item, index) {
      const confidence = Number(item.confidence) || 0;
      const tagClass = confidence >= 0.85 ? 'is-success' : confidence >= 0.6 ? 'is-warning' : 'is-danger';

      // 允许用户修正类别
      const initialCategory =
        item.category_id ||
        (state.categories.filter(function (c) {
          return c.name === (item.category || item.category_name) && c.direction === (item.direction || 'expense');
        })[0] || {}).id ||
        '';

      const entry = {
        direction: item.direction || 'expense',
        amount: Number(item.amount) || 0,
        category_id: initialCategory,
        // 账本要跟随用户在确认区的选择；识别结果里没有 account_id，
        // 所以回落到手动记账当前选中的账户（state.account_id）。
        account_id: toId(item.account_id || state.account_id || ''),
        memo: item.memo || item.raw_text || '',
        trade_time: item.trade_time || new Date().toISOString(),
        counterparty: item.counterparty || '',
        confidence: confidence,
        has_invoice: item.has_invoice === true,
        selected: true,
      };

      const checkbox = el('input', {
        type: 'checkbox',
        checked: true,
        style: { width: '17px', height: '17px', accentColor: 'var(--firefly)', cursor: 'pointer' },
        on: {
          change: function () {
            entry.selected = checkbox.checked;
            row.style.opacity = checkbox.checked ? '1' : '0.45';
          },
        },
      });

      const categorySelect = el('select.select');
      categorySelect.appendChild(el('option', { value: '', text: '未分类', selected: !entry.category_id }));
      state.categories
        .filter(function (c) {
          return c.direction === entry.direction;
        })
        .forEach(function (c) {
          categorySelect.appendChild(
            el('option', { value: c.id, text: (c.icon || '') + ' ' + c.name, selected: String(c.id) === String(entry.category_id) })
          );
        });
      categorySelect.value = String(entry.category_id || '');
      entry.category_id = toId(categorySelect.value);
      categorySelect.addEventListener('change', function () {
        // select.value 永远是字符串，而后端 id 是数字。
        // 这里转回数字，否则后续 c.id === entry.category_id 永远为 false，
        // 表现是「编辑时选中的类别显示不出来 / 保存后类别丢失」。
        entry.category_id = toId(categorySelect.value);
      });

      const amountInput = el('input.input', {
        type: 'number',
        step: '0.01',
        value: entry.amount,
        style: { textAlign: 'right', fontVariantNumeric: 'tabular-nums' },
      });
      amountInput.addEventListener('input', function () {
        entry.amount = Number(amountInput.value) || 0;
      });

      // 账本下拉：自然语言识别不出「这笔钱从哪个账本出」，
      // 必须让用户在确认时自己选，否则流水会落成无归属
      // （account_id 为空 → 永远不进任何账户余额）。
      const accountSelect = el('select.select');
      if (!state.accounts.length) {
        accountSelect.appendChild(el('option', { value: '', text: '暂无可用账本' }));
        accountSelect.disabled = true;
      } else {
        state.accounts.forEach(function (acc) {
          const label = acc.kind_label
            ? acc.name + '（' + acc.kind_label + '）'
            : acc.name;
          accountSelect.appendChild(
            el('option', {
              value: String(acc.id),
              text: label,
              selected: String(acc.id) === String(entry.account_id),
            })
          );
        });
        // 若 state.account_id 不在列表里（例如账户刚被删），
        // 回落到第一项，避免 select.value 与 entry.account_id 不一致。
        const hit = state.accounts.filter(function (a) {
          return String(a.id) === String(entry.account_id);
        })[0];
        if (!hit) entry.account_id = toId(state.accounts[0].id);
      }
      accountSelect.addEventListener('change', function () {
        entry.account_id = toId(accountSelect.value);
      });

      const memoInput = el('input.input', { value: entry.memo });
      memoInput.addEventListener('input', function () {
        entry.memo = memoInput.value;
      });
      const invoiceCheckbox = el('input', { type: 'checkbox', checked: entry.has_invoice });
      invoiceCheckbox.addEventListener('change', function () { entry.has_invoice = invoiceCheckbox.checked; });

      const row = el('div.budget-item', {}, [
        el('div', { style: { display: 'flex', gap: '13px', alignItems: 'flex-start' } }, [
          el('div', { style: { paddingTop: '4px' } }, [checkbox]),
          el('div', { style: { flex: '1', minWidth: '0' } }, [
            el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '9px', flexWrap: 'wrap' } }, [
              el('span.tag.' + (entry.direction === 'expense' ? 'is-danger' : 'is-success'), {
                text: entry.direction === 'expense' ? '支出' : '收入',
              }),
              el('span.tag.' + tagClass, { text: '置信度 ' + (confidence * 100).toFixed(0) + '%' }),
              item.raw_text ? el('span.text-muted', { text: '「' + item.raw_text + '」', style: { fontSize: '12px' } }) : null,
            ].filter(Boolean)),
            el('div.field-row', {}, [
              el('div.field', { style: { marginBottom: '10px' } }, [
                el('label.field-label', { text: '类别' }),
                categorySelect,
              ]),
              el('div.field', { style: { marginBottom: '10px' } }, [
                el('label.field-label', { text: '金额' }),
                amountInput,
              ]),
            ]),
            el('div.field', { style: { marginBottom: '10px' } }, [
              el('label.field-label', { text: '关联账本' }),
              accountSelect,
            ]),
            el('div.field', { style: { marginBottom: '8px' } }, [
              el('label.field-label', { text: '备注' }),
              memoInput,
            ]),
            el('div.field-hint', {
              text: '时间：' + FF.formatDate(entry.trade_time, 'YYYY-MM-DD HH:mm'),
            }),
            el('label.field-label', { style: { marginTop: '12px' } }, [invoiceCheckbox, el('span', { text: '已开票 / 有发票' })]),
            item.remark
              ? el('div.check-suggestion', { text: item.remark, style: { marginTop: '9px' } })
              : null,
          ].filter(Boolean)),
        ]),
      ]);

      entry.ref = entry;
      list.appendChild(row);
      items[index] = entry;
    });

    box.appendChild(list);

    const confirmBtn = el('button.btn.btn-primary', {
      html: '✓ 确认入账',
      on: {
        click: async function () {
          const selected = items.filter(function (i) {
            return i.selected && i.amount > 0;
          });

          if (!selected.length) {
            FF.toast('请至少选择一条有效记录', 'warning');
            return;
          }

          confirmBtn.classList.add('is-loading');
          confirmBtn.disabled = true;

          try {
            const payload = selected.map(function (i) {
              return {
                direction: i.direction,
                amount: i.amount,
                // 空串要转成 null 再发：后端 category_id 是整数列，
                // 传 '' 会被 pydantic 判为无法解析成整数 → 422 → 整批确认失败。
                // 类别名匹配不到时 initialCategory 就是 ''，这条路径很常见。
                category_id: i.category_id === '' ? null : i.category_id,
                account_id: i.account_id === '' ? null : i.account_id,
                memo: i.memo,
                trade_time: i.trade_time,
                counterparty: i.counterparty,
                source: 'natural_language',
                confidence: i.confidence,
                has_invoice: i.has_invoice,
                is_confirmed: true,
              };
            });

            const result2 = await withFallback(
              function () {
                return api.nlConfirm(payload);
              },
              function () {
                // mock：本地写入内存台账
                return {
                  created: payload.map(function (p, idx) {
                    const cat = state.categories.filter(function (c) {
                      return c.id === p.category_id;
                    })[0] || {};
                    return Object.assign({}, p, {
                      id: 'txn-nl-' + Date.now() + '-' + idx,
                      category_name: cat.name || '未分类',
                      category_icon: cat.icon || '📦',
                    });
                  }),
                  count: payload.length,
                };
              }
            );

            const created = (result2.data && (result2.data.created || result2.data.items)) || [];
            created.forEach(function (c) {
              state.transactions.unshift(c);
            });

            FF.toast('已入账 ' + selected.length + ' 笔', 'success');
            FF.clear(box);
            state.nlResult = null;

            renderLedger();
            refreshQuickStats();

            window.dispatchEvent(
              new CustomEvent('firefly:transaction-created', { detail: { count: selected.length } })
            );
          } catch (err) {
            FF.toast('入账失败：' + (err.message || '未知错误'), 'error');
          } finally {
            confirmBtn.classList.remove('is-loading');
            confirmBtn.disabled = false;
          }
        },
      },
    });

    box.appendChild(
      el('div.btn-group', { style: { marginTop: '14px' } }, [
        confirmBtn,
        el('button.btn.btn-ghost', {
          text: '全部忽略',
          on: {
            click: function () {
              FF.clear(box);
              state.nlResult = null;
              FF.toast('已忽略本次解析结果', 'info');
            },
          },
        }),
      ])
    );
  }

  /* ============================================================
     手动记账表单
     ============================================================ */
  function renderFormCard() {
    /* 方向切换 */
    const directionSwitch = el('div.direction-switch');
    const directionButtons = {};

    ['expense', 'income'].forEach(function (dir) {
      const btn = el('button.direction-option', {
        dataset: { direction: dir },
        text: dir === 'expense' ? '💸 支出' : '💰 收入',
        on: {
          click: function () {
            state.direction = dir;
            state.category_id = '';
            updateDirectionUI();
            renderCategoryGrid();
          },
        },
      });
      directionButtons[dir] = btn;
      directionSwitch.appendChild(btn);
    });

    function updateDirectionUI() {
      Object.keys(directionButtons).forEach(function (key) {
        directionButtons[key].classList.toggle('is-active', key === state.direction);
      });
    }

    /* 金额 */
    const amountInput = el('input.input', {
      type: 'number',
      step: '0.01',
      min: '0',
      placeholder: '0.00',
      style: { fontSize: '21px', fontWeight: '700', textAlign: 'right', fontVariantNumeric: 'tabular-nums' },
      on: {
        input: function () {
          state.amount = amountInput.value;
        },
      },
    });

    const quickAmounts = el('div.quick-amounts');
    [10, 50, 100, 200, 500, 1000, 5000].forEach(function (value) {
      quickAmounts.appendChild(
        el('button.quick-amount', {
          text: FF.formatMoney(value, { digits: 0 }),
          on: {
            click: function () {
              const current = Number(state.amount) || 0;
              state.amount = String(current + value);
              amountInput.value = state.amount;
            },
          },
        })
      );
    });

    /* 类别网格 */
    const categoryGrid = el('div.category-grid');

    function renderCategoryGrid() {
      FF.clear(categoryGrid);
      const list = state.categories.filter(function (c) {
        return c.direction === state.direction;
      });

      if (!list.length) {
        categoryGrid.appendChild(el('div.field-hint', { text: '暂无可用类别，请先在设置中初始化' }));
        return;
      }

      // 首次渲染自动选中第一个
      if (!state.category_id || !list.some(function (c) { return c.id === state.category_id; })) {
        state.category_id = list[0].id;
      }

      list.forEach(function (c) {
        const chip = el('button.category-chip', {
          dataset: { id: c.id },
          class: c.id === state.category_id ? 'is-active' : '',
          on: {
            click: function () {
              state.category_id = c.id;
              FF.$$('.category-chip', categoryGrid).forEach(function (node) {
                node.classList.toggle('is-active', node.dataset.id === c.id);
              });
            },
          },
        }, [
          el('span.emoji', { text: c.icon || '📦' }),
          el('span', { text: c.name }),
        ]);
        categoryGrid.appendChild(chip);
      });
    }

    /* 关联账本（资金账户） */
    const accountSelect = el('select.select', {
      on: {
        change: function () {
          state.account_id = accountSelect.value;
        },
      },
    });

    /**
     * 把已加载的账户列表填进下拉框。
     *
     * 账户列表是异步拉的（见 renderFormCard 末尾），首次渲染时还是空的。
     * 此时把下拉框禁用并显示「暂无可用账本」，而不是塞一个占位账户：
     * 占位账户一旦被用户当成真账本选中，提交时后端拿不到有效 id，
     * 这笔钱就又不进余额了 —— 正是这次要修的 bug 的翻版。
     */
    function renderAccountOptions() {
      FF.clear(accountSelect);
      if (!state.accounts.length) {
        accountSelect.appendChild(
          el('option', { value: '', text: '暂无可用账本' })
        );
        accountSelect.disabled = true;
        return;
      }
      accountSelect.disabled = false;
      state.accounts.forEach(function (acc) {
        const label = acc.kind_label
          ? acc.name + '（' + acc.kind_label + '）'
          : acc.name;
        accountSelect.appendChild(
          el('option', { value: String(acc.id), text: label })
        );
      });
      // 尽量保持用户已选中的项；否则回落到第一个账户。
      // 账户 id 是整数，这里统一转字符串再比较，避免 1 !== '1' 的误判。
      const want = String(state.account_id || '');
      const hit = state.accounts.filter(function (a) {
        return String(a.id) === want;
      })[0];
      const chosen = hit || state.accounts[0];
      state.account_id = String(chosen.id);
      accountSelect.value = state.account_id;
    }

    /* 时间 */
    const timeInput = el('input.input', {
      type: 'datetime-local',
      value: state.trade_time,
      on: {
        change: function () {
          state.trade_time = timeInput.value;
        },
      },
    });

    /* 备注 */
    const memoInput = el('input.input', {
      placeholder: '补充说明，例如「与 XX 公司项目沟通」',
      on: {
        input: function () {
          state.memo = memoInput.value;
        },
      },
    });

    /* 发票 */
    const invoiceSwitch = el('div', {
      style: { display: 'flex', alignItems: 'center', gap: '10px', padding: '11px 14px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--bg-elevated)' },
    });
    const invoiceCheckbox = el('input', {
      type: 'checkbox',
      checked: true,
      style: { width: '17px', height: '17px', accentColor: 'var(--firefly)', cursor: 'pointer' },
      on: {
        change: function () {
          state.has_invoice = invoiceCheckbox.checked;
        },
      },
    });
    invoiceSwitch.appendChild(invoiceCheckbox);
    invoiceSwitch.appendChild(
      el('label', {
        style: { fontSize: '13.5px', cursor: 'pointer', flex: '1' },
        text: '已取得发票（影响税前扣除判定）',
      })
    );

    /* 提交 */
    const submitBtn = el('button.btn.btn-primary', {
      html: '✓ 记一笔',
      style: { flex: '1' },
      on: {
        click: async function () {
          const amount = Number(state.amount);
          if (!amount || amount <= 0) {
            FF.toast('请输入有效金额', 'warning');
            amountInput.focus();
            return;
          }
          if (!state.category_id) {
            FF.toast('请选择类别', 'warning');
            return;
          }
          // 后端把 account_id 当可选字段，但它决定了这笔钱进哪个账户的余额。
          // 这里必须拦：空着提交会入库一条「不挂账户」的流水，
          // 用户以为记上了，账户余额却不动，排查成本极高。
          if (!state.account_id) {
            FF.toast('请选择关联账本', 'warning');
            accountSelect.focus();
            return;
          }

          submitBtn.classList.add('is-loading');

          const payload = {
            direction: state.direction,
            amount: amount,
            category_id: state.category_id,
            memo: state.memo,
            trade_time: new Date(state.trade_time).toISOString(),
            account_id: state.account_id,
            has_invoice: state.has_invoice,
            source: 'manual',
            is_confirmed: true,
          };

          try {
            const result = await withFallback(
              function () {
                return api.transactions.create(payload);
              },
              function () {
                const cat = state.categories.filter(function (c) {
                  return c.id === payload.category_id;
                })[0] || {};
                const txn = Object.assign({}, payload, {
                  id: 'txn-' + Date.now(),
                  category_name: cat.name,
                  category_icon: cat.icon,
                  is_anomaly: false,
                });
                state.transactions.unshift(txn);
                return txn;
              }
            );

            // 后端 /transactions 返回的是
            //   { transaction, duplicate, analysis }
            // 这样的**组合**结构，而不是流水本身。
            //
            // 这里曾经直接 `const created = result.data` —— 拿到的是那个
            // 外层对象：unshift 进台账后每一列都是空白（没有 amount /
            // category_name），checkAnomalyFeedback 也因为拿不到 `.id`
            // 直接静默返回，异常提醒等于从未生效。
            // 必须取出内层的 transaction。
            const created = (result.data && result.data.transaction) || result.data;

            if (result.mock) {
              // 已在 fallback 中写入本地台账
            } else {
              state.transactions.unshift(created);
            }

            FF.toast('已记录 ' + FF.formatMoney(amount) + ' 的' + (state.direction === 'expense' ? '支出' : '收入'), 'success');

            // 重置部分字段
            state.amount = '';
            state.memo = '';
            amountInput.value = '';
            memoInput.value = '';

            renderLedger();
            refreshQuickStats();

            window.dispatchEvent(
              new CustomEvent('firefly:transaction-created', { detail: { count: 1 } })
            );

            // 检查异常（后台静默）
            checkAnomalyFeedback(created);
          } catch (err) {
            FF.toast('记账失败：' + (err.message || '未知错误'), 'error');
          } finally {
            submitBtn.classList.remove('is-loading');
          }
        },
      },
    });

    updateDirectionUI();

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📝' }), el('span', { text: '手动记账' })]),
        el('span.card-desc', { text: '收支 · 类别 · 备注' }),
      ]),
      el('div.field', {}, [el('label.field-label', { text: '类型' }), directionSwitch]),
      el('div.field', {}, [
        el('label.field-label', {}, [el('span', { text: '金额' }), el('span.required', { text: '*' })]),
        amountInput,
        quickAmounts,
      ]),
      el('div.field', {}, [
        el('label.field-label', {}, [el('span', { text: '类别' }), el('span.required', { text: '*' })]),
        categoryGrid,
      ]),
      el('div.field-row', {}, [
        el('div.field', {}, [el('label.field-label', { text: '发生时间' }), timeInput]),
        el('div.field', {}, [
          el('label.field-label', {}, [
            el('span', { text: '关联账本' }),
            el('span.required', { text: '*' }),
          ]),
          accountSelect,
        ]),
      ]),
      el('div.field', {}, [el('label.field-label', { text: '备注' }), memoInput]),
      el('div.field', {}, [el('label.field-label', { text: '凭证' }), invoiceSwitch]),
      el('div.btn-group', {}, [
        submitBtn,
        el('button.btn.btn-ghost', {
          text: '重置',
          on: {
            click: function () {
              state.amount = '';
              state.memo = '';
              state.trade_time = new Date().toISOString().slice(0, 16);
              amountInput.value = '';
              memoInput.value = '';
              timeInput.value = state.trade_time;
              state.has_invoice = true;
              invoiceCheckbox.checked = true;
              // 账本重置回第一个可用账户，而不是清空 ——
              // 清空会让用户点完重置再提交时被「请选择关联账本」拦下，
              // 而他从没动过这个下拉框，体验上莫名其妙。
              if (state.accounts.length) {
                state.account_id = String(state.accounts[0].id);
                accountSelect.value = state.account_id;
              }
              FF.toast('表单已重置', 'info');
            },
          },
        }),
      ]),
    ]);

    // 类别网格与账本下拉异步填充
    Promise.resolve().then(renderCategoryGrid);
    Promise.resolve().then(async function () {
      await refreshAccounts();
      renderAccountOptions();
    });

    return card;
  }

  /* ============================================================
     异常反馈（记账后静默检查）
     ============================================================ */
  async function checkAnomalyFeedback(txn) {
    if (!txn || !txn.id || String(txn.id).startsWith('txn-' + Date.now().toString().slice(0, 6))) return;

    try {
      const result = await withFallback(
        function () {
          return api.anomalies({ limit: 5 });
        },
        function () {
          return [];
        },
        { silent: true }
      );

      const list = window.FFViewUtils.normalizeList(result.data);
      const hit = list.filter(function (a) {
        return a.transaction_id === txn.id || a.id === txn.id;
      })[0];

      if (hit) {
        FF.toast('⚠️ 检测到异常：' + (hit.reason || '金额显著偏离历史'), 'warning', 5000);
      }
    } catch (err) {
      /* 静默失败 */
    }
  }

  /* ============================================================
     台账
     ============================================================ */
  function renderLedgerCard() {
    const listBox = el('div.ledger-list');

    const keywordInput = el('input.input', {
      placeholder: '搜索备注 / 对方…',
      style: { maxWidth: '220px' },
      on: {
        input: FF.debounce(function () {
          state.filter.keyword = keywordInput.value.trim().toLowerCase();
          renderLedger();
        }, 200),
      },
    });

    const directionSelect = el('select.select', { style: { maxWidth: '130px' } }, [
      el('option', { value: '', text: '全部类型' }),
      el('option', { value: 'expense', text: '仅支出' }),
      el('option', { value: 'income', text: '仅收入' }),
    ]);
    directionSelect.addEventListener('change', function () {
      state.filter.direction = directionSelect.value;
      renderLedger();
    });

    const categorySelect = el('select.select', { style: { maxWidth: '150px' } });
    categorySelect.addEventListener('change', function () {
      state.filter.category_id = categorySelect.value;
      renderLedger();
    });

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📒' }), el('span', { text: '交易台账' })]),
        el('span.tag.is-firefly', { text: '实时' }),
      ]),
      el('div', { style: { display: 'flex', gap: '9px', flexWrap: 'wrap', marginBottom: '16px' } }, [
        keywordInput,
        directionSelect,
        categorySelect,
      ]),
      el('div.table-wrap', {}, [listBox]),
    ]);

    card._listBox = listBox;
    card._categorySelect = categorySelect;

    return card;
  }

  function renderLedger() {
    const card = FF.$('.card', null);
    const listBox = document.querySelector('.ledger-list');
    if (!listBox) return;

    const list = state.transactions.filter(function (t) {
      if (state.filter.direction && t.direction !== state.filter.direction) return false;
      if (state.filter.category_id && t.category_id !== state.filter.category_id) return false;
      if (state.filter.keyword) {
        const haystack = ((t.memo || '') + ' ' + (t.counterparty || '') + ' ' + (t.category_name || '')).toLowerCase();
        if (haystack.indexOf(state.filter.keyword) < 0) return false;
      }
      return true;
    });

    FF.clear(listBox);

    // 填充类别筛选下拉
    const categorySelect = document.querySelectorAll('.card select.select');
    if (categorySelect.length >= 2) {
      const sel = categorySelect[categorySelect.length - 1];
      if (sel.options.length <= 1) {
        sel.appendChild(el('option', { value: '', text: '全部类别' }));
        const seen = {};
        state.transactions.forEach(function (t) {
          if (!t.category_name || seen[t.category_name]) return;
          seen[t.category_name] = true;
          sel.appendChild(el('option', { value: t.category_id, text: t.category_name }));
        });
      }
    }

    if (!list.length) {
      listBox.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '📭' }),
          el('div.empty-title', { text: '暂无记录' }),
          el('div.empty-desc', { text: '使用左侧表单或自然语言记下第一笔，台账会立即更新。' }),
        ])
      );
      return;
    }

    const table = el('table.data-table');
    table.appendChild(
      el('thead', {}, [
        el('tr', {}, [
          el('th', { text: '时间' }),
          el('th', { text: '类别' }),
          el('th', { text: '备注' }),
          el('th', { text: '状态' }),
          el('th', { text: '金额', style: { textAlign: 'right' } }),
          el('th', { text: '', style: { width: '52px' } }),
        ]),
      ])
    );

    const tbody = el('tbody');
    list.slice(0, 40).forEach(function (t) {
      const isExpense = t.direction === 'expense';

      const flags = [];
      if (t.is_anomaly) flags.push(el('span.tag.is-danger', { text: '异常' }));
      else if (t.is_personal_mixed) flags.push(el('span.tag.is-warning', { text: '疑似个人' }));
      if (t.has_invoice === false) flags.push(el('span.tag.is-warning', { text: '无票' }));
      if (t.source === 'natural_language') flags.push(el('span.tag.is-info', { text: 'AI' }));
      if (!flags.length) flags.push(el('span.tag.is-success', { text: '正常' }));

      const removeBtn = el('button.btn.btn-ghost.btn-icon', {
        html: window.FFViewUtils.icon('trash'),
        title: '删除',
        style: { color: 'var(--text-muted)' },
        on: {
          click: async function () {
            const ok = await FF.confirm({
              title: '删除这笔记录？',
              message: FF.formatDate(t.trade_time, 'YYYY-MM-DD HH:mm') + ' · ' + (t.memo || t.category_name) + ' · ' + FF.formatMoney(t.amount),
              confirmText: '删除',
              danger: true,
            });
            if (!ok) return;

            try {
              if (!String(t.id).startsWith('txn-') || !window.FFApi.isOffline()) {
                await api.transactions.remove(t.id).catch(function () {});
              }
              state.transactions = state.transactions.filter(function (item) {
                return item.id !== t.id;
              });
              FF.toast('已删除', 'success');
              renderLedger();
              refreshQuickStats();
            } catch (err) {
              FF.toast('删除失败：' + (err.message || '未知错误'), 'error');
            }
          },
        },
      });

      tbody.appendChild(
        el('tr', {}, [
          el('td.text-muted', {
            text: FF.formatDate(t.trade_time, 'MM-DD HH:mm'),
            style: { fontSize: '12.5px', whiteSpace: 'nowrap' },
          }),
          el('td', {}, [
            el('span', { text: (t.category_icon || '📦') + ' ' + (t.category_name || '未分类') }),
          ]),
          el('td', { text: t.memo || '—', style: { maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }),
          el('td', {}, flags),
          el('td.num.' + (isExpense ? 'text-expense' : 'text-income'), {
            text: (isExpense ? '-' : '+') + FF.formatMoney(t.amount, { symbol: '', digits: 2 }),
          }),
          el('td', {}, [removeBtn]),
        ])
      );
    });

    table.appendChild(tbody);
    listBox.appendChild(table);

    if (list.length > 40) {
      listBox.appendChild(
        el('div.field-hint', {
          text: '仅展示最近 40 条，共 ' + list.length + ' 条记录',
          style: { padding: '12px 16px', textAlign: 'center' },
        })
      );
    }
  }

  /* ============================================================
     快捷统计
     ============================================================ */
  function renderQuickStatsCard() {
    const box = el('div.quick-stats');
    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📊' }), el('span', { text: '本期速览' })]),
      ]),
      box,
    ]);
    card._statsBox = box;
    return card;
  }

  function refreshQuickStats() {
    const box = document.querySelector('.quick-stats');
    if (!box) return;

    const now = new Date();
    const range = FF.periodRange('monthly', now);
    const current = state.transactions.filter(function (t) {
      const time = new Date(t.trade_time).getTime();
      return time >= range.start.getTime() && time <= range.end.getTime();
    });

    const expense = FF.sum(
      current.filter(function (t) { return t.direction === 'expense'; }),
      function (t) { return t.amount; }
    );
    const income = FF.sum(
      current.filter(function (t) { return t.direction === 'income'; }),
      function (t) { return t.amount; }
    );

    FF.clear(box);

    const stats = [
      { label: '本期支出', value: expense, color: 'var(--danger)', sign: '-' },
      { label: '本期收入', value: income, color: 'var(--success)', sign: '+' },
      { label: '本期净额', value: income - expense, color: 'var(--firefly)', sign: '' },
    ];

    stats.forEach(function (s) {
      const valueNode = el('div', {
        text: '¥0.00',
        style: { fontSize: '19px', fontWeight: '700', color: s.color, fontVariantNumeric: 'tabular-nums' },
      });

      box.appendChild(
        el('div', {
          style: {
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            padding: '11px 0',
            borderBottom: '1px solid var(--divider)',
          },
        }, [
          el('span', { text: s.label, style: { fontSize: '13.5px', color: 'var(--text-secondary)' } }),
          valueNode,
        ])
      );

      FF.animateCount(valueNode, s.value, {
        duration: 900,
        digits: 2,
        prefix: s.sign + '¥',
      });
    });

    box.appendChild(
      el('div.field-hint', {
        text: '统计区间：' + FF.formatDate(range.start, 'MM/DD') + ' - ' + FF.formatDate(range.end, 'MM/DD') +
          ' · 共 ' + current.length + ' 笔',
        style: { marginTop: '13px', textAlign: 'center' },
      })
    );
  }

  /* ============================================================
     数据刷新
     ============================================================ */
  async function refreshCategories() {
    const utils = window.FFViewUtils;
    state.categories = utils.store.categories.length ? utils.store.categories : await utils.loadCategories();
    return state.categories;
  }

  /**
   * 加载资金账户列表，供「关联账本」下拉使用。
   *
   * 走 accountsList() 而不是复用 utils.store：账户余额会随记账实时变化，
   * 缓存住会导致刚记完一笔、余额还是旧值。这里每次进页面都重新拉。
   *
   * 失败时**不清空**已有列表（保留上次可用值），只提示一次；
   * 否则后端抖动会让下拉框突然变成「暂无可用账本」，用户无法记账。
   */
  async function refreshAccounts() {
    try {
      // 注意：withFallback 内部已经剥过 {success, data} 外层，
      // 这里的 res.data 就是业务数据本身，不要再 unwrap 第二次。
      const res = await withFallback(
        function () {
          // 必须走 window.FFApi.api.accountsList —— accountsList 是挂在
          // 内层 api 对象上的，而 window.FFApi 只导出了 { api, request,
          // withFallback, ... } 几个字段，没有 accountsList。
          // 写错成 window.FFApi.accountsList() 时是 undefined is not a
          // function，被下面的 catch 吞掉，表现就是「一直没有请求发出、
          // 下拉框永远显示暂无可用账本」。
          return api.accountsList();
        },
        function () {
          return [];
        }
      );
      const data = res && res.data;
      const list = Array.isArray(data)
        ? data
        : (data && (data.accounts || data.items)) || [];
      if (Array.isArray(list) && list.length) {
        state.accounts = list;
      }
    } catch (err) {
      if (!state.accounts.length) {
        FF.toast('账本列表加载失败，请刷新页面重试', 'error');
      }
    }
    return state.accounts;
  }

  async function refreshLedger() {
    const utils = window.FFViewUtils;
    state.transactions = await utils.loadTransactions(60);

    // 填充台账类别筛选
    const selects = document.querySelectorAll('.card select.select');
    if (selects.length >= 2) {
      const sel = selects[selects.length - 1];
      if (sel && sel.options.length <= 1) {
        sel.appendChild(el('option', { value: '', text: '全部类别' }));
        const seen = {};
        state.transactions.forEach(function (t) {
          if (!t.category_name || seen[t.category_name]) return;
          seen[t.category_name] = true;
          sel.appendChild(el('option', { value: t.category_id, text: t.category_name }));
        });
      }
    }

    renderLedger();
    refreshQuickStats();
  }
})();
