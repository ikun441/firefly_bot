/* Unique administrator, independent live QQ CRUD grants. No mock authorization. */
(function () {
  'use strict';
  const FF=window.FF, el=FF.el, api=window.FFApi.api;
  function input(label,value,type) {
    const field=el('input.input',{type:type||'text',value:value||''});
    return {field:field,node:el('label.form-group',{},[el('span',{text:label}),field])};
  }
  window.FFViews.permissions={render:async function(root) {
    root.classList.add('ff-page');
    const status=el('p',{role:'status','aria-live':'polite'});
    root.append(el('h1',{text:'权限设置'}),el('p',{text:'有且仅有一个管理员。QQ 普通账号的增、删、改、查独立授权；未授权默认拒绝。已过账凭证不能直接改删。'}),status);
    const body=el('div');root.append(body);
    async function run(action) {
      status.textContent='';
      try {await action();status.textContent='已保存，权限立即生效。';await draw();}
      catch(e){status.textContent=e.message;}
    }
    async function draw() {
      const data=(await api.raw('/v2/permissions')).data;
      FF.clear(body);
      const admin=input('唯一管理员账号',data.administrator);admin.field.disabled=true;
      const qq=input('管理员绑定 QQ（可留空，不创建第二个管理员）',data.admin_qq);
      body.append(el('section.card.accounting-panel',{},[el('h2',{text:'管理员身份'}),admin.node,qq.node,
        el('button.btn.btn-primary',{type:'button',text:'保存绑定',on:{click:()=>run(()=>api.raw('/v2/permissions/administrator',{method:'PUT',body:{admin_qq:qq.field.value.trim(),expected_version:data.version}}))}})]));
      const name=input('显示名称',''),number=input('QQ 账号','');
      number.field.inputMode='numeric';
      const fields={};
      const grantForm=el('form.card.accounting-panel',{},[el('h2',{text:'添加 / 修改 QQ 授权'}),number.node,name.node]);
      [['can_create','增：录入业务 / 创建草稿'],['can_delete','删：作废 / 申请冲销'],['can_update','改：修改未过账草稿 / 提交审核'],['can_read','查：查询账务 / 报告']].forEach(([key,label])=>{
        const box=el('input',{type:'checkbox'});fields[key]=box;
        grantForm.append(el('label.accounting-check',{},[box,el('span',{text:label})]));
      });
      const enabled=el('input',{type:'checkbox',checked:true});
      grantForm.append(el('label.accounting-check',{},[enabled,el('span',{text:'启用该账号'})]),el('button.btn.btn-primary',{type:'submit',text:'保存 QQ 权限'}));
      grantForm.addEventListener('submit',e=>{
        e.preventDefault();
        run(()=>api.raw('/v2/permissions/qq/'+encodeURIComponent(number.field.value.trim()),{method:'PUT',body:{
          nickname:name.field.value,enabled:enabled.checked,...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.checked]))
        }}));
      });
      body.append(grantForm);
      const list=el('section.card.accounting-panel',{},[el('h2',{text:'已授权 QQ 账号'})]);
      if(!data.grants.length)list.append(el('p',{text:'尚未添加普通 QQ 账号。'}));
      data.grants.forEach(g=>{
        const rights=[['can_create','增'],['can_delete','删'],['can_update','改'],['can_read','查']].filter(([k])=>g[k]).map(x=>x[1]).join(' / ')||'无';
        list.append(el('div.accounting-grant',{},[el('span',{text:g.qq+' · '+(g.nickname||'未命名')+' · '+(g.enabled?rights:'已停用')}),
          el('button.btn',{type:'button',text:'编辑',on:{click:()=>{number.field.value=g.qq;name.field.value=g.nickname;enabled.checked=g.enabled;Object.keys(fields).forEach(k=>fields[k].checked=g[k]);grantForm.scrollIntoView({behavior:'smooth'});number.field.focus();}}}),
          el('button.btn',{type:'button',text:'撤销全部权限',on:{click:()=>run(()=>api.raw('/v2/permissions/qq/'+g.qq,{method:'DELETE'}))}})]));
      });
      body.append(list);
      const events=(await api.raw('/v2/permission-events')).data;
      const audit=el('section.card.accounting-panel',{},[el('h2',{text:'权限操作记录'})]);
      audit.append(window.FFAccounting.events(events.slice(0,30)));
      body.append(audit);
    }
    await draw();
  }};
})();
