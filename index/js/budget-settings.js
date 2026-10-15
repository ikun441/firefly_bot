/* ==========================================================================
   流萤 Firefly · 预算与设置视图
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
  // unwrap 是 api.js 的模块私有函数，对外只通过 window.FFApi.unwrap 暴露。
  // 这里本地起个别名，避免每处都写 window.FFApi.unwrap。
  const unwrap = window.FFApi.unwrap;

  /* ============================================================
     预算视图
     ============================================================ */
  Views.budget = {
    async render(container) {
      container.appendChild(
        el('div.page-head', {}, [
          el('h1.page-title', {}, [el('span', { text: '🎯' }), el('span', { text: '预算管理' })]),
          el('p.page-subtitle', {
            text: '为常用类别设定周期额度。达到 80% 时流萤会通过 Bot 主动预警，超支后计入报告。',
          }),
        ])
      );

      const listBox = el('div');
      const formBox = el('div');

      container.appendChild(
        el('div.grid.grid-sidebar', {}, [
          el('div.card', {}, [
            el('div.card-header', {}, [
              el('div.card-title', {}, [el('span.icon', { text: '📋' }), el('span', { text: '当前预算' })]),
            ]),
            listBox,
          ]),
          el('div.card', {}, [
            el('div.card-header', {}, [
              el('div.card-title', {}, [el('span.icon', { text: '➕' }), el('span', { text: '新增 / 调整' })]),
            ]),
            formBox,
          ]),
        ])
      );

      const result = await withFallback(
        function () {
          return api.budgets.list();
        },
        function () {
          return Mock.BUDGETS;
        }
      );

      const budgets = utils
        .normalizeList(result.data)
        .map(normalizeBudget)
        .filter(function (b) {
          // 列表接口会把「未设预算的类别」也混进来（budget_id 为 null、
          // state 为 unbudgeted），这些不是用户创建的预算，不渲染。
          return b.id !== undefined && b.id !== null;
        });
      utils.store.budgets = budgets;

      if (!store_categories_ready()) await utils.loadCategories();

      renderList(listBox, budgets, result.mock);
      renderForm(formBox, budgets);
    },
  };

  function store_categories_ready() {
    return utils.store.categories && utils.store.categories.length > 0;
  }

  /**
   * 把后端预算对象归一化成 renderList 期望的字段名。
   *
   * 后端 GET /api/budgets 返回的是聚合视图：
   *   budget_id / budget / actual / usage_ratio / name / icon / state
   * 而创建接口 POST /api/budgets 返回的是原始记录：
   *   id / amount / category_id / ...
   * 两套形状都兼容，统一映射成 amount / used / ratio / id / category_name。
   */
  function normalizeBudget(b) {
    var amount = b.budget !== undefined ? b.budget : b.amount;
    var used = b.actual !== undefined ? b.actual : b.used || 0;
    var ratio = b.usage_ratio !== undefined && b.usage_ratio !== null ? b.usage_ratio : b.ratio;
    if (ratio === undefined || ratio === null) {
      ratio = amount > 0 ? (Number(used) / Number(amount)) * 100 : 0;
    }
    return {
      id: b.budget_id !== undefined ? b.budget_id : b.id,
      amount: Number(amount) || 0,
      used: Number(used) || 0,
      ratio: Number(ratio) || 0,
      category_name: b.category_name || b.name || '总预算',
      category_icon: b.category_icon || b.icon || '📦',
      state: b.state || null,
      period: b.period,
      raw: b,
    };
  }

  function renderList(box, budgets, isMock) {
    FF.clear(box);

    if (!budgets.length) {
      box.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '🎯' }),
          el('div.empty-title', { text: '还没有设置预算' }),
          el('div.empty-desc', { text: '在右侧为餐饮、交通等类别设定额度，报告会自动计算执行率。' }),
        ])
      );
      return;
    }

    const grid = el('div');
    budgets.forEach(function (b) {
      const ratio = Number(b.ratio) || 0;
      const used = Number(b.used) || 0;
      const limit = Number(b.amount) || 0;
      // 优先用后端算好的 state（over/warn/ok），没有才本地推算
      let status = b.state;
      if (status !== 'over' && status !== 'warn' && status !== 'ok') {
        status = used > limit ? 'over' : used > limit * 0.8 ? 'warn' : 'ok';
      }
      const statusClass = status === 'over' ? 'is-over' : status === 'warn' ? 'is-warn' : 'is-ok';
      const barClass = status === 'over' ? 'is-over' : status === 'warn' ? 'is-warn' : '';
      const tagClass = status === 'over' ? 'is-danger' : status === 'warn' ? 'is-warning' : 'is-success';
      const tagText = status === 'over' ? '已超支' : status === 'warn' ? '接近上限' : '正常';

      grid.appendChild(
        el('div.budget-item.' + statusClass, {}, [
          el('div.budget-head', {}, [
            el('div.budget-title', {}, [
              el('span', { text: b.category_icon || b.icon || '📦' }),
              el('span', { text: b.category_name || '总预算' }),
            ]),
            el('div', { style: { display: 'flex', alignItems: 'center', gap: '9px' } }, [
              el('span.tag.' + tagClass, { text: tagText }),
              el('span.bar-amount', {
                html: '<strong>' + FF.formatMoney(used, { digits: 0 }) + '</strong> / ' + FF.formatMoney(limit, { digits: 0 }),
              }),
            ]),
          ]),
          el('div.progress', {}, [
            el('div.progress-bar.' + barClass, { style: { width: '0%' }, dataset: { target: Math.min(ratio, 100) } }),
          ]),
          el('div', {
            style: { display: 'flex', justifyContent: 'space-between', marginTop: '8px', alignItems: 'center' },
          }, [
            el('div.field-hint', { text: '执行率 ' + FF.formatPercent(ratio) }),
            el('button.btn.btn-ghost.btn-sm', {
              text: '删除',
              style: { color: 'var(--danger)' },
              on: {
                click: async function () {
                  const ok = await FF.confirm({
                    title: '删除该预算？',
                    message: '删除后该类别不再计入预算执行分析。',
                    confirmText: '删除',
                    danger: true,
                  });
                  if (!ok) return;

                  try {
                    await api.budgets.remove(b.id).catch(function () {});
                    const index = budgets.indexOf(b);
                    if (index >= 0) budgets.splice(index, 1);
                    FF.toast('已删除', 'success');
                    renderList(box, budgets, isMock);
                  } catch (err) {
                    FF.toast('删除失败：' + (err.message || '未知错误'), 'error');
                  }
                },
              },
            }),
          ]),
        ])
      );
    });

    box.appendChild(grid);

    window.requestAnimationFrame(function () {
      FF.$$('.progress-bar[data-target]', box).forEach(function (bar, i) {
        window.setTimeout(function () {
          bar.style.width = bar.dataset.target + '%';
        }, 160 + i * 70);
      });
    });
  }

  function renderForm(box, budgets) {
    FF.clear(box);

    const categories = utils.store.categories.filter(function (c) {
      return c.direction === 'expense';
    });

    const categorySelect = el('select.select');
    categories.forEach(function (c) {
      categorySelect.appendChild(el('option', { value: c.id, text: (c.icon || '') + ' ' + c.name }));
    });

    const amountInput = el('input.input', {
      type: 'number',
      min: '0',
      step: '100',
      placeholder: '例如 5000',
    });

    const periodSelect = el('select.select', {}, [
      el('option', { value: 'monthly', text: '月度' }),
      el('option', { value: 'weekly', text: '周度' }),
      el('option', { value: 'quarterly', text: '季度' }),
      el('option', { value: 'yearly', text: '年度' }),
    ]);

    const alertCheckbox = el('input', {
      type: 'checkbox',
      checked: true,
      style: { width: '17px', height: '17px', accentColor: 'var(--firefly)', cursor: 'pointer' },
    });

    const submitBtn = el('button.btn.btn-primary', {
      text: '保存预算',
      style: { width: '100%' },
      on: {
        click: async function () {
          const amount = Number(amountInput.value);
          if (!amount || amount <= 0) {
            FF.toast('请输入有效额度', 'warning');
            amountInput.focus();
            return;
          }

          // 必须用宽松比较或统一转字符串。
          // <option value="1"> 的 select.value 永远是**字符串** "1"，
          // 而后端返回的 c.id 是**数字** 1，用 === 会永远匹配不到，
          // 于是无论选什么类别都提示「请选择类别」。
          const selected = String(categorySelect.value);
          const category = categories.filter(function (c) {
            return String(c.id) === selected;
          })[0];
          if (!category) {
            FF.toast('请选择类别', 'warning');
            return;
          }

          const payload = {
            category_id: category.id,
            category_name: category.name,
            category_icon: category.icon,
            amount: amount,
            period: periodSelect.value,
            alert_enabled: alertCheckbox.checked,
            used: 0,
          };

          submitBtn.classList.add('is-loading');

          try {
            const result = await withFallback(
              function () {
                return api.budgets.create(payload);
              },
              function () {
                return Object.assign({}, payload, { id: 'b-' + Date.now() });
              }
            );

            // 创建接口返回的是原始记录（id/amount/category_id...），
            // 与列表接口的聚合形状（budget_id/budget/actual/usage_ratio...）
            // 完全不同。直接 push 进去必然渲染错，改为整体重新拉取列表。
            const listBox = document.querySelector('.card .budget-item')
              ? document.querySelector('.card .budget-item').parentNode
              : null;

            if (result.mock) {
              budgets.push(normalizeBudget(result.data));
              FF.toast('预算已保存', 'success');
              amountInput.value = '';
              if (listBox) renderList(listBox, budgets, true);
            } else {
              try {
                const fresh = await api.budgets.list();
                const freshList = utils
                  .normalizeList(fresh.data)
                  .map(normalizeBudget)
                  .filter(function (b) {
                    return b.id !== undefined && b.id !== null;
                  });
                budgets.length = 0;
                freshList.forEach(function (b) {
                  budgets.push(b);
                });
                utils.store.budgets = budgets;
                FF.toast('预算已保存', 'success');
                amountInput.value = '';
                if (listBox) renderList(listBox, budgets, false);
              } catch (refreshErr) {
                // 列表刷新失败不影响保存成功的事实，只是看不到最新执行率
                FF.toast('预算已保存（列表刷新失败，请刷新页面）', 'warning');
              }
            }
          } catch (err) {
            FF.toast('保存失败：' + (err.message || '未知错误'), 'error');
          } finally {
            submitBtn.classList.remove('is-loading');
          }
        },
      },
    });

    box.appendChild(
      el('div.field', {}, [
        el('label.field-label', {}, [el('span', { text: '类别' }), el('span.required', { text: '*' })]),
        categorySelect,
      ])
    );
    box.appendChild(
      el('div.field', {}, [
        el('label.field-label', {}, [el('span', { text: '额度（元）' }), el('span.required', { text: '*' })]),
        amountInput,
      ])
    );
    box.appendChild(
      el('div.field', {}, [el('label.field-label', { text: '周期' }), periodSelect])
    );
    box.appendChild(
      el('div.field', {}, [
        el('label.field-label', { text: '预警' }),
        el('div', {
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '11px 14px',
            borderRadius: '8px',
            border: '1px solid var(--border)',
            background: 'var(--bg-elevated)',
          },
        }, [
          alertCheckbox,
          el('span', { text: '达到 80% 时主动提醒', style: { fontSize: '13.5px' } }),
        ]),
      ])
    );
    box.appendChild(submitBtn);
  }

  /* ============================================================
     设置视图
     ============================================================ */
  Views.settings = {
    async render(container) {
      container.appendChild(
        el('div.page-head', {}, [
          el('h1.page-title', {}, [el('span', { text: '⚙️' }), el('span', { text: '系统设置' })]),
          el('p.page-subtitle', {
            text: '这里的修改会写回 config.yaml 立即生效。API Key 等敏感项仅在 .env 中维护，不在前端展示。',
          }),
        ])
      );

      const body = el('div.stack');
      container.appendChild(body);
      body.appendChild(el('div.skeleton', { style: { height: '300px', borderRadius: '20px' } }));

      const result = await withFallback(
        function () {
          return api.getSettings();
        },
        function () {
          return buildMockSettings();
        }
      );

      const settings = result.data || {};
      FF.clear(body);

      if (result.mock) {
        body.appendChild(
          el('div.card', { style: { borderLeft: '3px solid var(--warning)' } }, [
            el('div', {
              text: '⚠️ 后端未连接，以下为本地默认配置的只读预览。启动后端后即可在此修改并写回 config.yaml。',
              style: { fontSize: '13.5px', color: 'var(--warning)' },
            }),
          ])
        );
      }

      const groups = [
        { key: 'app', label: '应用信息', icon: '🏷️' },
        { key: 'server', label: '服务与端口', icon: '🌐' },
        { key: 'ledger', label: '记账规则', icon: '📚' },
        { key: 'llm', label: '大模型配置', icon: '🧠' },
        { key: 'anomaly', label: '异常检测', icon: '🚨' },
        { key: 'tax_check', label: '税务自检', icon: '🧾' },
        { key: 'budget', label: '预算与预警', icon: '🎯' },
        { key: 'alert', label: '阈值提醒', icon: '🔔' },
        { key: 'push', label: '报告推送', icon: '📤' },
        { key: 'ui', label: '界面外观', icon: '🎨' },
        { key: 'auth', label: '登录与安全', icon: '🔐' },
      ];

      const patch = {};
      const inputs = {};

      groups.forEach(function (group) {
        const values = settings[group.key];
        if (!values || typeof values !== 'object') return;

        const fields = el('div');
        let hasField = false;

        Object.keys(values).forEach(function (field) {
          const value = values[field];
          if (value === null || value === undefined) return;
          if (typeof value === 'object' && !Array.isArray(value)) return;
          hasField = true;

          const path = group.key + '.' + field;
          const fieldEl = buildSettingField(path, field, value, inputs, patch);
          if (fieldEl) fields.appendChild(fieldEl);
        });

        if (!hasField) return;

        body.appendChild(
          el('div.card.reveal', {}, [
            el('div.card-header', {}, [
              el('div.card-title', {}, [
                el('span.icon', { text: group.icon }),
                el('span', { text: group.label }),
              ]),
            ]),
            fields,
          ])
        );
      });

      /* ---- 登录与安全：修改口令卡片 ---- */
      // 单独做成一张卡片而不是塞进上面的通用字段列表，
      // 因为改口令必须走 /api/auth/change-password（校验原口令 + 强度），
      // 不能通过 PUT /api/settings 直接写入 password 字段。
      body.appendChild(buildPasswordCard());

      /* ---- 保存条 ---- */
      const saveBtn = el('button.btn.btn-primary', {
        html: '💾 保存设置',
        disabled: result.mock,
        on: {
          click: async function () {
            const payload = {};
            Object.keys(patch).forEach(function (key) {
              payload[key] = patch[key];
            });

            if (!Object.keys(payload).length) {
              FF.toast('没有需要保存的修改', 'info');
              return;
            }

            saveBtn.classList.add('is-loading');
            try {
              const saved = await api.updateSettings(payload);
              FF.toast(
                '已保存 ' + (saved.updated ? saved.updated.length : Object.keys(payload).length) + ' 项配置' +
                  (saved.rejected && saved.rejected.length ? '，' + saved.rejected.length + ' 项被拒绝' : ''),
                'success'
              );
            } catch (err) {
              FF.toast('保存失败：' + (err.message || '未知错误'), 'error');
            } finally {
              saveBtn.classList.remove('is-loading');
            }
          },
        },
      });

      const resetBtn = el('button.btn.btn-ghost', {
        text: '放弃修改',
        on: {
          click: function () {
            Object.keys(patch).forEach(function (key) {
              delete patch[key];
            });
            FF.toast('已放弃本次修改', 'info');
            Views.settings.render(container);
          },
        },
      });

      const actions = el('div.card.no-print', {
        style: {
          position: 'sticky',
          bottom: '18px',
          display: 'flex',
          gap: '12px',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          zIndex: '50',
        },
      }, [
        el('div.field-hint', { text: '修改将写入 config.yaml；带 * 的敏感项请编辑 .env 文件。' }),
        el('div.btn-group', {}, [resetBtn, saveBtn]),
      ]);

      body.appendChild(actions);
      utils.observeReveal(body);

      /* ---- 协议端连接状态（与推送配置放在一起，便于排查收不到推送的原因） ---- */
      if (window.FFBotStatus) {
        body.appendChild(window.FFBotStatus.buildEmbeddedCard().node);
      }

      /* ---- 数据备份与还原 ---- */
      body.appendChild(buildBackupCard());

      /* ---- 只读信息卡 ---- */
      body.appendChild(buildReadonlyCard(settings));
    },
  };

  /**
   * 数据备份与还原卡片。
   *
   * 交互上的两个刻意设计：
   * 1. **还原前必须先「查看内容」**。还原是覆盖式操作，让用户在点还原之前
   *    先看到「这份备份是什么时候的、里面有多少条流水」，比事后解释划算。
   * 2. **还原要输入确认词**，不是随手一个二次弹窗。后端也强制校验
   *    ``confirm: "RESTORE"``，两端都拦一道——误点一次就抹掉几个月账目
   *    的代价太高。
   */
  function buildBackupCard() {
    const listBox = el('div', { id: 'backup-list' });
    const uploadInput = el('input', {
      type: 'file',
      accept: '.json,application/json',
      id: 'backup-file',
      style: { display: 'none' },
    });

    const statusBox = el('div', { style: { marginTop: '12px' } });

    /* ---- 生成备份 ---- */
    const createBtn = el('button.btn.btn-primary', {
      text: '📦 立即备份',
      on: {
        click: async function () {
          createBtn.disabled = true;
          createBtn.textContent = '正在导出…';
          try {
            const envelope = await api.backupCreate();
            const data = unwrap(envelope);
            if (!envelope || envelope.success === false) {
              statusBox.innerHTML = '';
              statusBox.appendChild(errorLine((envelope && envelope.message) || '备份失败'));
            } else {
              statusBox.innerHTML = '';
              statusBox.appendChild(
                successLine(
                  '备份已生成：' +
                    data.name +
                    '（' +
                    data.size_label +
                    '）'
                )
              );
              await loadBackupList(listBox, statusBox);
            }
          } catch (err) {
            statusBox.innerHTML = '';
            statusBox.appendChild(errorLine('备份失败：' + (err.message || err)));
          } finally {
            createBtn.disabled = false;
            createBtn.textContent = '📦 立即备份';
          }
        },
      },
    });

    /* ---- 上传还原 ---- */
    const uploadBtn = el('button.btn.btn-ghost', {
      text: '⬆️ 上传备份并还原',
      on: {
        click: function () {
          uploadInput.click();
        },
      },
    });

    uploadInput.addEventListener('change', async function () {
      const file = uploadInput.files && uploadInput.files[0];
      if (!file) return;

      // 第一步：只解析不写库，让用户确认这确实是想要的那份备份
      const form = new FormData();
      form.append('file', file);

      statusBox.innerHTML = '';
      statusBox.appendChild(hintLine('正在校验 ' + file.name + ' …'));

      let summary = null;
      try {
        const envelope = await api.backupInspect(form);
        if (!envelope || envelope.success === false) {
          statusBox.innerHTML = '';
          statusBox.appendChild(errorLine((envelope && envelope.message) || '备份文件校验失败'));
          uploadInput.value = '';
          return;
        }
        summary = unwrap(envelope);
      } catch (err) {
        statusBox.innerHTML = '';
        statusBox.appendChild(errorLine('备份文件校验失败：' + (err.message || err)));
        uploadInput.value = '';
        return;
      }

      // 第二步：展示摘要 + 要求输入确认词
      const counts = summary.counts || {};
      const rows = Object.keys(counts)
        .filter(function (k) {
          return counts[k] > 0;
        })
        .map(function (k) {
          return TABLE_LABELS[k] || k;
        });

      const confirmInput = el('input.input', {
        type: 'text',
        placeholder: '输入 RESTORE 以确认',
        style: { maxWidth: '220px' },
      });

      const doRestoreBtn = el('button.btn.btn-danger', {
        text: '确认还原',
        disabled: true,
        on: {
          click: async function () {
            doRestoreBtn.disabled = true;
            doRestoreBtn.textContent = '正在还原…';
            try {
              const form2 = new FormData();
              form2.append('file', file);
              const env2 = await api.backupRestoreUpload(form2);
              if (!env2 || env2.success === false) {
                statusBox.innerHTML = '';
                statusBox.appendChild(errorLine((env2 && env2.message) || '还原失败'));
              } else {
                const d = unwrap(env2);
                statusBox.innerHTML = '';
                statusBox.appendChild(
                  successLine(
                    '已还原 ' +
                      d.total_rows +
                      ' 行数据。' +
                      (d.safety_backup_name
                        ? ' 还原前的数据已自动备份为 ' + d.safety_backup_name + '，可随时退回。'
                        : '')
                  )
                );
                await loadBackupList(listBox, statusBox);
              }
            } catch (err) {
              statusBox.innerHTML = '';
              statusBox.appendChild(errorLine('还原失败：' + (err.message || err)));
            } finally {
              doRestoreBtn.disabled = false;
              doRestoreBtn.textContent = '确认还原';
            }
          },
        },
      });

      confirmInput.addEventListener('input', function () {
        doRestoreBtn.disabled = confirmInput.value.trim() !== 'RESTORE';
      });

      statusBox.innerHTML = '';
      statusBox.appendChild(
        el('div', {
          style: {
            border: '1px solid var(--border, rgba(255,255,255,.12))',
            borderRadius: '12px',
            padding: '14px',
            marginTop: '4px',
          },
        }, [
          el('div', { style: { fontWeight: '600', marginBottom: '6px' } }, [
            el('span', { text: '📄 ' + file.name }),
          ]),
          el('div.field-hint', {
            text:
              '备份时间：' +
              (summary.created_at ? FF.formatDate(summary.created_at, 'YYYY-MM-DD HH:mm') : '未知') +
              ' · 共 ' + summary.total_rows + ' 行',
          }),
          el('div.field-hint', {
            text: rows.length ? '包含：' + rows.join('、') : '备份内没有数据',
            style: { marginBottom: '10px' },
          }),
          el('div.field-hint', {
            text:
              '⚠️ 还原会用这份备份**覆盖当前全部数据**。' +
              '系统会先自动备份当前状态，但请务必确认这是你要的那一份。',
            style: { color: 'var(--danger, #f87171)', marginBottom: '10px' },
          }),
          el('div.field-row', { style: { display: 'flex', gap: '8px', alignItems: 'center' } }, [
            confirmInput,
            doRestoreBtn,
          ]),
        ])
      );
      uploadInput.value = '';
    });

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', { text: '💾 数据备份与还原' }),
        el('div.card-desc', {
          text:
            '把全部账本、流水、类别、报告导出成一份 JSON 文件；' +
            '换设备或想退回某个时点，上传该文件即可恢复。',
        }),
      ]),
      el('div.btn-group', { style: { marginBottom: '14px' } }, [createBtn, uploadBtn]),
      uploadInput,
      listBox,
      statusBox,
    ]);

    // 首次渲染即拉取列表
    loadBackupList(listBox, statusBox);
    return card;
  }

  /** 备份文件里各表的展示名（用户看不懂 transactions 这种英文 key） */
  var TABLE_LABELS = {
    users: '用户',
    accounts: '账本',
    categories: '类别',
    transactions: '流水',
    subscriptions: '周期订阅',
    budgets: '预算',
    reports: '报告',
    tax_checks: '税检结论',
    suggestions: 'AI 建议',
    audit_logs: '审计日志',
    policy_docs: '政策文档',
    import_batches: '导入批次',
    system_states: '系统状态',
  };

  /** 加载并渲染备份文件列表 */
  async function loadBackupList(listBox, statusBox) {
    listBox.innerHTML = '';
    let items = [];
    try {
      const envelope = await api.backupList();
      items = unwrap(envelope) || [];
      if (!Array.isArray(items)) items = [];
    } catch (err) {
      listBox.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '🗂' }),
          el('div.empty-title', { text: '备份列表读取失败' }),
          el('div.empty-desc', { text: err.message || String(err) }),
        ])
      );
      return;
    }

    if (!items.length) {
      listBox.appendChild(
        el('div.empty', {}, [
          el('div.empty-icon', { text: '📭' }),
          el('div.empty-title', { text: '还没有备份' }),
          el('div.empty-desc', { text: '点「立即备份」生成第一份。建议在每次录入大批流水后再备一次。' }),
        ])
      );
      return;
    }

    const rows = items.map(function (item) {
      const actions = el('div', { style: { display: 'flex', gap: '6px' } }, [
        el('a.btn.btn-sm.btn-ghost', {
          text: '⬇️ 下载',
          href: api.backupDownloadUrl(item.name),
          title: '下载到本地保存',
        }),
        el('button.btn.btn-sm.btn-ghost', {
          text: '♻️ 还原',
          title: '用这份备份覆盖当前数据',
          on: {
            click: async function () {
              const agreed = await FF.confirm({
                title: '确认还原',
                message:
                  '将用「' +
                  item.name +
                  '」覆盖当前全部数据。\n\n' +
                  '系统会先自动备份当前状态，但仍请确认。',
                confirmText: '确认还原',
                danger: true,
              });
              if (!agreed) return;

              statusBox.innerHTML = '';
              statusBox.appendChild(hintLine('正在还原…'));
              try {
                const envelope = await api.backupRestore(item.name);
                if (!envelope || envelope.success === false) {
                  statusBox.innerHTML = '';
                  statusBox.appendChild(errorLine((envelope && envelope.message) || '还原失败'));
                  return;
                }
                const d = unwrap(envelope);
                statusBox.innerHTML = '';
                statusBox.appendChild(
                  successLine(
                    '已还原 ' + d.total_rows + ' 行数据。' +
                      (d.safety_backup_name
                        ? ' 还原前的数据已备份为 ' + d.safety_backup_name + '。请刷新页面查看。'
                        : ' 请刷新页面查看。')
                  )
                );
                await loadBackupList(listBox, statusBox);
              } catch (err) {
                statusBox.innerHTML = '';
                statusBox.appendChild(errorLine('还原失败：' + (err.message || err)));
              }
            },
          },
        }),
        el('button.btn.btn-sm.btn-ghost', {
          text: '🗑️',
          title: '删除这份备份',
          on: {
            click: async function () {
              const agreed = await FF.confirm({
                title: '删除备份',
                message: '确定删除 ' + item.name + '？删除后无法恢复。',
                confirmText: '删除',
                danger: true,
              });
              if (!agreed) return;
              try {
                const envelope = await api.backupDelete(item.name);
                if (envelope && envelope.success === false) {
                  statusBox.innerHTML = '';
                  statusBox.appendChild(errorLine(envelope.message || '删除失败'));
                  return;
                }
                await loadBackupList(listBox, statusBox);
              } catch (err) {
                statusBox.innerHTML = '';
                statusBox.appendChild(errorLine('删除失败：' + (err.message || err)));
              }
            },
          },
        }),
      ]);

      return el('div.budget-item', {}, [
        el('div.budget-head', { style: { flex: '1', minWidth: '0' } }, [
          el('div.budget-title', {}, [
            el('span', { text: item.is_auto ? '🛡 ' : '📦 ' }),
            el('span', { text: item.name }),
            item.is_auto
              ? el('span.tag', { text: '自动安全备份', style: { marginLeft: '8px' } })
              : null,
          ].filter(Boolean)),
          el('div.field-hint', {
            text:
              FF.formatDate(item.created_at, 'YYYY-MM-DD HH:mm') +
              ' · ' +
              item.size_label,
          }),
        ]),
        actions,
      ]);
    });

    const wrap = el('div', { style: { display: 'grid', gap: '8px' } }, rows);
    listBox.appendChild(
      el('div.field-hint', {
        text: '共 ' + items.length + ' 份备份',
        style: { marginBottom: '6px' },
      })
    );
    listBox.appendChild(wrap);
  }

  function hintLine(text) {
    return el('div.field-hint', { text: text });
  }
  function successLine(text) {
    return el('div.field-hint', {
      text: '✅ ' + text,
      style: { color: 'var(--firefly, #5eead4)' },
    });
  }
  function errorLine(text) {
    return el('div.field-hint', {
      text: '⚠️ ' + text,
      style: { color: 'var(--danger, #f87171)' },
    });
  }

  /**
   * 修改登录口令卡片。
   *
   * 三个输入框都**不设置 value 回填**：口令永远不预填，
   * 避免密码被浏览器或"查看源代码"直接看到。
   */
  function buildPasswordCard() {
    const oldInput = el('input.input', {
      type: 'password',
      id: 'pwd-old',
      placeholder: '请输入当前口令',
      autocomplete: 'current-password',
    });

    const newInput = el('input.input', {
      type: 'password',
      id: 'pwd-new',
      placeholder: '新口令（至少 8 位，含大小写/数字/符号中三类）',
      autocomplete: 'new-password',
    });

    const confirmInput = el('input.input', {
      type: 'password',
      id: 'pwd-confirm',
      placeholder: '再次输入新口令',
      autocomplete: 'new-password',
    });

    const strengthBar = el('div.pwd-strength', { id: 'pwd-strength' });

    // 实时强度提示：让用户在提交前就知道口令够不够强，
    // 而不是提交后被打回一次才知道规则。
    newInput.addEventListener('input', function () {
      const value = newInput.value;
      if (!value) {
        strengthBar.textContent = '';
        strengthBar.className = 'pwd-strength';
        return;
      }

      let kinds = 0;
      if (/[a-z]/.test(value)) kinds += 1;
      if (/[A-Z]/.test(value)) kinds += 1;
      if (/[0-9]/.test(value)) kinds += 1;
      if (/[^a-zA-Z0-9]/.test(value)) kinds += 1;

      let level, text, cls;
      if (value.length < 8) {
        level = 0;
        text = '长度不足 8 位';
        cls = 'is-weak';
      } else if (kinds <= 1) {
        level = 1;
        text = '强度：弱（字符种类过少）';
        cls = 'is-weak';
      } else if (kinds === 2) {
        level = 2;
        text = '强度：中';
        cls = 'is-medium';
      } else {
        level = 3;
        text = '强度：强';
        cls = 'is-strong';
      }

      strengthBar.textContent = text + (level >= 2 ? '' : ' —— 需至少三类字符');
      strengthBar.className = 'pwd-strength ' + cls;
    });

    const submitBtn = el('button.btn.btn-primary', {
      text: '🔑 修改口令',
      on: {
        click: async function () {
          const oldPwd = oldInput.value;
          const newPwd = newInput.value;
          const confirmPwd = confirmInput.value;

          if (!oldPwd || !newPwd) {
            FF.toast('请填写当前口令与新口令', 'warning');
            return;
          }
          if (newPwd !== confirmPwd) {
            FF.toast('两次输入的新口令不一致', 'warning');
            return;
          }
          if (newPwd === oldPwd) {
            FF.toast('新口令不能与当前口令相同', 'warning');
            return;
          }

          submitBtn.classList.add('is-loading');
          submitBtn.disabled = true;

          try {
            const envelope = await api.changePassword(oldPwd, newPwd);

            if (envelope && envelope.success === false) {
              FF.toast(envelope.message || '修改失败', 'error', 4000);
              return;
            }

            // 后端在成功时会换发新令牌（旧令牌虽仍可用，但当前浏览器
            // 不应停在旧令牌上），这里把它同步到本地会话状态。
            const data = (envelope && envelope.data) || {};
            if (data.token && window.FFAuth && window.FFAuth.adoptToken) {
              window.FFAuth.adoptToken(data.token);
            }

            FF.toast(data.message || '口令已修改，请牢记新口令', 'success', 3600);
            oldInput.value = '';
            newInput.value = '';
            confirmInput.value = '';
            strengthBar.textContent = '';
            strengthBar.className = 'pwd-strength';
          } catch (err) {
            FF.toast('修改失败：' + (err.message || '未知错误'), 'error', 4000);
          } finally {
            submitBtn.classList.remove('is-loading');
            submitBtn.disabled = false;
          }
        },
      },
    });

    return el('div.card.reveal', {}, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [
          el('span.icon', { text: '🔑' }),
          el('span', { text: '修改登录口令' }),
        ]),
      ]),
      el('p.setting-desc', {
        text:
          '修改口令需要先验证当前口令 —— 这是刻意的：若只凭登录态就能改口令，' +
          '一旦令牌被窃取，攻击者可以直接改掉口令把你锁在门外。',
      }),
      el('div.setting-field', {}, [el('label.setting-label', { text: '当前口令' }), oldInput]),
      el('div.setting-field', {}, [el('label.setting-label', { text: '新口令' }), newInput, strengthBar]),
      el('div.setting-field', {}, [el('label.setting-label', { text: '确认新口令' }), confirmInput]),
      el('div.setting-actions', {}, [submitBtn]),
    ]);
  }

  function buildSettingField(path, field, value, inputs, patch) {
    const label = FIELD_LABELS[field] || field;
    const isArray = Array.isArray(value);
    const type = typeof value;

    let control = null;

    if (type === 'boolean') {
      const checkbox = el('input', {
        type: 'checkbox',
        checked: Boolean(value),
        style: { width: '18px', height: '18px', accentColor: 'var(--firefly)', cursor: 'pointer' },
        on: {
          change: function () {
            patch[path] = checkbox.checked;
          },
        },
      });
      control = el('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '11px 14px',
          borderRadius: '8px',
          border: '1px solid var(--border)',
          background: 'var(--bg-elevated)',
        },
      }, [checkbox, el('span', { text: checkbox.checked ? '已启用' : '已关闭', style: { fontSize: '13px' } })]);
      inputs[path] = checkbox;
    } else if (type === 'number') {
      const numberInput = el('input.input', {
        type: 'number',
        value: String(value),
        step: Number.isInteger(value) ? '1' : '0.01',
        on: {
          input: function () {
            patch[path] = Number(numberInput.value);
          },
        },
      });
      control = numberInput;
      inputs[path] = numberInput;
    } else if (isArray) {
      const arrayInput = el('input.input', {
        value: value.join(', '),
        placeholder: '以英文逗号分隔',
        on: {
          input: function () {
            patch[path] = arrayInput.value
              .split(',')
              .map(function (s) { return s.trim(); })
              .filter(Boolean);
          },
        },
      });
      control = arrayInput;
      inputs[path] = arrayInput;
    } else {
      const textInput = el('input.input', {
        value: String(value),
        on: {
          input: function () {
            patch[path] = textInput.value;
          },
        },
      });
      control = textInput;
      inputs[path] = textInput;
    }

    return el('div.field', {}, [
      el('label.field-label', {}, [
        el('span', { text: label }),
        el('span.field-hint', { text: path, style: { marginLeft: '8px', fontFamily: 'ui-monospace, monospace' } }),
      ]),
      control,
      FIELD_HINTS[field] ? el('div.field-hint', { text: FIELD_HINTS[field] }) : null,
    ].filter(Boolean));
  }

  const FIELD_LABELS = {
    name: '应用名称',
    subtitle: '副标题',
    version: '版本号',
    debug: '演示模式（使用模拟数据）',
    timezone: '时区',
    host: '监听地址',
    port: '服务端口',
    show_welcome: '启动时显示欢迎信息',
    echo: '打印 SQL',
    enabled: '启用',
    base_url: 'API 地址',
    model: '模型名称',
    temperature: '温度',
    max_tokens: '最大输出长度',
    timeout: '超时（秒）',
    max_retries: '重试次数',
    currency: '币种',
    symbol: '货币符号',
    default_period: '默认统计周期',
    custom_period_days: '自定义周期选项',
    large_amount_threshold: '大额阈值',
    duplicate_window_minutes: '重复记账窗口（分钟）',
    auto_confirm_hours: '自动确认时限（小时）',
    allow_custom_category: '允许自定义类别',
    quick_amounts: '快捷金额',
    memo_required: '备注必填',
    method: '检测方法',
    zscore_threshold: 'Z-Score 阈值',
    min_samples: '最小样本数',
    risk_keywords_weight: '风险关键词权重',
    taxpayer_type: '纳税人身份',
    vat_rate_small_scale: '小规模征收率（%）',
    vat_rate_general: '一般计税税率（%）',
    vat_threshold_monthly: '月免征额',
    tax_burden_low: '税负率下限（%）',
    tax_burden_high: '税负率上限（%）',
    category_concentration_warn: '类别集中度预警（%）',
    warn_ratio: '预警比例',
    over_ratio: '超支比例',
    daily_expense_limit: '单日支出上限',
    low_balance_threshold: '余额安全线',
    target_type: '推送目标类型',
    target_id: '推送目标 ID',
    schedule_times: '定时推送时间',
    send_image: '推送图片',
    send_summary_text: '推送文字摘要',
    report_period: '报告周期',
    image_width: '图片宽度',
    image_scale: '渲染缩放',
    theme: '主题',
    accent_color: '强调色',
    particle_enabled: '萤火粒子背景',
    report_animation: '报告生成动画',
    counter_duration: '数字动画时长（ms）',
    page_size: '列表每页条数',
    track_adoption: '统计建议采纳率',
    run_day: '订阅执行日',
    run_time: '订阅执行时间',
    persist_dir: '向量库路径',
    collection_name: '集合名称',
    top_k: '检索条数',
    score_threshold: '相似度阈值',
  };

  const FIELD_HINTS = {
    api_key: '敏感项，请编辑根目录 .env 中的 LLM_API_KEY',
    port: '修改后需重启后端生效',
    api_key_env: '从环境变量读取的键名，不在前端展示真实值',
    debug: '开启后前端全部使用模拟数据，不请求真实接口，适合演示与联调排查',
    zscore_threshold: '数值越小越敏感，建议 2.5-3.0',
    vat_threshold_monthly: '小规模纳税人月销售额免征额，单位元',
    warn_ratio: '例如 0.8 表示使用 80% 时预警',
    particle_enabled: '关闭可提升低配设备流畅度',
    disclaimer: '报告尾部免责声明，可自定义',
  };

  function buildReadonlyCard(settings) {
    const info = [
      { label: '配置文件', value: 'config.yaml（根目录）' },
      { label: '敏感配置', value: '.env（不进入版本库）' },
      { label: '数据库', value: (settings.database && settings.database.url) || 'sqlite+aiosqlite:///./data/firefly.db' },
      { label: '协议端', value: 'OneBot v11' },
      { label: '前端目录', value: 'index/' },
    ];

    return el('div.card', { style: { marginTop: '8px' } }, [
      el('div.card-header', {}, [
        el('div.card-title', {}, [el('span.icon', { text: '📁' }), el('span', { text: '部署信息（只读）' })]),
      ]),
      el('div', { style: { display: 'flex', flexDirection: 'column', gap: '11px' } },
        info.map(function (item) {
          return el('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '14px', fontSize: '13px' } }, [
            el('span', { text: item.label, style: { color: 'var(--text-secondary)' } }),
            el('span', { text: item.value, style: { fontFamily: 'ui-monospace, monospace', fontSize: '12.5px', color: 'var(--text-primary)' } }),
          ]);
        })
      ),
    ]);
  }

  function buildMockSettings() {
    return {
      app: { name: '流萤 Firefly', subtitle: '财报总结优化 · 财税智能助手', version: '1.0.0', debug: false, timezone: 'Asia/Shanghai' },
      server: { host: '127.0.0.1', port: 2525, show_welcome: true },
      database: { url: 'sqlite+aiosqlite:///./data/firefly.db', echo: false },
      ledger: {
        currency: 'CNY',
        symbol: '¥',
        default_period: 'monthly',
        large_amount_threshold: 5000,
        duplicate_window_minutes: 10,
        auto_confirm_hours: 0,
      },
      llm: { enabled: true, base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', temperature: 0.3, max_tokens: 4096, timeout: 60, max_retries: 2 },
      anomaly: { enabled: true, method: 'zscore', zscore_threshold: 2.5, min_samples: 10, risk_keywords_weight: 0.6 },
      tax_check: {
        taxpayer_type: 'small_scale',
        vat_rate_small_scale: 1,
        vat_rate_general: 6,
        vat_threshold_monthly: 100000,
        tax_burden_low: 1,
        tax_burden_high: 8,
        category_concentration_warn: 40,
      },
      budget: { warn_ratio: 0.8, over_ratio: 1 },
      alert: { daily_expense_limit: 3000, low_balance_threshold: 1000 },
      push: { enabled: false, target_type: 'group', schedule_times: ['09:00'], send_image: true, send_summary_text: true, report_period: 'monthly', image_width: 800, image_scale: 2 },
      ui: { theme: 'firefly-dark', accent_color: '#5eead4', particle_enabled: true, report_animation: true, counter_duration: 1200, page_size: 20 },
    };
  }
})();
