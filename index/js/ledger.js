/* ==========================================================================
   流萤 Firefly · 账本
   --------------------------------------------------------------------------
   「账本」在这里指**资金账户** —— 现金、银行卡、微信、支付宝这些装钱的容器。

   为什么不叫「账户管理」：用户脑子里的模型是「我有几个账本」，
   而不是「我有几条 Account 记录」。命名跟着用户的说法走，
   找功能时不用先翻译一遍。

   这一页只做账户本身的增删改查；账户里的流水通过「查看流水」弹层查看，
   不在这里做流水编辑 —— 避免出现两套编辑入口，导致行为不一致。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const el = FF.el;
  const api = window.FFApi.api;
  const withFallback = window.FFApi.withFallback;
  const Views = window.FFViews;

  /* 账户类型 → 图标 / 中文名。与后端 firefly/api/account.py 的 ACCOUNT_KINDS 一致。 */
  const KINDS = [
    { value: 'cash', label: '现金', icon: '💵' },
    { value: 'bank', label: '银行卡', icon: '🏦' },
    { value: 'wechat', label: '微信', icon: '💬' },
    { value: 'alipay', label: '支付宝', icon: '🅰️' },
    { value: 'credit', label: '信用卡', icon: '💳' },
    { value: 'other', label: '其他', icon: '📁' },
  ];

  function kindMeta(kind) {
    return (
      KINDS.filter(function (k) {
        return k.value === kind;
      })[0] || KINDS[KINDS.length - 1]
    );
  }

  /* ============================================================
     数据
     ============================================================ */

  /**
   * 拉取账户列表。
   *
   * ``withFallback`` 在正式模式下**不会**返回 mock，失败会直接抛错，
   * 由调用方渲染错误态。这里刻意不吞异常：账户余额是财务结论的一部分，
   * 拿假数字顶替比报错危险得多。
   */
  async function loadAccounts() {
    const result = await withFallback(
      function () {
        return api.accountsList();
      },
      function () {
        // 仅在演示模式（app.debug=true）下才会走到这里
        return [
          {
            id: 'acc-1',
            name: '现金钱包',
            kind: 'cash',
            balance: 3200.5,
            initial_balance: 3000,
            include_in_net: true,
            transaction_count: 12,
          },
          {
            id: 'acc-2',
            name: '招行工资卡',
            kind: 'bank',
            balance: 48210.0,
            initial_balance: 45000,
            include_in_net: true,
            transaction_count: 34,
          },
          {
            id: 'acc-3',
            name: '信用卡（待还）',
            kind: 'credit',
            balance: -2600.0,
            initial_balance: 0,
            include_in_net: false,
            transaction_count: 6,
          },
        ];
      }
    );

    const list = Array.isArray(result.data) ? result.data : [];
    return { list: list, mock: !!result.mock };
  }

  /* ============================================================
     汇总指标
     ============================================================ */

  function renderSummary(box, accounts) {
    const sum = function (arr) {
      return arr.reduce(function (s, a) {
        return s + (Number(a.balance) || 0);
      }, 0);
    };
    const total = sum(accounts);
    const netTotal = sum(
      accounts.filter(function (a) {
        return a.include_in_net;
      })
    );
    const excluded = total - netTotal;
    const txnTotal = accounts.reduce(function (s, a) {
      return s + (Number(a.transaction_count) || 0);
    }, 0);

    const metrics = [
      { label: '账本数', icon: '📚', value: accounts.length, suffix: ' 个' },
      { label: '余额合计', icon: '💰', value: total, money: true },
      { label: '净资产', icon: '📈', value: netTotal, money: true },
      { label: '关联流水', icon: '🧾', value: txnTotal, suffix: ' 笔' },
    ];

    const grid = el('div.metric-grid', { style: { marginBottom: '18px' } });

    metrics.forEach(function (m, i) {
      const valueNode = el('div.metric-value');

      const card = el('div.metric-card', {}, [
        el('div.metric-label', {}, [
          el('span', { text: m.icon }),
          el('span', { text: m.label }),
        ]),
        valueNode,
      ]);

      if (m.money) {
        valueNode.dataset.money = '1';
        valueNode.dataset.target = String(Math.round(m.value * 100) / 100);
      } else {
        valueNode.dataset.suffix = m.suffix || '';
        valueNode.dataset.target = String(m.value);
      }

      grid.appendChild(card);
    });

    box.appendChild(grid);

    // 数字滚动动画，与总览页观感保持一致。
    // 注意：animateCount 只认 prefix / suffix / digits / compact，
    // **没有** format 回调（传了会被静默忽略，数字退化成默认的两位小数——
    // 「账本数 3 个」会显示成「3.00」）。所以这里用 digits: 0 去掉小数。
    window.requestAnimationFrame(function () {
      FF.$$('.metric-value[data-target]', grid).forEach(function (node, i) {
        const target = Number(node.dataset.target) || 0;
        const isMoney = node.dataset.money === '1';
        window.setTimeout(function () {
          if (isMoney) {
            FF.animateCount(node, target, {
              duration: 1000,
              prefix: '¥',
              digits: 2,
              compact: true,
            });
          } else {
            FF.animateCount(node, target, {
              duration: 900,
              // prefix 必须显式清空：不传时 animateCount 按 compact 推断，
              // 金额场景会带上 '¥'，计数场景显示成货币符号是错的。
              prefix: '',
              digits: 0,
              suffix: node.dataset.suffix || '',
            });
          }
        }, 90 + i * 80);
      });
    });

    // 不计入净资产的金额单独说明，避免用户以为「总额算错了」
    if (Math.abs(excluded) > 0.005) {
      box.appendChild(
        el('div.card', { style: { marginBottom: '18px', padding: '14px 18px' } }, [
          el('div.field-hint', {
            html:
              'ℹ️ 有 <b>' +
              FF.escapeHtml(FF.formatMoney(excluded)) +
              '</b> 分布在「不计入净资产」的账本里，因此净资产与余额合计不一致。',
          }),
        ])
      );
    }
  }

  /* ============================================================
     表单（新建 / 编辑共用）
     ============================================================ */

  function renderForm(box, editing, onDone, onCancel) {
    const isEdit = !!editing;

    const nameInput = el('input.input', {
      type: 'text',
      placeholder: '例如：招商银行工资卡',
      value: isEdit ? editing.name : '',
      maxlength: '64',
    });

    const kindSelect = el('select.select');
    KINDS.forEach(function (k) {
      kindSelect.appendChild(
        el('option', {
          value: k.value,
          text: k.icon + ' ' + k.label,
          selected: isEdit ? String(editing.kind) === k.value : k.value === 'cash',
        })
      );
    });

    const balanceInput = el('input.input', {
      type: 'number',
      step: '0.01',
      placeholder: '0.00',
      value: isEdit ? String(editing.initial_balance || 0) : '',
    });

    const netCheckbox = el('input', {
      type: 'checkbox',
      checked: isEdit ? !!editing.include_in_net : true,
      style: {
        width: '17px',
        height: '17px',
        accentColor: 'var(--firefly)',
        cursor: 'pointer',
      },
    });

    const errorBox = el('div.field-hint', {
      hidden: true,
      style: { color: 'var(--danger)', marginTop: '10px' },
    });

    const submitBtn = el('button.btn.btn-primary', {
      text: isEdit ? '保存修改' : '添加账本',
      on: {
        click: async function () {
          const name = (nameInput.value || '').trim();
          if (!name) {
            errorBox.textContent = '请填写账本名称';
            errorBox.hidden = false;
            nameInput.focus();
            return;
          }
          errorBox.hidden = true;

          const payload = {
            name: name,
            // select.value 永远是字符串，这里原样传；后端会做合法值校验，
            // 未知类型落到 other，不必在前端维护一份同步的枚举。
            kind: kindSelect.value,
            initial_balance: Number(balanceInput.value) || 0,
            include_in_net: netCheckbox.checked,
          };

          submitBtn.classList.add('is-loading');
          try {
            if (isEdit) {
              await api.accountUpdate(editing.id, payload);
              FF.toast('账本已更新', 'success');
            } else {
              await api.accountCreate(payload);
              FF.toast('账本已添加', 'success');
            }
            onDone();
          } catch (err) {
            errorBox.textContent = err.message || '保存失败';
            errorBox.hidden = false;
            FF.toast('保存失败：' + (err.message || '未知错误'), 'error');
          } finally {
            submitBtn.classList.remove('is-loading');
          }
        },
      },
    });

    const actions = [submitBtn];
    if (isEdit) {
      actions.push(
        el('button.btn.btn-ghost', {
          text: '取消',
          on: { click: onCancel },
        })
      );
    }

    box.appendChild(
      el('div.card', { style: { marginBottom: '18px' } }, [
        el('div.card-header', {}, [
          el('div.card-title', { text: isEdit ? '编辑账本' : '添加账本' }),
        ]),
        el('div.field-row', {}, [
          el('div.field', {}, [
            el('label.field-label', {}, [
              el('span', { text: '账本名称' }),
              el('span.required', { text: '*' }),
            ]),
            nameInput,
          ]),
          el('div.field', {}, [
            el('label.field-label', { text: '类型' }),
            kindSelect,
          ]),
          el('div.field', {}, [
            el('label.field-label', { text: '初始余额（元）' }),
            balanceInput,
          ]),
        ]),
        el('label.field', {
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: '9px',
            marginTop: '12px',
            cursor: 'pointer',
          },
        }, [
          netCheckbox,
          el('span', { text: '计入净资产统计' }),
          el('span.field-hint', {
            text: '（关掉后该账本余额不算进「净资产」，适合信用卡等负债账本）',
          }),
        ]),
        errorBox,
        el('div.btn-group', { style: { marginTop: '16px' } }, actions),
      ])
    );
  }

  /* ============================================================
     列表
     ============================================================ */

  function renderList(box, accounts, ctx) {
    const card = el('div.card');

    card.appendChild(
      el('div.card-header', {}, [
        el('div.card-title', { text: '我的账本' }),
        el('div.card-desc', { text: '共 ' + accounts.length + ' 个' }),
      ])
    );

    if (!accounts.length) {
      card.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '📚' }),
          el('div.empty-title', { text: '还没有账本' }),
          el('div.empty-desc', {
            text: '先添加一个账本，记账时才有地方归属，报告里的账户余额也才有意义。',
          }),
        ])
      );
      box.appendChild(card);
      return;
    }

    accounts.forEach(function (acc, i) {
      const meta = kindMeta(acc.kind);

      const item = el('div.budget-item', { style: { animationDelay: i * 45 + 'ms' } });

      const left = el('div.budget-head', { style: { flex: '1', minWidth: '0' } }, [
        el('div.budget-title', {}, [
          el('span', { text: meta.icon + ' ' }),
          el('span', { text: acc.name }),
          el('span.tag', { text: meta.label, style: { marginLeft: '8px' } }),
          acc.include_in_net
            ? null
            : el('span.tag', {
                text: '不计入净资产',
                style: { marginLeft: '6px' },
              }),
        ].filter(Boolean)),
        el('div.field-hint', {
          text:
            '初始余额 ' +
            FF.formatMoney(acc.initial_balance || 0) +
            ' · 当前余额 ' +
            FF.formatMoney(acc.balance || 0) +
            ' · ' +
            (acc.transaction_count || 0) +
            ' 笔流水' +
            // 有流水时补上流入/流出明细：用户看到「初始 12000 · 当前 11800」
            // 却找不到钱去哪了，是这个页面最容易产生的疑问。
            (acc.transaction_count
              ? '（流入 ' +
                FF.formatMoney(acc.income || 0) +
                ' · 流出 ' +
                FF.formatMoney(acc.expense || 0) +
                '）'
              : ''),
        }),
      ]);

      const actions = el('div', {
        style: { display: 'flex', gap: '6px', alignItems: 'center', flex: '0 0 auto' },
      }, [
        el('button.btn-icon.btn-sm', {
          title: '查看该账本下的流水',
          text: '🧾',
          on: {
            click: function () {
              viewTransactions(acc);
            },
          },
        }),
        el('button.btn-icon.btn-sm', {
          title: '编辑',
          text: '✏️',
          on: {
            click: function () {
              ctx.edit(acc);
            },
          },
        }),
        el('button.btn-icon.btn-sm', {
          title: '删除',
          text: '🗑️',
          on: {
            click: async function () {
              const okToDelete = await FF.confirm({
                title: '删除账本',
                message:
                  '确定删除「' +
                  acc.name +
                  '」吗？' +
                  (acc.transaction_count
                    ? '该账本下有 ' +
                      acc.transaction_count +
                      ' 笔流水，需要先迁移或删除流水才能删除账本。'
                    : ''),
                confirmText: '删除',
                danger: true,
              });
              if (!okToDelete) return;

              try {
                const envelope = await api.accountRemove(acc.id);
                const payload = window.FFApi.unwrap(envelope);
                // 业务规则拒绝（最后一个账本 / 名下仍有流水）走 success:false，
                // 不是 HTTP 错误，所以这里要单独判一次。
                if (envelope && envelope.success === false) {
                  FF.toast(envelope.message || '删除失败', 'error', 4200);
                  return;
                }
                FF.toast((payload && payload.message) || '账本已删除', 'success');
                ctx.reload();
              } catch (err) {
                FF.toast('删除失败：' + (err.message || '未知错误'), 'error');
              }
            },
          },
        }),
      ]);

      item.appendChild(left);
      item.appendChild(actions);
      card.appendChild(item);
    });

    box.appendChild(card);
  }

  /* ============================================================
     某账本下的流水（弹层）
     ============================================================ */

  async function viewTransactions(acc) {
    let rows = [];
    try {
      const envelope = await api.accountTransactions(acc.id, { limit: 50 });
      const data = window.FFApi.unwrap(envelope);
      rows = Array.isArray(data) ? data : [];
    } catch (err) {
      FF.toast('流水加载失败：' + (err.message || '未知错误'), 'error');
      return;
    }

    const overlay = el('div', {
      style: {
        position: 'fixed',
        inset: '0',
        zIndex: '2200',
        display: 'grid',
        placeItems: 'center',
        padding: '24px',
        background: 'rgba(8, 12, 22, 0.62)',
        backdropFilter: 'blur(6px)',
      },
      on: {
        click: function (e) {
          if (e.target === overlay) overlay.remove();
        },
      },
    });

    const body = el('div', { style: { maxHeight: '54vh', overflowY: 'auto' } });

    if (!rows.length) {
      body.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '🧾' }),
          el('div.empty-desc', { text: '这个账本下还没有流水。' }),
        ])
      );
    } else {
      const table = el('table.data-table');
      table.appendChild(
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: '时间' }),
            el('th', { text: '方向' }),
            el('th', { text: '金额' }),
            el('th', { text: '备注' }),
          ]),
        ])
      );
      const tbody = el('tbody');
      rows.forEach(function (t) {
        const isIncome = t.direction === 'income';
        tbody.appendChild(
          el('tr', {}, [
            el('td', {
              text: String(t.trade_time || '').replace('T', ' ').slice(0, 16),
            }),
            el('td', {}, [el('span.tag', { text: isIncome ? '收入' : '支出' })]),
            el('td', {
              text: (isIncome ? '+' : '−') + FF.formatMoney(t.amount || 0),
              class: isIncome ? 'text-income' : 'text-expense',
            }),
            el('td', { text: t.memo || '—' }),
          ])
        );
      });
      table.appendChild(tbody);
      body.appendChild(el('div.table-wrap', {}, [table]));
    }

    const dialog = el('div.card', {
      style: { width: 'min(680px, 100%)', maxHeight: '80vh', overflow: 'hidden' },
    }, [
      el('div.card-header', {}, [
        el('div.card-title', {
          text: kindMeta(acc.kind).icon + ' ' + acc.name + ' · 流水',
        }),
        el('button.btn-icon', {
          text: '✕',
          title: '关闭',
          on: {
            click: function () {
              overlay.remove();
            },
          },
        }),
      ]),
      body,
    ]);

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
  }

  /* ============================================================
     主入口
     ============================================================ */

  Views.ledger = {
    async render(container) {
      FF.clear(container);

      container.appendChild(
        el('div.page-head', {}, [
          el('h2.page-title', { text: '📚 账本' }),
          el('p.page-subtitle', {
            text: '管理资金账户 —— 现金、银行卡、微信、支付宝。记账时选择归属账本，报告里的余额与净资产都来源于此。',
          }),
        ])
      );

      const body = el('div');
      container.appendChild(body);

      let editing = null;

      async function draw() {
        FF.clear(body);
        body.appendChild(
          el('div.skeleton', { style: { height: '180px', borderRadius: '18px' } })
        );

        let payload;
        try {
          payload = await loadAccounts();
        } catch (err) {
          FF.clear(body);
          body.appendChild(
            el('div.empty', {}, [
              el('div.empty-icon', { text: '⚠️' }),
              el('div.empty-title', { text: '账本加载失败' }),
              el('div.empty-desc', { text: err.message || '服务内部错误' }),
              el('button.btn.btn-primary', {
                text: '重新加载',
                on: { click: draw },
              }),
            ])
          );
          return;
        }

        FF.clear(body);

        if (payload.mock) {
          body.appendChild(
            el('div.card', { style: { marginBottom: '16px', padding: '14px 18px' } }, [
              el('div.field-hint', {
                text: '⚠️ 当前为演示模式，以下为示例数据。',
              }),
            ])
          );
        }

        renderSummary(body, payload.list);

        const ctx = {
          reload: function () {
            editing = null;
            draw();
          },
          edit: function (acc) {
            editing = acc;
            draw();
          },
        };

        renderForm(body, editing, ctx.reload, function () {
          editing = null;
          draw();
        });
        renderList(body, payload.list, ctx);
      }

      await draw();

      return function destroy() {
        /* 本视图没有定时器或观察器需要清理 */
      };
    },
  };
})();
