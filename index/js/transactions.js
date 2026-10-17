/* Full transaction management. All edits use the authenticated API and server audit trail. */
(function () {
  'use strict';
  const FF = window.FF, el = FF.el, api = window.FFApi.api;
  function check(envelope) {
    if (!envelope || envelope.success === false) throw new Error(envelope && envelope.message || '操作失败');
    return window.FFApi.unwrap(envelope);
  }
  function select(label, choices) {
    const input = el('select.select', { 'aria-label': label });
    choices.forEach(function (item) { input.appendChild(el('option', { value: String(item.id), text: item.name })); });
    return input;
  }
  function field(label, input) { return el('label.field', {}, [el('span.field-label', { text: label }), input]); }
  function localDate(value) {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return '';
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  window.FFViews.transactions = {
    async render(container) {
      const dialogs = new Set();
      let disposed = false, page = 0, logPage = 0, serial = 0, logSerial = 0, query = {};
      const pageSize = 30, logSize = 20;
      container.appendChild(el('div.page-head', {}, [el('h1.page-title', { text: '流水管理' }), el('p.page-subtitle', { text: '查询全部流水，修正类别、账本、收支与发票信息。每次新增、修改和删除均保留操作记录。' })]));
      if (window.FFApi.isDebug()) {
        container.appendChild(el('div.card', { text: '流水管理使用真实财务数据，请关闭演示模式后操作。' }));
        return;
      }
      const categories = check(await api.categories.list());
      const accounts = check(await api.accountsList());
      if (disposed) return;
      const categoryMap = new Map(categories.map(function (c) { return [String(c.id), c.name]; }));
      const accountMap = new Map(accounts.map(function (a) { return [String(a.id), a.name]; }));
      const keyword = el('input.input', { type: 'search', placeholder: '搜索备注或交易对方', maxlength: 128, 'aria-label': '关键词' });
      const direction = select('收支方向', [{ id: '', name: '全部收支' }, { id: 'expense', name: '支出' }, { id: 'income', name: '收入' }]);
      const category = select('类别', [{ id: '', name: '全部类别' }].concat(categories));
      const account = select('账本', [{ id: '', name: '全部账本' }].concat(accounts));
      const start = el('input.input', { type: 'date', 'aria-label': '开始日期' });
      const end = el('input.input', { type: 'date', 'aria-label': '结束日期' });
      const list = el('div', { 'aria-live': 'polite' });
      const logs = el('div', { 'aria-live': 'polite' });
      const logTitle = el('h2.card-title', { text: '操作日志 · 全部流水' });
      const filterForm = el('form.card', {}, [
        el('div.card-header', {}, [el('h2.card-title', { text: '查找流水' }), el('button.btn.btn-primary', { type: 'button', text: '＋ 新增流水', on: { click: function () { edit(null); } } })]),
        el('div.transaction-filters', {}, [field('关键词', keyword), field('收支', direction), field('类别', category), field('账本', account), field('开始日期', start), field('结束日期（含当天）', end)]),
        el('div.btn-group', {}, [el('button.btn.btn-primary', { type: 'submit', text: '查询' }), el('button.btn', { type: 'button', text: '重置', on: { click: function () { filterForm.reset(); query = {}; page = 0; load(); } } })]),
      ]);
      filterForm.addEventListener('submit', function (event) {
        event.preventDefault();
        if (start.value && end.value && start.value > end.value) { FF.toast('结束日期不能早于开始日期', 'warning'); return; }
        query = { keyword: keyword.value.trim(), direction: direction.value, category_id: category.value, account_id: account.value };
        if (start.value) query.start = new Date(start.value + 'T00:00:00').toISOString();
        if (end.value) { const next = new Date(end.value + 'T00:00:00'); next.setDate(next.getDate() + 1); query.end = next.toISOString(); }
        page = 0; load();
      });
      let logTarget = null;
      const logSection = el('section.card', {}, [el('div.card-header', {}, [logTitle, el('button.btn', { type: 'button', text: '查看全部日志', on: { click: function () { logTarget = null; logPage = 0; loadLogs(); } } })]), logs]);
      container.appendChild(filterForm); container.appendChild(list); container.appendChild(logSection);
      async function loadLogs() {
        const current = ++logSerial;
        logs.textContent = '正在读取操作记录…';
        logTitle.textContent = logTarget ? '操作日志 · 流水 #' + logTarget : '操作日志 · 全部流水';
        try {
          const envelope = await api.auditLogs({ target_type: 'transaction', target_id: logTarget, limit: logSize, offset: logPage * logSize });
          const rows = check(envelope);
          if (disposed || current !== logSerial) return;
          FF.clear(logs);
          if (!rows.length) logs.appendChild(el('p.field-hint', { text: '暂无操作记录。历史版本的日志可能没有修改前后的快照。' }));
          rows.forEach(function (log) {
            logs.appendChild(el('details.transaction-log', {}, [
              el('summary', { text: FF.formatDate(log.created_at, 'YYYY-MM-DD HH:mm') + ' · ' + log.action + ' · #' + (log.target_id || '批次') + ' · ' + log.channel + ' · ' + log.operator }),
              el('p', { text: log.detail || '' }),
              el('pre', { text: JSON.stringify(log.extra || {}, null, 2) }),
            ]));
          });
          logs.appendChild(pager(logPage, envelope.total || 0, logSize, function (value) { logPage = value; loadLogs(); }));
        } catch (error) { if (current === logSerial) logs.textContent = '日志加载失败：' + error.message; }
      }
      function pager(index, total, size, change) {
        return el('div.transaction-pagination', {}, [
          el('span.field-hint', { text: '共 ' + total + ' 条 · 第 ' + (index + 1) + ' / ' + Math.max(1, Math.ceil(total / size)) + ' 页' }),
          el('button.btn', { type: 'button', text: '上一页', disabled: index === 0, on: { click: function () { change(index - 1); } } }),
          el('button.btn', { type: 'button', text: '下一页', disabled: (index + 1) * size >= total, on: { click: function () { change(index + 1); } } }),
        ]);
      }
      async function load() {
        const current = ++serial;
        list.setAttribute('aria-busy', 'true'); list.textContent = '正在读取流水…';
        try {
          const envelope = await api.transactions.list(Object.assign({}, query, { limit: pageSize, offset: page * pageSize }));
          const rows = check(envelope);
          if (disposed || current !== serial) return;
          if (!rows.length && page > 0) { page -= 1; return load(); }
          FF.clear(list);
          const table = el('table.data-table', {}, [el('thead', {}, [el('tr', {}, ['时间', '收支 / 金额', '类别 / 账本', '交易对方 / 备注', '发票', '操作'].map(function (text) { return el('th', { text: text }); }))])]);
          const body = el('tbody');
          rows.forEach(function (row) {
            const actions = el('div.btn-group', {}, [
              el('button.btn.btn-sm', { text: '编辑', type: 'button', on: { click: function () { edit(row); } } }),
              el('button.btn.btn-sm', { text: '日志', type: 'button', on: { click: function () { logTarget = row.id; logPage = 0; loadLogs(); logSection.scrollIntoView({ behavior: 'smooth', block: 'start' }); } } }),
              el('button.btn.btn-sm', { text: '删除', type: 'button', on: { click: async function (event) {
                const button = event.currentTarget;
                if (!await FF.confirm({ title: '删除流水', message: '确认删除 ' + FF.formatMoney(row.amount) + ' 的流水？删除会保留操作日志。', confirmText: '删除', danger: true })) return;
                button.disabled = true;
                try { check(await api.transactions.remove(row.id)); FF.toast('流水已删除', 'success'); changed(); await load(); await loadLogs(); }
                catch (error) { FF.toast(error.message, 'error'); }
                finally { button.disabled = false; }
              } } }),
            ]);
            body.appendChild(el('tr', {}, [
              el('td', { text: FF.formatDate(row.trade_time, 'YYYY-MM-DD HH:mm') }),
              el('td', { class: row.direction === 'income' ? 'text-income' : 'text-expense', text: (row.direction === 'income' ? '收入 +' : '支出 −') + FF.formatMoney(row.amount) }),
              el('td', {}, [el('div', { text: categoryMap.get(String(row.category_id)) || '未分类' }), el('small.text-muted', { text: accountMap.get(String(row.account_id)) || '未关联账本' })]),
              el('td.transaction-memo', {}, [el('div', { text: row.counterparty || '—' }), el('small.text-muted', { text: row.memo || '—' })]),
              el('td', { text: row.has_invoice ? '有发票' : '无发票' }), el('td', {}, [actions]),
            ]));
          });
          table.appendChild(body);
          list.appendChild(el('section.card', {}, [el('div.table-wrap', {}, [table]), rows.length ? null : el('p.field-hint', { text: '没有符合条件的流水。' }), pager(page, envelope.total || 0, pageSize, function (value) { page = value; load(); })].filter(Boolean)));
        } catch (error) { if (current === serial) { list.textContent = '加载失败：' + error.message; list.appendChild(el('button.btn', { text: '重试', on: { click: load } })); } }
        finally { if (current === serial) list.removeAttribute('aria-busy'); }
      }
      function changed() { window.dispatchEvent(new CustomEvent('firefly:transactions-changed')); }
      function edit(row) {
        const dialog = el('dialog.transaction-dialog'); dialogs.add(dialog);
        const heading = 'transaction-form-' + Date.now(); dialog.setAttribute('aria-labelledby', heading);
        const form = el('form', { class: 'card' });
        const amount = el('input.input', { type: 'number', min: '0.01', max: '100000000', step: '0.01', required: true, value: row ? row.amount : '' });
        const dir = select('收支方向', [{ id: 'expense', name: '支出' }, { id: 'income', name: '收入' }]); dir.value = row ? row.direction : 'expense';
        const cat = select('类别', []);
        function fillCategories(initial) {
          FF.clear(cat); cat.appendChild(el('option', { value: '', text: '未分类' }));
          categories.filter(function (c) { return c.direction === dir.value; }).forEach(function (c) { cat.appendChild(el('option', { value: String(c.id), text: c.name })); });
          cat.value = initial ? String(initial) : ''; if (!cat.value) cat.value = '';
        }
        fillCategories(row && row.category_id); dir.addEventListener('change', function () { fillCategories(null); });
        const acc = select('账本', [{ id: '', name: '不关联账本' }].concat(accounts)); acc.value = String(row && row.account_id || '');
        const date = el('input.input', { type: 'datetime-local', required: true, value: localDate(row && row.trade_time) });
        const counterparty = el('input.input', { maxlength: 128, value: row && row.counterparty || '' });
        const memo = el('textarea.input', { maxlength: 512, rows: 3, value: row && row.memo || '' });
        const invoice = el('input', { type: 'checkbox', checked: !!(row && row.has_invoice) });
        const error = el('p.field-hint', { role: 'alert', style: { color: 'var(--danger)' } });
        const save = el('button.btn.btn-primary', { type: 'submit', text: '保存' });
        function close() { dialog.close(); dialog.remove(); dialogs.delete(dialog); }
        form.appendChild(el('div.card-header', {}, [el('h2.card-title', { id: heading, text: row ? '编辑流水 #' + row.id : '新增流水' }), el('button.btn', { type: 'button', text: '关闭', on: { click: close } })]));
        form.appendChild(el('div.transaction-form-grid', {}, [field('金额（元）', amount), field('收支方向', dir), field('类别', cat), field('账本', acc), field('交易时间', date), field('交易对方', counterparty)]));
        form.appendChild(field('备注', memo)); form.appendChild(field('已开票 / 有发票', invoice)); form.appendChild(error);
        form.appendChild(el('div.btn-group', {}, [save, el('button.btn', { type: 'button', text: '取消', on: { click: close } })]));
        let saving = false;
        form.addEventListener('submit', async function (event) {
          event.preventDefault(); if (saving) return;
          const value = Number(amount.value);
          if (!Number.isFinite(value) || value <= 0 || value > 100000000 || Math.abs(value * 100 - Math.round(value * 100)) > 0.00001) { error.textContent = '请填写有效金额，最多两位小数。'; return; }
          saving = true; save.disabled = true; error.textContent = '';
          try {
            const payload = { amount: value, direction: dir.value, category_id: cat.value ? Number(cat.value) : null, account_id: acc.value ? Number(acc.value) : null, trade_time: new Date(date.value).toISOString(), counterparty: counterparty.value.trim(), memo: memo.value.trim(), has_invoice: invoice.checked };
            check(row ? await api.raw('/transactions/' + encodeURIComponent(row.id), { method: 'PATCH', body: payload }) : await api.transactions.create(payload));
            close(); FF.toast('流水已保存', 'success'); changed(); await load(); await loadLogs();
          } catch (err) { error.textContent = err.message; }
          finally { saving = false; save.disabled = false; }
        });
        dialog.addEventListener('cancel', function (event) { event.preventDefault(); if (!saving) close(); });
        dialog.appendChild(form); document.body.appendChild(dialog); dialog.showModal(); amount.focus();
      }
      await Promise.all([load(), loadLogs()]);
      return function () { disposed = true; serial += 1; logSerial += 1; dialogs.forEach(function (d) { d.close(); d.remove(); }); dialogs.clear(); };
    },
  };
})();
