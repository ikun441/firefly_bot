/* Books, chart of accounts, expense budgets and the unified operation feed. */
(function(){
  'use strict';
  const A=window.FFAccounting,el=A.el,Views=window.FFViews;

  Views.books={render:async function(root){
    const p=A.page(root,'账套管理','管理会计主体与启用日期。每个账套独立核算，凭证和预算互不混用。');
    const formBox=el('div'),listBox=el('div');p.body.append(formBox,listBox);
    p.actions.append(A.button('新建账套',()=>edit(null),true));
    async function edit(book){
      window.FF.clear(formBox);
      const detail=book?await A.request('/books/'+book.id):null;
      const name=A.field('账套名称','text',detail?detail.name:'',{required:true,maxLength:100,placeholder:'例如：梅穿库子工作室'});
      const start=A.field('启用日期（月初）','date',detail?detail.starts_on:A.date().slice(0,7)+'-01',{required:true});
      if(detail&&(detail.voucher_count||detail.opening_finalized))start.input.disabled=true;
      const form=el('form.ff-form-grid',{},[name.node,start.node]);
      const actions=el('div.ff-actions.ff-span-all');
      const save=el('button.btn.btn-primary',{type:'submit',text:detail?'保存账套信息':'创建账套'});
      actions.append(save,A.button('取消',()=>window.FF.clear(formBox)));form.append(actions);
      form.addEventListener('submit',event=>{event.preventDefault();p.run(async()=>{
        save.disabled=true;
        try{
          await A.request('/books'+(detail?'/'+detail.id:''),detail?{name:name.input.value,starts_on:start.input.value,expected_version:detail.version}:{name:name.input.value,starts_on:start.input.value},detail?'PATCH':'POST');
          window.FF.clear(formBox);await draw();p.success(detail?'账套信息已保存':'账套已创建，并已设为当前账套');
        }finally{save.disabled=false;}
      });});
      formBox.append(A.card(detail?'编辑账套':'创建新账套','本位币为人民币；已有凭证或核定期初后，启用日期锁定。',[form]));
      name.input.focus();formBox.scrollIntoView({behavior:'smooth',block:'start'});
    }
    async function draw(){
      const books=await A.request('/books?include_archived=true');window.FF.clear(listBox);
      if(!books.length){listBox.append(A.card('',null,[A.empty('还没有账套','创建第一个账套后，即可录入期初、凭证和预算。',A.button('创建账套',()=>edit(null),true))]));return;}
      listBox.append(A.card('全部账套','删除会将账套归档，保留全部凭证和操作记录，可随时恢复。',[
        el('div.ff-stack',{},books.map(book=>{
          const actions=el('div.ff-actions');
          if(book.archived)actions.append(A.button('恢复账套',()=>p.run(async()=>{
            await A.request('/books/'+book.id,{name:book.name,starts_on:book.starts_on,expected_version:book.version,archived:false},'PATCH');await draw();p.success('账套已恢复');
          })));
          else{
            if(!book.is_current)actions.append(A.button('设为当前账套',()=>p.run(async()=>{
              await A.request('/books/'+book.id+'/activate',{});await draw();p.success('已切换到 '+book.name+'，QQ与网页使用同一当前账套');
            }),true));
            actions.append(A.button('进入工作台',()=>p.run(async()=>{await A.request('/books/'+book.id+'/activate',{});location.hash='#accounting';})),
              A.button('编辑',()=>p.run(()=>edit(book))),
              A.button('删除账套',()=>p.run(async()=>{if(!confirm('将“'+book.name+'”归档？凭证与操作记录会保留，之后可恢复。'))return;
                await A.request('/books/'+book.id,{},'DELETE');await draw();p.success('账套已归档，记录完整保留');})));
          }
          return el('article.ff-list-item',{},[
            el('div',{},[el('div.ff-list-title',{},[el('h3',{text:book.name}),
              el('span.ff-chip',{'data-tone':book.is_current?'success':'',text:book.archived?'已归档':book.is_current?'当前账套':'可用账套'})]),
              el('p.ff-muted',{text:'启用 '+book.starts_on+' · 人民币 · '+(book.opening_finalized?'期初已核定':'期初待核定')})]),actions]);
        }))
      ]));
    }
    await p.run(draw);
  }};

  Views.subjects={render:async function(root){
    const p=A.page(root,'科目管理','建立清晰的科目层级，为需要的科目开启数量、单价和单位核算。');
    const context=await A.context(),book=context.book;
    if(!book){p.body.append(A.empty('请先创建账套','科目属于具体账套。',el('a.btn.btn-primary',{href:'#ledger',text:'前往账套管理'})));return;}
    const base='/books/'+book.id;let chart=[];
    const formBox=el('div'),listBox=el('div');p.body.append(el('p.ff-muted',{text:'当前账套：'+book.name}),formBox,listBox);
    p.actions.append(A.button('新建科目',()=>edit(null,null),true));
    async function edit(account,parent){
      window.FF.clear(formBox);
      const parentField=A.select('上级科目',[{value:'',text:'无上级（一级科目）'}].concat(chart.filter(a=>a.enabled&&(!account||a.id!==account.id)).map(a=>({value:a.id,text:a.code+' '+a.name}))),parent?parent.id:account?account.parent_id||'':'');
      const code=A.field('科目编码','text',account?account.code:'',{required:true,maxLength:20,inputMode:'numeric'});
      const name=A.field('科目名称','text',account?account.name:'',{required:true,maxLength:100});
      const kind=A.select('科目类别',Object.keys(A.kinds).map(k=>({value:k,text:A.kinds[k]})),account?account.kind:'asset');
      const side=A.select('正常余额方向',[{value:'debit',text:'借方'},{value:'credit',text:'贷方'}],account?account.normal_side:'debit');
      const cash=A.check('属于现金或银行等资金科目',account&&account.cash);
      const qty=A.check('开启数量与单价核算',account&&account.quantity_enabled);
      const unit=A.field('计量单位','text',account?account.unit:'件',{maxLength:16,required:true,placeholder:'件、小时、千克…'});
      const enabled=A.check('启用此科目',account?account.enabled:true);
      function inherit(){
        const chosen=chart.find(a=>a.id===Number(parentField.input.value));
        if(chosen){kind.input.value=chosen.kind;side.input.value=chosen.normal_side;cash.input.checked=chosen.cash;
          if(!account){let index=1;while(chart.some(a=>a.code===chosen.code+String(index).padStart(2,'0')))index++;
            code.input.value=chosen.code+String(index).padStart(2,'0');}}
        kind.input.disabled=!!chosen||!!account;side.input.disabled=!!chosen||!!account;cash.input.disabled=!!chosen||!!account;
      }
      parentField.input.addEventListener('change',inherit);inherit();
      if(account){code.input.disabled=true;parentField.input.disabled=true;}
      const form=el('form.ff-form-grid',{},[parentField.node,code.node,name.node,kind.node,side.node,unit.node,cash.node,qty.node,enabled.node]);
      const actions=el('div.ff-actions.ff-span-all',{},[el('button.btn.btn-primary',{type:'submit',text:account?'保存科目':'新增科目'}),A.button('取消',()=>window.FF.clear(formBox))]);form.append(actions);
      form.addEventListener('submit',e=>{e.preventDefault();p.run(async()=>{
        const data={name:name.input.value,enabled:enabled.input.checked,quantity_enabled:qty.input.checked,unit:unit.input.value};
        if(account)data.expected_version=account.version;
        else Object.assign(data,{code:code.input.value,kind:kind.input.value,normal_side:side.input.value,cash:cash.input.checked,parent_id:parentField.input.value?Number(parentField.input.value):null});
        await A.request(base+'/chart-accounts'+(account?'/'+account.id:''),data,account?'PATCH':'POST');
        window.FF.clear(formBox);await draw();p.success(account?'科目信息已保存':'科目已新增，可在凭证中选择');
      });});
      formBox.append(A.card(account?'编辑科目':parent?'新增“'+parent.name+'”的子科目':'新增会计科目',
        '子科目继承上级类别与方向。新增子科目后，上级科目保留历史记录，新的凭证使用末级科目。',[form]));
      name.input.focus();formBox.scrollIntoView({behavior:'smooth',block:'start'});
    }
    async function draw(){
      chart=await A.request(base+'/chart-accounts');window.FF.clear(listBox);
      const search=A.field('搜索科目','search','',{placeholder:'输入编码或名称'}),results=el('div');
      function renderRows(){
        window.FF.clear(results);
        const term=search.input.value.trim(),filtered=chart.filter(a=>(a.code+' '+a.name).includes(term));
        results.append(A.table(['科目编码与名称','类别 / 方向','核算设置','状态','操作'],filtered.map(a=>{
          const parent=chart.find(x=>x.id===a.parent_id);
          const label=el('div',{},[el('strong',{text:(parent?'↳ ':'')+a.code+' '+a.name}),el('p.ff-muted',{text:parent?'上级：'+parent.name:a.is_leaf?'一级科目':'上级科目'})]);
          return [label,A.kinds[a.kind]+' / '+(a.normal_side==='debit'?'借方':'贷方'),a.quantity_enabled?'数量单价 · '+a.unit:'金额核算',
            el('span.ff-chip',{text:a.enabled?'启用':'停用'}),el('div.ff-actions',{},[
              A.button('编辑',()=>p.run(()=>edit(a,null))),A.button('添加子科目',()=>p.run(()=>edit(null,a)))])];
        })));
      }
      search.input.addEventListener('input',renderRows);
      listBox.append(A.card('会计科目','共 '+chart.length+' 个科目 · 选择末级科目记账。',[search.node,results]));renderRows();
    }
    await p.run(draw);
  }};

  Views.legacyBudget=Views.budget;
  Views.budget={render:async function(root){
    const p=A.page(root,'预算管理','按费用类别设置月度额度，实际执行以已过账费用分录为准。');
    const context=await A.context(),book=context.book;
    if(!book){p.body.append(A.empty('请先创建账套','费用类别与预算属于具体账套。',el('a.btn.btn-primary',{href:'#ledger',text:'前往账套管理'})));return;}
    const base='/books/'+book.id,month=A.field('预算月份','month',A.date().slice(0,7),{required:true}),formBox=el('div'),output=el('div.ff-stack');
    const filters=el('div.ff-filter',{},[month.node,A.button('查看预算',()=>p.run(draw))]);p.body.append(el('p.ff-muted',{text:'当前账套：'+book.name}),filters,formBox,output);
    p.actions.append(A.button('添加费用类别',()=>editCategory(null),true));
    function editCategory(category){
      window.FF.clear(formBox);
      const name=A.field('费用类别名称','text',category?category.name:'',{required:true,maxLength:64,placeholder:'例如：办公用品、差旅交通、房租物业'});
      const enabled=A.check('启用该费用类别',category?category.enabled:true),form=el('form.ff-form-grid',{},[name.node,enabled.node,
        el('div.ff-actions.ff-span-all',{},[el('button.btn.btn-primary',{type:'submit',text:category?'保存类别':'添加类别'}),A.button('取消',()=>window.FF.clear(formBox))])]);
      form.addEventListener('submit',e=>{e.preventDefault();p.run(async()=>{
        await A.request(base+'/expense-categories'+(category?'/'+category.id:''),{name:name.input.value,enabled:enabled.input.checked,expected_version:category?category.version:null},category?'PATCH':'POST');
        window.FF.clear(formBox);await draw();p.success('费用类别已保存，制证时可选择');
      });});
      formBox.append(A.card(category?'编辑费用类别':'添加预算费用类别','费用类别用于归集多种费用科目，金额只在凭证过账后计入执行。',[form]));
      name.input.focus();formBox.scrollIntoView({behavior:'smooth',block:'start'});
    }
    async function draw(){
      const data=await A.request(base+'/expense-budgets?month='+encodeURIComponent(month.input.value));window.FF.clear(output);
      output.append(el('div.ff-metrics',{},[A.metric('月度预算',data.planned),A.metric('已发生费用',data.used),A.metric('尚未归集类别的费用',data.unassigned)]));
      output.append(el('p.ff-muted',{text:data.basis+'。未归集费用请核对；已过账记录需通过冲销及新凭证更正。'}));
      if(!data.categories.length){output.append(A.card('',null,[A.empty('先添加一个费用类别','创建类别后，可设置预算额度，并在费用凭证中选择归属。',A.button('添加费用类别',()=>editCategory(null),true))]));return;}
      output.append(el('div.ff-budget-grid',{},data.categories.map(category=>{
        const amount=A.field('月度预算额度（元）','text',category.budget||'',{inputMode:'decimal',placeholder:'请输入本月额度',required:true});
        const progress=el('div.ff-progress',{'aria-hidden':'true'},[el('span',{style:{width:Math.max(0,Math.min(100,Number(category.ratio)||0))+'%'}})]);
        const form=el('form.ff-stack',{},[amount.node,el('div.ff-actions',{},[
          el('button.btn.btn-primary',{type:'submit',text:'保存额度',disabled:!category.enabled}),A.button('编辑类别',()=>editCategory(category))])]);
        form.addEventListener('submit',e=>{e.preventDefault();p.run(async()=>{
          await A.request(base+'/expense-budgets',{category_id:category.id,month:month.input.value,amount:amount.input.value},'PUT');await draw();p.success('预算额度已保存');
        });});
        return A.card(category.name,(category.enabled?category.state:'已停用')+' · '+month.input.value,[
          el('div.ff-actions',{},[el('strong',{text:'已用 '+category.used+' 元'}),el('span.ff-muted',{text:category.remaining==null?'尚未设置额度':'剩余 '+category.remaining+' 元'})]),progress,form]);
      })));
    }
    await p.run(draw);
  }};

  Views.operations={render:async function(root){
    const p=A.page(root,'操作日志','统一追溯账套、凭证、预算、科目、权限与登录操作。');
    const start=A.field('开始日期','date',''),end=A.field('结束日期','date',''),output=el('div');let offset=0;
    p.body.append(el('div.ff-filter',{},[start.node,end.node,A.button('查询记录',()=>p.run(async()=>{offset=0;await draw();}),true)]),output);
    async function draw(){
      const params=new URLSearchParams({offset:offset,limit:30});if(start.input.value)params.set('start',start.input.value);if(end.input.value)params.set('end',end.input.value);
      const data=await A.request('/operation-events?'+params);window.FF.clear(output);
      output.append(A.card('全部操作','共 '+data.total+' 条记录 · 按最新时间排序',[A.events(data.items)]));
      const prev=A.button('上一页',()=>p.run(async()=>{offset=Math.max(0,offset-30);await draw();})),next=A.button('下一页',()=>p.run(async()=>{offset+=30;await draw();}));
      prev.disabled=offset===0;next.disabled=offset+data.items.length>=data.total;
      output.append(el('div.ff-pagination',{},[prev,el('span.ff-muted',{text:'第 '+(offset/30+1)+' 页'}),next]));
    }
    await p.run(draw);
  }};
})();
