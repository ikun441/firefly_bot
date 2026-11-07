/* Professional journal worksheet: summary, account, debit and credit. */
(function(){
  'use strict';
  const A=window.FFAccounting,el=A.el;
  window.FFViews.accounting={render:async function(root){
    const p=A.page(root,'会计工作台','录入、审核与查询凭证，所有金额以已过账分录为准。');
    const context=await A.context();let book=context.book,chart=[],categories=[],base='',currentEditor=null;
    const content=el('div.ff-stack'),tabs=el('div.ff-tabs',{role:'tablist','aria-label':'会计工作台功能'});
    const tabButtons={};
    function tab(name,fn){const b=A.button(name,()=>p.run(async()=>{active(name);await fn();}));b.setAttribute('role','tab');tabButtons[name]=b;tabs.append(b);}
    function active(name){root.classList.remove('ff-editing');Object.keys(tabButtons).forEach(k=>tabButtons[k].setAttribute('aria-selected',String(k===name)));currentEditor=null;}
    async function refreshData(){chart=await A.request(base+'/chart-accounts');categories=await A.request(base+'/expense-categories');}
    if(!book){p.body.append(A.card('',null,[A.empty('先创建一个账套','创建账套后，即可在这里录入期初和日常凭证。',el('a.btn.btn-primary',{href:'#ledger',text:'创建会计账套'}))]));return;}
    base='/books/'+book.id;await refreshData();
    const switcher=A.select('当前账套',context.books.map(b=>({value:b.id,text:b.name})),book.id);
    switcher.node.classList.add('ff-book-switch');
    switcher.input.addEventListener('change',()=>p.run(async()=>{await A.request('/books/'+switcher.input.value+'/activate',{});window.FF.clear(root);await window.FFViews.accounting.render(root);}));
    p.actions.append(switcher.node,A.button('新增凭证',()=>p.run(()=>editor(null,false)),true));
    if(!book.opening_finalized){
      p.body.append(el('section.ff-banner',{},[el('div',{},[el('strong',{text:'期初余额尚未核定'}),el('p.ff-muted',{text:'可先保存日常凭证草稿；核定期初后才能正式过账。'})]),
        el('div.ff-actions',{},[A.button('录入期初',()=>p.run(()=>editor(null,true))),A.button('确认全零期初',()=>p.run(async()=>{
          if(!confirm('确认所有科目期初为零？核定后不能修改期初。'))return;
          book=await A.request(base+'/opening/zero',{confirm:'CONFIRM_ALL_ZERO'});window.FF.clear(root);await window.FFViews.accounting.render(root);
        }))])]));
    }
    p.body.append(tabs,content);
    tab('凭证查询',showVouchers);tab('业务草稿',showBusiness);tab('科目余额',showBalances);tab('财务报表',showReports);tab('资金对账',showReconcile);tab('期末结账',showClose);
    async function showVouchers(){
      root.classList.remove('ff-editing');window.FF.clear(content);currentEditor=null;
      const start=A.field('开始日期','date',book.starts_on),end=A.field('结束日期','date',A.date()),list=el('div');let offset=0;
      content.append(A.card('凭证查询','按会计日期筛选，可查看草稿、待审核和已过账凭证。',[el('div.ff-filter',{},[start.node,end.node,A.button('查询',()=>p.run(async()=>{offset=0;await load();})),A.button('新增凭证',()=>p.run(()=>editor(null,false)),true)]),list]));
      async function load(){
        const qs=new URLSearchParams({start:start.input.value,end:end.input.value,offset:offset,limit:20});
        const response=await window.FFApi.api.raw('/v2'+base+'/vouchers?'+qs);
        const rows=response.data;window.FF.clear(list);
        if(!rows.length){list.append(A.empty('当前期间暂无凭证','点击“新增凭证”开始录入，保存后可以在此查询。',A.button('新增凭证',()=>p.run(()=>editor(null,false)),true)));return;}
        list.append(A.table(['会计日期','凭证号','摘要','状态','制单人','操作'],rows.map(v=>[
          v.accounting_date,v.number||'未过账',v.summary,el('span.ff-chip',{'data-state':v.state,text:A.states[v.state]}),A.actor(v.created_by),
          A.button('查看凭证',()=>p.run(async()=>showDetail(await A.request(base+'/vouchers/'+v.id))))
        ])));
        const prev=A.button('上一页',()=>p.run(async()=>{offset-=20;await load();})),next=A.button('下一页',()=>p.run(async()=>{offset+=20;await load();}));
        prev.disabled=offset===0;next.disabled=offset+rows.length>=response.total;
        list.append(el('div.ff-pagination',{},[el('span.ff-muted',{text:'共 '+response.total+' 张'}),prev,next]));
      }
      await load();
    }
    async function editor(v,opening){
      await refreshData();active('凭证查询');root.classList.add('ff-editing');window.FF.clear(content);
      const entries=[],form=el('form.ff-voucher'),saveKey=crypto.randomUUID();let saving=false;
      const day=A.field('凭证日期','date',v?v.accounting_date:A.date()<book.starts_on?book.starts_on:A.date(),{required:true});
      const openingBox=A.check('期初凭证',v?v.kind==='opening':opening);openingBox.input.disabled=!!v||book.opening_finalized;
      const title=el('h2',{text:'记 账 凭 证'}),notice=el('p.ff-voucher-error',{role:'alert','aria-live':'assertive'}),tbody=el('tbody'),totalDebit=el('strong'),totalCredit=el('strong'),balance=el('span'),lineCount=el('span');
      const saveTop=A.button('保存凭证',()=>save(false),true),saveBottom=A.button('保存凭证',()=>save(false),true),saveNext=A.button('保存并新增',()=>save(true));
      form.append(el('div.ff-voucher-toolbar',{},[el('div.ff-actions',{},[A.button('← 凭证查询',()=>p.run(showVouchers)),el('span.ff-chip',{text:v?'编辑草稿':'新增凭证'})]),
        el('div.ff-actions',{},[saveNext,saveTop])]),el('div.ff-voucher-title',{},[title,el('p',{text:book.name})]),
        el('div.ff-voucher-meta',{},[el('div.ff-actions',{},[el('span',{text:'凭证字：记'}),el('span.ff-muted',{text:v?'草稿编号：'+v.id:'凭证号在过账后自动生成'})]),day.node,openingBox.node]),notice,
        el('div.ff-journal-scroll',{},[el('table.ff-journal',{},[
          el('colgroup',{},[el('col',{style:{width:'5%'}}),el('col',{style:{width:'26%'}}),el('col',{style:{width:'35%'}}),el('col',{style:{width:'17%'}}),el('col',{style:{width:'17%'}})]),
          el('thead',{},[el('tr',{},['','摘要','会计科目','借方金额（元）','贷方金额（元）'].map(x=>el('th',{scope:'col',text:x})))]),tbody,
          el('tfoot',{},[el('tr',{},[el('td',{colSpan:3},[el('strong',{text:'合计'}),lineCount]),el('td',{},[totalDebit]),el('td',{},[totalCredit])])])
        ])]),
        el('div.ff-voucher-after',{},[A.button('＋ 增加分录',()=>add(null)),el('span.ff-muted',{text:'选择末级科目；金额输入“=”可自动找平。'})]),
        el('div.ff-save-bar',{},[el('div',{},[balance,el('p.ff-muted',{text:'保存为草稿，提交并审核过账后才影响正式账务。'})]),el('div.ff-actions',{},[A.button('取消',()=>p.run(showVouchers)),saveBottom])]));
      form.addEventListener('submit',e=>{e.preventDefault();save(false);});
      content.append(form);
      currentEditor=form;form.scrollIntoView({block:'start'});
      function realEntries(){return entries.filter(x=>x.row.isConnected&&(x.account.input.value.trim()||x.summary.value.trim()||x.debit.value||x.credit.value));}
      function calculate(){
        let debit=0n,credit=0n,invalid=false;
        realEntries().forEach(x=>{const d=x.debit.value?A.minor(x.debit.value):0n,c=x.credit.value?A.minor(x.credit.value):0n;if(d===null||c===null)invalid=true;else{debit+=d;credit+=c;}});
        totalDebit.textContent=A.money(debit);totalCredit.textContent=A.money(credit);
        lineCount.textContent=' / '+realEntries().length+' 条分录';
        balance.textContent=invalid?'金额格式有误':debit===credit&&debit>0n?'借贷平衡':'借贷差额：'+A.money(debit-credit)+' 元';
        balance.className=debit===credit&&debit>0n&&!invalid?'ff-balanced':'ff-unbalanced';
      }
      function chosen(entry){const text=entry.account.input.value.trim();return chart.find(a=>a.is_leaf&&a.enabled&&(a.code===text||a.code+' '+a.name===text));}
      function categoryOptions(entry){
        const old=entry.category.input.value;
        window.FF.clear(entry.category.input);
        entry.category.input.append(el('option',{value:'',text:categories.some(c=>c.enabled)?'请选择预算费用类别':'暂无费用类别'}));
        categories.filter(c=>c.enabled).forEach(c=>entry.category.input.append(el('option',{value:c.id,text:c.name})));
        entry.category.input.value=old;
      }
      function updateDimension(entry){
        const a=chosen(entry);
        if(a&&!entry.summary.value.trim()){const carried=entries.map(x=>x.summary.value.trim()).find(Boolean);if(carried)entry.summary.value=carried;}
        entry.extra.hidden=!a;
        entry.expense.hidden=!a||a.kind!=='expense'||openingBox.input.checked;
        entry.quantityBox.hidden=!a||!a.quantity_enabled;
        entry.flow.node.hidden=!a||!a.cash;
        entry.category.input.required=!!a&&a.kind==='expense'&&!openingBox.input.checked&&categories.some(c=>c.enabled);
        entry.unit.value=a&&a.quantity_enabled?(entry.originalUnit||a.unit):'';
        entry.debit.readOnly=entry.credit.readOnly=!!a&&a.quantity_enabled;
        calculateQuantity(entry);calculate();
      }
      function calculateQuantity(entry){
        const a=chosen(entry);
        if(!a||!a.quantity_enabled)return;
        const amount=A.quantityAmount(entry.quantity.value,entry.price.value);
        if(amount!==null){entry[entry.quantitySide].value=amount;entry[entry.quantitySide==='debit'?'credit':'debit'].value='';}
      }
      function add(line){
        const row=el('tr'),number=entries.filter(x=>x.row.isConnected).length+1;
        const summary=el('textarea.ff-journal-summary',{'aria-label':'第'+number+'行摘要',rows:2,placeholder:number===1?'请输入凭证摘要':'同上一行摘要',value:line?(line.summary||v.summary):''});
        const account=A.field('会计科目','text','',{placeholder:'输入科目编码或名称','aria-label':'第'+number+'行会计科目',autocomplete:'off'});
        const datalist=el('datalist',{id:'ff-accounts-'+crypto.randomUUID()});
        chart.filter(a=>a.is_leaf&&a.enabled).forEach(a=>datalist.append(el('option',{value:a.code+' '+a.name})));
        account.input.setAttribute('list',datalist.id);account.node.querySelector('.ff-label').hidden=true;
        if(line){const a=chart.find(a=>a.id===line.account_id);account.input.value=a?a.code+' '+a.name:'';}
        const debit=el('input.ff-journal-money',{'aria-label':'第'+number+'行借方金额',inputMode:'decimal',placeholder:'0.00',value:line&&line.side==='debit'?line.amount:''});
        const credit=el('input.ff-journal-money',{'aria-label':'第'+number+'行贷方金额',inputMode:'decimal',placeholder:'0.00',value:line&&line.side==='credit'?line.amount:''});
        const category=A.select('预算费用类别',[{value:'',text:'请选择预算费用类别'}],'');
        const aux=A.field('往来单位 / 辅助说明','text',line?line.auxiliary:'',{placeholder:'客户、供应商、员工等','aria-label':'第'+number+'行往来单位'});
        const flow=A.select('现金流项目',[{value:'',text:'请选择现金流项目'},{value:'operating',text:'经营活动'},{value:'investing',text:'投资活动'},{value:'financing',text:'筹资活动'},{value:'internal',text:'内部转账'}],line?line.cashflow:'');
        const quantity=A.field('数量','text',line?line.quantity||'':'',{inputMode:'decimal'}),price=A.field('单价（元）','text',line?line.unit_price||'':'',{inputMode:'decimal'}),unit=A.field('单位','text',line?line.unit:'',{readOnly:true});
        const quantityBox=el('div.ff-quantity-fields',{},[quantity.node,price.node,unit.node]);
        const expense=el('div.ff-expense-selection',{},[category.node,A.button('＋ 添加类别',()=>p.run(async()=>{
          const name=prompt('请输入预算费用类别名称');if(!name||!name.trim())return;
          const created=await A.request(base+'/expense-categories',{name:name.trim()});
          categories=await A.request(base+'/expense-categories');
          entries.forEach(categoryOptions);category.input.value=String(created.id);p.success('已添加费用类别');})),el('span.ff-muted',{text:'费用过账后计入该类别预算执行。'})]);
        const extra=el('div.ff-journal-extra',{},[expense,quantityBox,el('div.ff-line-details',{},[aux.node,flow.node])]);
        const entry={row:row,summary:summary,account:account,debit:debit,credit:credit,category:category,aux:aux.input,flow:flow,
          quantity:quantity.input,price:price.input,unit:unit.input,quantityBox:quantityBox,expense:expense,extra:extra,quantitySide:line?line.side:'debit',originalUnit:line?line.unit:''};
        const remove=A.button('×',()=>{row.remove();calculate();});remove.setAttribute('aria-label','删除第'+number+'条分录');
        row.append(el('td.ff-journal-index',{},[el('span',{text:number}),remove]),el('td',{},[summary]),el('td',{},[account.node,datalist,extra]),el('td.ff-money-cell',{},[debit]),el('td.ff-money-cell',{},[credit]));
        tbody.append(row);entries.push(entry);categoryOptions(entry);
        if(line)category.input.value=line.budget_category_id||'';
        account.input.addEventListener('change',()=>updateDimension(entry));
        summary.addEventListener('blur',calculate);
        [debit,credit].forEach((input,index)=>{
          const side=index?'credit':'debit',other=index?debit:credit;
          input.addEventListener('focus',()=>{entry.quantitySide=side;if(chosen(entry)&&chosen(entry).quantity_enabled){calculateQuantity(entry);calculate();}});
          input.addEventListener('input',()=>{if(input.value)other.value='';calculate();});
          input.addEventListener('keydown',e=>{if(e.key==='='){e.preventDefault();if(input.readOnly)return;
            let net=0n;realEntries().filter(x=>x!==entry).forEach(x=>{net+=(A.minor(x.debit.value||'0')||0n)-(A.minor(x.credit.value||'0')||0n);});
            debit.value=net<0n?A.money(-net):'';credit.value=net>0n?A.money(net):'';calculate();}});
        });
        [quantity.input,price.input].forEach(x=>x.addEventListener('input',()=>{calculateQuantity(entry);calculate();}));
        updateDimension(entry);calculate();
      }
      openingBox.input.addEventListener('change',()=>entries.forEach(updateDimension));
      if(v)v.lines.forEach(add);else for(let index=0;index<4;index++)add(null);
      async function save(next){
        if(saving)return;
        notice.textContent='';
        await p.run(async()=>{
          if(!day.input.value){notice.textContent='请选择凭证日期';day.input.focus();return;}
          const rows=realEntries(),summary=rows.map(x=>x.summary.value.trim()).find(Boolean);
          if(!summary){notice.textContent='请填写凭证摘要';if(entries[0])entries[0].summary.focus();return;}
          if(rows.length<2){notice.textContent='凭证至少需要两条分录';return;}
          const lines=rows.map((x,index)=>{
            const a=chosen(x),d=A.minor(x.debit.value||'0'),c=A.minor(x.credit.value||'0');
            if(!a)throw new Error('第'+(index+1)+'行：请选择有效的末级科目');
            if(d===null||c===null||((d>0n)===(c>0n)))throw new Error('第'+(index+1)+'行：借方或贷方需填写一个有效金额');
            if(a.kind==='expense'&&!openingBox.input.checked&&categories.some(k=>k.enabled)&&!x.category.input.value)throw new Error('第'+(index+1)+'行：请选择预算费用类别');
            if(a.quantity_enabled&&(!x.quantity.value||!x.price.value))throw new Error('第'+(index+1)+'行：请填写数量和单价');
            return {account_id:a.id,summary:x.summary.value.trim()||summary,side:d>0n?'debit':'credit',amount:d>0n?x.debit.value:x.credit.value,
              auxiliary:x.aux.value,cashflow:a.cash?x.flow.input.value:'',budget_category_id:a.kind==='expense'&&x.category.input.value?Number(x.category.input.value):null,
              quantity:a.quantity_enabled?x.quantity.value:null,unit_price:a.quantity_enabled?x.price.value:null,unit:a.quantity_enabled?x.unit.value:''};
          });
          const net=lines.reduce((sum,line)=>sum+(line.side==='debit'?1n:-1n)*A.minor(line.amount),0n);
          if(net!==0n){notice.textContent='借贷尚未平衡，差额 '+A.money(net)+' 元';return;}
          const payload={accounting_date:day.input.value,summary:summary,lines:lines};
          if(v)payload.expected_version=v.version;else payload.opening=openingBox.input.checked;
          saving=true;saveTop.disabled=saveBottom.disabled=saveNext.disabled=true;
          try{
            const saved=await A.request(base+'/vouchers'+(v?'/'+v.id:''),payload,v?'PATCH':'POST',saveKey);
            if(next)await editor(null,false);else await showDetail(saved);
            p.success('凭证已保存为草稿，尚未过账');
          }finally{saving=false;saveTop.disabled=saveBottom.disabled=saveNext.disabled=false;}
        });
      }
    }
    async function showDetail(v){
      root.classList.remove('ff-editing');window.FF.clear(content);currentEditor=null;
      const header=el('div.ff-voucher-toolbar',{},[A.button('← 凭证查询',()=>p.run(showVouchers)),el('div.ff-actions')]);
      const actions=header.lastChild,card=A.card('',null,[header,el('div.ff-voucher-title',{},[el('h2',{text:'记 账 凭 证'}),el('p',{text:book.name})]),
        el('div.ff-voucher-meta',{},[el('strong',{text:v.number||'草稿编号：'+v.id}),el('span',{text:'日期：'+v.accounting_date}),el('span.ff-chip',{'data-state':v.state,text:A.states[v.state]})]),
        A.table(['摘要','会计科目','预算类别 / 数量','借方金额','贷方金额'],v.lines.map(line=>{
          const a=chart.find(x=>x.id===line.account_id),category=categories.find(x=>x.id===line.budget_category_id);
          return [line.summary||v.summary,a?a.code+' '+a.name:'科目已停用',
            [category?category.name:'',line.quantity?line.quantity+' '+line.unit+' × '+line.unit_price+' 元':''].filter(Boolean).join(' / ')||'—',
            line.side==='debit'?(line.effect_sign===-1?'-':'')+line.amount:'—',line.side==='credit'?(line.effect_sign===-1?'-':'')+line.amount:'—'];
        })),
        el('div.ff-voucher-signatures',{},[['制单',v.created_by],['审核',v.approved_by],['过账',v.posted_by]].map(pair=>el('span',{text:pair[0]+'：'+(pair[1]&&pair[1].key?A.actor(pair[1]):'尚未操作')})))]);
      content.append(card);
      if(v.state==='draft'&&['ordinary','opening'].includes(v.kind))actions.append(A.button('编辑凭证',()=>p.run(()=>editor(v,false))));
      const transitions={draft:[['submit','提交审核']],submitted:[['approve','审核通过'],['reject','驳回'],['withdraw','撤回']],approved:[['post','过账'],['revoke-approval','撤销审核']]}[v.state]||[];
      transitions.forEach(pair=>actions.append(A.button(pair[1],()=>p.run(async()=>{
        const reason=pair[0]==='reject'?prompt('请填写驳回原因'):'';if(reason===null)return;
        const updated=await A.request(base+'/vouchers/'+v.id+'/'+pair[0],{expected_version:v.version,reason:reason||''});await showDetail(updated);p.success(pair[1]+'成功');
      }),pair[0]==='submit'||pair[0]==='post')));
      if(!['posted','void'].includes(v.state))actions.append(A.button('作废',()=>p.run(async()=>{const reason=prompt('请填写作废原因');if(!reason||!reason.trim())return;
        await showDetail(await A.request(base+'/vouchers/'+v.id+'/void',{expected_version:v.version,reason:reason}));p.success('凭证已作废');})));
      if(v.state==='posted'&&v.kind==='ordinary')actions.append(A.button('全额冲销',()=>p.run(async()=>{
        const reason=prompt('请填写冲销原因');if(!reason||!reason.trim())return;const date=prompt('冲销会计日期',A.date());if(!date)return;
        await showDetail(await A.request(base+'/vouchers/'+v.id+'/reversal-drafts',{accounting_date:date,reason:reason}));p.success('冲销草稿已生成，仍需审核过账');})));
    }
    async function showBusiness(){
      window.FF.clear(content);const text=A.field('业务说明','text','',{placeholder:'请填写经济事项及原始依据'}),list=el('div.ff-stack');
      const file=el('input.input',{type:'file',accept:'.csv,.xlsx','aria-label':'业务资料文件'});
      content.append(A.card('业务资料','确认业务后需核对科目制证，保存资料不会自动过账。',[
        el('div.ff-filter',{},[text.node,A.button('保存业务草稿',()=>p.run(async()=>{if(!text.input.value.trim())throw new Error('请填写业务说明');
          await A.request(base+'/business-drafts',{source_key:'web:'+crypto.randomUUID(),facts:{original:text.input.value,source:'web'}});await load();text.input.value='';p.success('业务草稿已保存');}),true)]),
        el('div.ff-actions',{style:{marginTop:'24px'}},[file,A.button('导入 CSV / Excel',()=>p.run(async()=>{
          if(!file.files.length)throw new Error('请先选择文件');const form=new FormData();form.append('file',file.files[0]);await A.request(base+'/imports',form);await load();p.success('业务资料已导入，尚未过账');
        }))])]),list);
      async function load(){const rows=await A.request(base+'/business-drafts');window.FF.clear(list);
        if(!rows.length){list.append(A.card('',null,[A.empty('暂无待制证业务','可输入业务说明，也可导入 CSV 或 Excel。')]));return;}
        rows.forEach(d=>{const actions=el('div.ff-actions');
          if(d.state==='pending')actions.append(A.button('根据资料新建凭证',()=>p.run(()=>editor(null,false)),true),
            A.button('关联凭证',()=>p.run(async()=>{const id=prompt('请输入已创建的凭证编号');if(!id)return;await A.request(base+'/business-drafts/'+d.id+'/resolve',{voucher_id:Number(id)});await load();p.success('业务资料已关联');})),
            A.button('作废资料',()=>p.run(async()=>{if(!confirm('确认作废该业务草稿？'))return;await A.request(base+'/business-drafts/'+d.id+'/resolve',{void:true});await load();})));
          list.append(A.card('业务资料 '+d.id,A.states[d.state]+' · '+A.actor(d.actor),[A.facts(d.facts),actions]));});}
      await load();
    }
    async function showBalances(){
      window.FF.clear(content);const data=await A.request(base+'/trial-balance');
      content.append(el('div.ff-metrics',{},[A.metric('借方累计',data.debit),A.metric('贷方累计',data.credit),A.metric('试算状态',data.balanced?'借贷平衡':'需要核对','以已过账凭证为准')]),
        A.card('科目余额','上级科目的历史直记余额与末级科目分别列示，不重复累计。',[A.table(['科目','类别','借方累计','贷方累计','借贷净额','数量余额'],data.rows.map(r=>[
          r.code+' '+r.name,A.kinds[r.kind],r.debit,r.credit,r.balance,r.quantity_enabled?(r.quantity_balance||'0')+' '+r.unit:'—'
        ]))]));
    }
    async function showReports(){
      window.FF.clear(content);const start=A.field('开始日期','date',book.starts_on),end=A.field('结束日期','date',A.date()),output=el('div.ff-stack');
      content.append(el('div.ff-filter',{},[start.node,end.node,A.button('生成报表',()=>p.run(load),true)]),output);
      async function load(){
        const data=await A.request(base+'/reports?start='+start.input.value+'&end='+end.input.value);window.FF.clear(output);
        output.append(el('div.ff-metrics',{},[A.metric('资产总额',data.balance_sheet.assets),A.metric('负债总额',data.balance_sheet.liabilities),A.metric('所有者权益',data.balance_sheet.equity)]),
          A.card('本期经营结果',start.input.value+' 至 '+end.input.value+' · 仅已过账',[A.table(['本期收入','本期费用','本期利润'],[[data.income_statement.income,data.income_statement.expense,data.income_statement.profit]])]),
          A.card('现金流量',data.cash_flow.complete?'资金变动已完成分类':'现金流分类尚不完整',[
            data.cash_flow.complete?A.table(['经营活动','投资活动','筹资活动','现金净变动'],[[data.cash_flow.items.operating,data.cash_flow.items.investing,data.cash_flow.items.financing,data.cash_flow.cash_change]]):
              el('p',{text:'请核对凭证中的现金流项目。待补充凭证：'+data.cash_flow.unclassified_voucher_ids.join('、')}),
            el('p.ff-muted',{text:data.notice})]));
      }
      await load();
    }
    async function showReconcile(){
      window.FF.clear(content);
      const account=A.select('资金科目',chart.filter(a=>a.cash&&a.is_leaf).map(a=>({value:a.id,text:a.code+' '+a.name})));
      const day=A.field('对账日期','date',A.date(),{required:true}),amount=A.field('对账单 / 盘点余额','text','',{inputMode:'decimal',required:true}),reason=A.field('核对依据与说明','text','',{required:true});
      const rows=el('div.ff-stack'),items=[],form=el('form.ff-stack',{},[el('div.ff-form-grid',{},[account.node,day.node,amount.node,reason.node]),rows]);
      form.append(el('div.ff-actions',{},[A.button('添加未达项',()=>{
        const side=A.select('调节归属',[{value:'bank',text:'银行侧'},{value:'book',text:'账内侧'}]),value=A.field('有符号调节金额','text','',{inputMode:'decimal'}),memo=A.field('未达项说明','text','');
        const node=el('div.ff-form-grid',{},[side.node,value.node,memo.node]);node.append(A.button('移除此项',()=>node.remove()));rows.append(node);items.push({node:node,side:side,value:value,memo:memo});
      }),el('button.btn.btn-primary',{type:'submit',text:'确认对账'})]));
      form.addEventListener('submit',e=>{e.preventDefault();p.run(async()=>{
        await A.request(base+'/reconciliations',{account_id:Number(account.input.value),as_of:day.input.value,statement_balance:amount.input.value,reason:reason.input.value,
          outstanding:items.filter(x=>x.node.isConnected).map(x=>({side:x.side.input.value,amount:x.value.input.value,reason:x.memo.input.value}))});p.success('对账已记录，新增相关过账后需重新核对');
      });});
      content.append(A.card('资金对账','银行余额＋银行调节＝账内余额＋账内调节。对账不会自动生成凭证。',[form]));
    }
    async function showClose(){
      window.FF.clear(content);const month=A.field('会计期间','month',A.date().slice(0,7)),result=el('div');
      content.append(A.card('期末结账','按顺序完成对账、损益结转与预检。关闭后不能向该期间补记。',[month.node,
        el('div.ff-actions',{style:{marginTop:'24px'}},[
          A.button('检查结账条件',()=>p.run(async()=>{const data=await A.request(base+'/periods/'+month.input.value+'/pre-close',{});window.FF.clear(result);result.append(data.issues.length?el('ul',{},data.issues.map(x=>el('li',{text:x}))):el('p.ff-balanced',{text:'全部检查通过，可以结账'}));})),
          A.button('生成损益结转',()=>closing(false)),A.button('生成年度结转',()=>closing(true)),
          A.button('关闭本期间',()=>p.run(async()=>{if(!confirm('确认关闭 '+month.input.value+'？关闭后不提供反结账。'))return;await A.request(base+'/periods/'+month.input.value+'/close',{});p.success('期间已关闭，报表快照已保存');}),true)
        ]),result]));
      async function closing(annual){await p.run(async()=>{const data=await A.request(base+'/periods/'+month.input.value+'/closing-drafts'+(annual?'?annual=true':''),{});if(data.no_entry)p.success(data.message);else await showDetail(data);});}
    }
    active('凭证查询');await p.run(showVouchers);
  }};
})();
