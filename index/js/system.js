/* Version visibility and explicit repository update controls. */
(function () {
  'use strict';
  const FF = window.FF;
  const el = FF.el;
  let updating = false;
  function read(payload) {
    if (payload.success === false) throw new Error(payload.message || '操作失败');
    return window.FFApi.unwrap(payload);
  }
  async function info() { return read(await window.FFApi.raw('/system/version')); }
  async function update() {
    if (updating) return;
    updating = true;
    try {
      const current = await info();
      if (!current.update_supported) { FF.toast(current.reason, 'warning', 6500); return; }
      if (!await FF.confirm({ title: '从 main 拉取更新', message: '系统会备份并保留本地配置，只接受快进更新。完成后需要重启服务；源码有未提交改动时会停止。', confirmText: '拉取更新' })) return;
      const result = read(await window.FFApi.raw('/system/update', { method:'POST', headers:{'X-Firefly-Update':'1'}, timeout:150000 }));
      FF.toast(result.message, result.restart_required ? 'warning' : 'success', 10000);
      document.querySelectorAll('[data-system-message]').forEach(function (node) { node.textContent = result.message; });
    } catch (error) { FF.toast(error.message, 'error', 8000); }
    finally { updating = false; }
  }
  function buildCard() {
    const message = el('p.field-hint', { dataset: { systemMessage: '1' }, text: '正在读取版本与仓库状态…' });
    const version = el('div.system-version', { text: '流萤 Firefly' });
    const pull = el('button.btn.btn-primary', { text: '↓ 一键拉取更新', type:'button', on:{ click:async function () { pull.disabled=true;pull.classList.add('is-loading');try{await update();}finally{pull.disabled=false;pull.classList.remove('is-loading');} } } });
    async function refresh() {
      try {
        const data = await info();
        version.textContent = '运行版本 v' + data.running_version + ' · 已安装 v' + data.installed_version;
        message.textContent = data.restart_required ? '已有新代码，请重启服务后使用新版本。' : data.update_supported ? '分支 ' + data.branch + ' · 提交 ' + data.commit + ' · 更新后请重启服务' : data.reason;
        pull.disabled = !data.update_supported;
      } catch (error) { message.textContent = '版本信息读取失败：' + error.message; pull.disabled=true; }
    }
    refresh();
    return el('section.card', { id:'system-version-card' }, [el('div.card-header', {}, [el('h2.card-title', {text:'版本与仓库更新'}), el('span.tag',{text:'v1.0.3'})]),version,message,el('div.btn-group',{style:{marginTop:'18px'}},[el('button.btn',{text:'刷新版本状态',type:'button',on:{click:refresh}}),pull]),el('p.field-hint',{text:'从官方仓库 main 更新。配置备份保存在 data/updates；依赖有变化时，重启请使用 start.cmd / start.sh。',style:{marginTop:'12px'}})]);
  }
  window.FFSystem = { buildCard:buildCard, update:update };
  document.addEventListener('DOMContentLoaded', function () { const button=document.getElementById('repository-update');if(button)button.addEventListener('click',update); });
})();
