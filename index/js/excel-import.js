/* Native XLSX upload view: server validation is authoritative. */
(function () {
  'use strict';
  const FF = window.FF;
  const el = FF.el;
  const api = window.FFApi;
  window.FFViews.excel = {
    async render(container) {
      container.appendChild(el('div.page-head', {}, [
        el('h1.page-title', { text: 'Excel 导入记账' }),
        el('p.page-subtitle', { text: '下载通用模板，填写流水后批量入账。成功后流萤会向已配置的协议端发送汇总。' }),
      ]));
      const result = el('section.stack', { 'aria-live': 'polite', 'aria-label': '导入结果' });
      const account = el('select.select', { id: 'excel-account', 'aria-label': '默认账本' }, [el('option', { value: '', text: '按表格中的账本名称匹配' })]);
      const accountHint = el('div.field-hint', { text: '表内账本名称留空时使用默认账本；填写名称时优先按名称匹配。' });
      let accounts = [];
      try {
        const payload = await api.api.accountsList();
        const data = api.unwrap(payload);
        accounts = Array.isArray(data) ? data : data.items || [];
        accounts.forEach(function (item) { account.appendChild(el('option', { value: String(item.id), text: item.name })); });
        if (!accounts.length) accountHint.textContent = '还没有账本，请先到「账本管理」添加资金账户。';
      } catch (error) { accountHint.textContent = '账本加载失败：' + error.message; }
      let selected = null;
      let busy = false;
      const file = el('input', { id: 'excel-file', type: 'file', accept: '.xlsx', 'aria-label': '选择 Excel 账单' });
      const filename = el('strong', { text: '选择或拖入 Excel 文件' });
      const fileHint = el('span', { text: '.xlsx · 最大 8MB · 最多 2000 笔', class: 'text-muted' });
      const dropzone = el('label.excel-dropzone', { htmlFor: 'excel-file' }, [
        el('span.excel-file-symbol', { text: '↥', 'aria-hidden': 'true' }), filename, fileHint, file,
      ]);
      function choose(candidate) {
        if (busy || !candidate) return;
        if (!/\.xlsx$/i.test(candidate.name) || candidate.size > 8 * 1024 * 1024 || !candidate.size) {
          selected = null; filename.textContent = '请选择非空的 .xlsx 文件，大小不超过 8MB';
          file.value = ''; return;
        }
        selected = candidate; filename.textContent = candidate.name;
        fileHint.textContent = (candidate.size / 1024).toFixed(1) + ' KB · 已准备上传';
        FF.clear(result);
      }
      file.addEventListener('change', function () { choose(file.files[0]); });
      dropzone.addEventListener('dragover', function (event) { event.preventDefault(); if (!busy) dropzone.classList.add('is-dragging'); });
      dropzone.addEventListener('dragleave', function () { dropzone.classList.remove('is-dragging'); });
      dropzone.addEventListener('drop', function (event) { event.preventDefault(); dropzone.classList.remove('is-dragging'); choose(event.dataTransfer.files[0]); });
      const preview = el('button.btn', { text: '检查文件', type: 'button', on: { click: function () { upload(true); } } });
      const submit = el('button.btn.btn-primary', { text: '导入并通知协议端', type: 'button', on: { click: function () { upload(false); } } });
      container.appendChild(el('div.grid.grid-2.excel-layout', {}, [
        el('div.card', {}, [
          el('div.card-header', {}, [el('h2.card-title', { text: '01 / 准备模板' }), el('span.tag', { text: '通用 XLSX' })]),
          el('p.excel-description', { text: '「记账数据」页只填写真实流水，「填写说明」页不参与导入。请保留表头，从第二行开始填写。' }),
          el('a.btn', { href: '/api/import/excel/template', text: '↓ 下载通用 Excel 模板', download: '' }),
          el('ul.excel-rules', {}, [
            el('li', { text: '必填：交易日期、收支类型（收入 / 支出）、金额。' }),
            el('li', { text: '金额填写正数，单位为元，最多两位小数，单笔不超过 1 亿元。' }),
            el('li', { text: '日期示例：2026-10-17 12:30，也支持 Excel 日期单元格。' }),
            el('li', { text: '账本和类别使用已有名称；类别须与收支类型一致。' }),
            el('li', { text: '发票填「是 / 否」，留空按「否」处理。不要使用公式、合并单元格或加密文件。' }),
            el('li', { text: '校验错误时整批不入账；同账本、同方向的疑似重复流水会跳过并列出。' }),
          ]),
        ]),
        el('div.card', {}, [
          el('div.card-header', {}, [el('h2.card-title', { text: '02 / 上传并入账' })]),
          el('div.field', {}, [el('label.field-label', { htmlFor: 'excel-account', text: '默认账本' }), account, accountHint]),
          dropzone,
          el('div.btn-group', { style: { marginTop: '18px' } }, [preview, submit]),
          el('p.field-hint', { text: '「检查文件」只校验、不保存。正式导入完成后会显示新增、重复和推送结果。', style: { marginTop: '14px' } }),
        ]),
      ]));
      container.appendChild(result);
      if (api.isDebug()) {
        container.appendChild(el('div.excel-notice', { text: '当前为演示模式。Excel 导入使用真实接口和账本，请切回正式模式后操作。' }));
        preview.disabled = submit.disabled = true;
      }
      async function upload(dryRun) {
        if (busy) return;
        if (!selected) { FF.toast('请先选择 Excel 文件', 'warning'); file.focus(); return; }
        busy = true; preview.disabled = submit.disabled = file.disabled = account.disabled = true;
        const active = dryRun ? preview : submit; active.classList.add('is-loading');
        result.setAttribute('aria-busy', 'true'); FF.clear(result);
        result.appendChild(el('div.excel-notice', { text: dryRun ? '正在检查格式、字段与账本归属…' : '正在入账，请等待完成后再关闭页面，避免重复提交…' }));
        try {
          const body = new FormData(); body.append('file', selected);
          if (account.value) body.append('account_id', account.value);
          body.append('dry_run', String(dryRun));
          const response = await api.raw('/import/excel', { method: 'POST', body: body, timeout: 180000 });
          FF.clear(result);
          if (response.success === false) {
            result.appendChild(el('div.card', {}, [el('h2.card-title', { text: response.message || '文件校验失败' }), el('p.field-hint', { text: '本次没有新增账目。修正后可重新上传。' })]));
            const errors = response.detail && response.detail.errors || [];
            if (errors.length) result.appendChild(el('ul.excel-errors', {}, errors.map(function (error) { return el('li', { text: '第 ' + error.row + ' 行：' + error.message }); })));
            return;
          }
          const data = api.unwrap(response);
          const notification = data.notification || {};
          result.appendChild(el('div.card', {}, [
            el('h2.card-title', { text: dryRun ? '文件校验通过 · 尚未入账' : data.message }),
            el('p.excel-description', { text: dryRun ? '共识别 ' + data.total + ' 笔，以下预览最多展示 50 笔。可点击「导入并通知协议端」保存。' : '批次 #' + data.batch_id + ' · 收入 ¥' + data.income.toFixed(2) + ' · 支出 ¥' + data.expense.toFixed(2) }),
            el('div.excel-notice', { text: dryRun ? '预览不会发送通知' : notification.message || '账目已保存', dataset: { tone: notification.sent ? 'success' : 'info' } }),
          ]));
          if (dryRun) {
            const table = el('table.table', {}, [el('thead', {}, [el('tr', {}, ['行号','交易日期','收支','金额','账本','备注'].map(function (label) { return el('th', { text: label }); }))])]);
            const tbody = el('tbody');
            (data.preview || []).forEach(function (row) { tbody.appendChild(el('tr', {}, [row.row, FF.formatDate(row.trade_time,'YYYY-MM-DD HH:mm'), row.direction === 'income' ? '收入' : '支出', FF.formatMoney(row.amount), row.account_name || (accounts.find(function (a) { return a.id === row.account_id; }) || {}).name || '', row.memo].map(function (value) { return el('td', { text: String(value) }); }))); });
            table.appendChild(tbody); result.appendChild(el('div.table-wrap', {}, [table]));
          } else {
            if (data.duplicates && data.duplicates.length) result.appendChild(el('p.field-hint', { text: '重复跳过行：' + data.duplicates.map(function (item) { return item.row; }).join('、') }));
            window.dispatchEvent(new CustomEvent('firefly:import-completed', { detail: data }));
            selected = null; file.value = ''; filename.textContent = '选择下一份 Excel 文件'; fileHint.textContent = '.xlsx · 最大 8MB · 最多 2000 笔';
          }
        } catch (error) {
          FF.clear(result);
          result.appendChild(el('div.excel-notice', { text: '未收到完整响应：' + error.message + '。若已提交，请先到流水记账核对结果再重试；重复检测会跳过已入账记录。' }));
        } finally {
          busy = false; preview.disabled = submit.disabled = file.disabled = account.disabled = false;
          active.classList.remove('is-loading'); result.setAttribute('aria-busy', 'false');
        }
      }
    },
  };
})();
