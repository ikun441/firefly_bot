/* Shared accounting controls. API objects are never rendered as JSON. */
(function(){
  'use strict';
  const el=window.FF.el,api=window.FFApi.api;
  const states={draft:'草稿',submitted:'待审核',approved:'已审核',posted:'已过账',void:'已作废',pending:'待制证',linked:'已关联凭证',accepted:'已确认'};
  const kinds={asset:'资产',liability:'负债',equity:'权益',income:'收入',expense:'费用'};
  function field(label,type,value,attrs){
    const input=el('input.input',Object.assign({type:type||'text',value:value==null?'':value},attrs||{}));
    return {input:input,node:el('label.ff-field',{},[el('span.ff-label',{text:label}),input])};
  }
  function select(label,options,value){
    const input=el('select.input',{},options.map(o=>el('option',{value:o.value,text:o.text,disabled:o.disabled||false})));
    if(value!=null)input.value=String(value);
    return {input:input,node:el('label.ff-field',{},[el('span.ff-label',{text:label}),input])};
  }
  function check(label,value){
    const input=el('input',{type:'checkbox',checked:!!value});
    return {input:input,node:el('label.ff-check',{},[input,el('span',{text:label})])};
  }
  function button(text,fn,primary){
    return el('button.btn'+(primary?'.btn-primary':''),{type:'button',text:text,on:{click:fn}});
  }
  async function request(path,body,method,key){
    const options=body===undefined?{}:{method:method||'POST',body:body,headers:{'Idempotency-Key':key||crypto.randomUUID()}};
    const response=await api.raw('/v2'+path,options);
    if(response.success===false)throw new Error(response.message||'操作未完成，请重试');
    return response.data;
  }
  function page(root,title,subtitle){
    root.classList.add('ff-page');
    const actions=el('div.ff-page-actions'),heading=el('div.ff-page-heading',{},[
      el('div',{},[el('div.ff-eyebrow',{text:'流萤 · 会计工作空间'}),el('h1',{text:title}),el('p.ff-muted',{text:subtitle})]),actions]);
    const status=el('p.ff-status',{role:'status','aria-live':'polite'}),body=el('div.ff-stack');
    root.append(heading,status,body);
    async function run(fn){
      status.textContent='';
      try{return await fn();}
      catch(e){status.textContent=e.message==='Failed to fetch'?'服务连接失败，请稍后重试':e.message;status.dataset.tone='error';window.FF.toast(status.textContent,'error');}
    }
    function success(message){status.textContent=message;status.dataset.tone='success';window.FF.toast(message,'success');}
    return {actions:actions,body:body,status:status,run:run,success:success};
  }
  function card(title,subtitle,children){
    const node=el('section.card.ff-card');
    if(title)node.append(el('div.ff-section-heading',{},[el('h2',{text:title}),subtitle?el('p.ff-muted',{text:subtitle}):el('span')]));
    (children||[]).forEach(c=>node.append(c));return node;
  }
  function empty(title,text,action){return el('div.ff-empty',{},[el('div.ff-empty-mark',{text:'◌','aria-hidden':'true'}),el('h2',{text:title}),el('p.ff-muted',{text:text}),action||el('span')]);}
  function table(headers,rows){
    const body=el('tbody');
    rows.forEach(row=>body.append(el('tr',{},row.map(cell=>el('td',{},[typeof cell==='string'||typeof cell==='number'?document.createTextNode(String(cell)):cell])))));
    return el('div.ff-table-scroll',{},[el('table.ff-table',{},[el('thead',{},[el('tr',{},headers.map(h=>el('th',{scope:'col',text:h})))]),body])]);
  }
  function actor(actor){
    if(!actor)return '尚未操作';
    const name=actor.kind==='admin'?'管理员 '+actor.identifier:actor.kind==='qq'?'QQ '+actor.identifier:'系统任务';
    return name+' · '+({web:'网页端',qq:'QQ端',system:'系统'}[actor.platform]||'系统');
  }
  function time(value){
    if(!value)return '';
    const date=new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z');
    return isNaN(date)?value:date.toLocaleString('zh-CN',{hour12:false});
  }
  function events(items){
    if(!items.length)return empty('暂无操作记录','后续操作会按时间记录在这里。');
    return el('div.ff-timeline',{},items.map(e=>el('article.ff-event',{},[
      el('div.ff-event-dot',{'aria-hidden':'true'}),
      el('div',{},[el('div.ff-event-top',{},[el('h3',{text:e.title||'账务操作'}),el('time',{text:time(e.created_at)})]),
        el('p',{text:e.description||'操作已记录'}),el('p.ff-muted',{text:(e.operator_name||actor(e.actor))+' · '+(e.platform_name||'')})])
    ])));
  }
  function date(){const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
  async function context(){
    const books=await request('/books');
    return {books:books,book:books.find(b=>b.is_current)||books[0]||null};
  }
  function minor(value){
    if(!/^\d+(?:\.\d{1,2})?$/.test(value))return null;
    const p=value.split('.');return BigInt(p[0])*100n+BigInt((p[1]||'').padEnd(2,'0'));
  }
  function money(n){const neg=n<0n;if(neg)n=-n;return (neg?'-':'')+(n/100n)+'.'+String(n%100n).padStart(2,'0');}
  function quantityAmount(quantity,price){
    function scaled(value){if(!/^\d+(?:\.\d{1,6})?$/.test(value))return null;const p=value.split('.');return BigInt(p[0])*1000000n+BigInt((p[1]||'').padEnd(6,'0'));}
    const q=scaled(quantity),p=scaled(price);
    return q===null||p===null?null:money((q*p+5000000000n)/10000000000n);
  }
  function metric(label,value,hint){return el('div.card.ff-metric',{},[el('span.ff-muted',{text:label}),el('strong',{text:value}),el('span.ff-muted',{text:hint||'人民币 / 元'})]);}
  function facts(facts){
    const labels={original:'原始说明',memo:'用途',amount:'金额',direction:'业务方向',trade_time:'交易时间',counterparty:'往来单位',source_row:'原文件行号',row:'行号',filename:'文件名',has_invoice:'发票情况'};
    const fragment=el('div.ff-facts');
    if(facts.filename)fragment.append(el('p.ff-muted',{text:'来源文件：'+facts.filename}));
    function show(item){
      if(item.fields){Object.keys(item.fields).forEach(k=>fragment.append(el('p',{},[el('span.ff-muted',{text:(labels[k]||k)+'：'}),document.createTextNode(String(item.fields[k]||'—'))])));return;}
      Object.keys(labels).forEach(k=>{if(item[k]!=null&&item[k]!=='')fragment.append(el('p',{},[el('span.ff-muted',{text:labels[k]+'：'}),document.createTextNode(k==='direction'?({expense:'支出',income:'收入'}[item[k]]||'待核对'):typeof item[k]==='boolean'?(item[k]?'是':'否'):String(item[k]))]));});
    }
    if(facts.original)show({original:facts.original});
    if(facts.item)show(facts.item);
    (facts.items||[]).forEach(show);
    if(!fragment.children.length)fragment.append(el('p',{text:'业务资料已保存，请核对原始依据后制证。'}));
    return fragment;
  }
  window.FFAccounting={el:el,field:field,select:select,check:check,button:button,request:request,page:page,card:card,empty:empty,
    table:table,actor:actor,time:time,events:events,date:date,context:context,minor:minor,money:money,
    quantityAmount:quantityAmount,metric:metric,facts:facts,states:states,kinds:kinds};
})();
