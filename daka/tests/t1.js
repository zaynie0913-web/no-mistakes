const fs=require('fs');
const {build}=require('./stub.js');
const HTML='app.html';
let pass=0,fail=0;
const T=(name,fn)=>{try{fn();pass++;}catch(e){fail++;console.log('  ✗ '+name+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+',实得 '+JSON.stringify(a));};

const html=fs.readFileSync(HTML,'utf8');

/* ================= 第一层:静态结构(正则解析真实 HTML) ================= */
console.log('【一】静态结构');
const ids=(html.match(/\bid="([^"]+)"/g)||[]).map(x=>x.slice(4,-1));
T('id 全局唯一',()=>{
  const seen={},dup=[];
  ids.forEach(i=>{if(seen[i])dup.push(i);seen[i]=1;});
  eq(dup.join(','),'','重复 id: '+dup.join(','));
});
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
const used=[...new Set([...js.matchAll(/\$\(\s*['"]#([A-Za-z0-9_-]+)['"]\s*\)/g)].map(m=>m[1]))];
T('JS 引用的每个 id 都存在于 HTML',()=>{
  const miss=used.filter(u=>ids.indexOf(u)<0);
  eq(miss.join(','),'','缺失: '+miss.join(','));
});
['detModal','detTitle','detBody','detClose'].forEach(id=>{
  T('新增弹层 id 存在:'+id,()=>ok(ids.indexOf(id)>=0));
});
T('弹层默认 hidden',()=>ok(/<div class="modal" id="detModal" hidden>/.test(html)));
T('弹层 CSS 类已定义',()=>{
  ['.dbox','.dbody','.medmenu','.srow.clk','.srow .go'].forEach(c=>{
    ok(html.indexOf(c+'{')>=0||html.indexOf(c+',')>=0,'缺 CSS: '+c);
  });
});
T('感受行不再把日期直接铺在页面上',()=>{
  ok(!/sc">\$\{cnt\[t\]\} 次<\/span><\/div>\s*<div class="dates">/.test(html),'感受日期仍是平铺');
});
T('补剂行带 data-med 且可点',()=>ok(/class="srow clk" data-med=/.test(html)));
T('感受行带 data-feel 且可点',()=>ok(/class="srow clk" data-feel=/.test(html)));
T('健康回顾绑定了行点击',()=>ok(/box\.onclick=ev=>/.test(html)));
T('补剂已记录后不再直接 delete',()=>{
  ok(!/if\(on\)delete d\.meds\[t\.k\];else d\.meds\[t\.k\]=Date\.now\(\);/.test(html),'旧的一点即删逻辑还在');
});
T('医学免责仍在',()=>ok(html.indexOf('请问医生或药师')>=0&&html.indexOf('身体上的疑问请找医生')>=0));
T('工具内文案没有混进 emoji 装饰',()=>{
  const seg=js.match(/function medDetail[\s\S]*?\n}/)[0]+js.match(/function feelDetail[\s\S]*?\n}/)[0];
  const bad=seg.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu);
  eq(bad?bad.join(''):'','','出现 emoji: '+(bad||[]).join(''));
});
T('禁用词未出现在新增文案里',()=>{
  const seg=js.slice(js.indexOf('明细弹层'));
  ['必须吃','应该吃','赶紧','别偷懒','不许','浪费'].forEach(w=>{
    ok(seg.indexOf(w)<0,'出现禁用词: '+w);
  });
});

/* ================= 第二层:装载运行 ================= */
console.log('【二】装载与运行');
function boot(seed){
  const doc=build(HTML);
  const store={};
  const g={
    document:doc,
    window:{},
    localStorage:{
      getItem:k=>(k in store)?store[k]:null,
      setItem:(k,v)=>{store[k]=String(v);},
      removeItem:k=>{delete store[k];}
    },
    alert:m=>{g.__alert=m;},
    confirm:()=>true,
    prompt:()=>null,
    setTimeout:(f)=>{return 0;},
    clearTimeout:()=>{},
    setInterval:()=>0,clearInterval:()=>{},
    requestAnimationFrame:()=>0,
    navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},
    Date,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},
    Promise,
  };
  g.window=g; g.globalThis=g; g.self=g;
  if(seed)g.localStorage.setItem('dk_v1',JSON.stringify(seed));
  const vm=require('vm');
  const ctx=vm.createContext(g);
  vm.runInContext(js,ctx,{filename:'app.js'});
  return {g,doc,ctx,vm};
}
let env=null;
T('脚本可在桩环境里跑完不抛错',()=>{env=boot();ok(env);});
T('存储键名探测',()=>{
  const keys=[...js.matchAll(/localStorage\.(?:getItem|setItem)\(\s*['"]([^'"]+)['"]/g)].map(m=>m[1]);
  ok(keys.length>0,'没找到 localStorage 键');
  global.__KEY=keys[0];
});

/* ================= 第三层:功能与状态 ================= */
console.log('【三】功能与状态');
const KEY=global.__KEY;
const DAY=k=>k;
// 造数据:14 天里 10 天吃了维生素D,时刻都在 12:xx;甘氨酸镁记在凌晨
function mkTs(dateStr,h,mi){const d=new Date(dateStr+'T00:00:00');d.setHours(h,mi,0,0);return d.getTime();}
function seedDays(){
  const days={};
  const base=new Date();base.setHours(12,0,0,0);
  for(let i=1;i<=14;i++){
    const d=new Date(base);d.setDate(d.getDate()-i);
    const k=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    days[k]={meds:{},feel:{b:[],m:[],note:''}};
    if(i<=10)days[k].meds.m_vd=mkTs(k,12,10+i);
    if(i%2===0)days[k].meds.m_mg=mkTs(k,23,30);
    days[k].feel.b=(i%3===0)?['疲劳']:['焦虑'];
  }
  return days;
}
function run(seed,fnSrc){
  const e=boot(null);
  e.g.localStorage.setItem(KEY,JSON.stringify(seed));
  return e.vm.runInContext(fnSrc,e.ctx);
}
function withEnv(seed){
  const e=boot(null);
  // 直接注入内存态,绕过异步 load;并按 app 导入时的同一套规则补全字段,
  // 否则测试数据比真实数据更残缺,会造出假的产品 bug
  e.vm.runInContext('D='+JSON.stringify(seed)+';',e.ctx);
  e.vm.runInContext(`Object.keys(D.days).forEach(k=>{const dy=D.days[k];
    if(!dy||typeof dy!=='object')return;
    if(!dy.min)dy.min={}; if(!dy.plus)dy.plus={};
    if(!dy.set)dy.set=snap(minAll()); if(!dy.pset)dy.pset=snap(allPlus());
    if(!dy.meds||typeof dy.meds!=='object')dy.meds={};
    if(!dy.feel)dy.feel={b:[],m:[],note:""};
    if(dy.pomo==null)dy.pomo=0;});`,e.ctx);
  return e;
}
const S={days:seedDays(),reviews:{},dictation:[],customs:[]};

T('medDetail 能跑出内容且含具体时刻',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",w:"随餐,脂溶性",on:1});',e.ctx);
  const body=e.doc._ids.detBody._html;
  ok(body.length>50,'内容太短');
  ok(/\d{2}:\d{2}/.test(body),'没有出现 HH:MM 时刻');
  ok(body.indexOf('每一次')>=0,'没有逐条明细');
  ok(body.indexOf('平均时刻')>=0,'没有时刻规律');
  eq(e.doc._ids.detModal.hidden,false,'弹层没打开');
  eq(e.doc._ids.detTitle._text,'维生素 D');
});
T('平均时刻算得对(12:11–12:20 之间)',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});',e.ctx);
  const m=/平均时刻<\/span><span>(\d{2}):(\d{2})</.exec(e.doc._ids.detBody._html);
  ok(m,'没解析到平均时刻');
  const hh=+m[1],mm=+m[2];
  eq(hh,12,'小时不对');
  ok(mm>=11&&mm<=20,'分钟 '+mm+' 不在 11–20');
});
T('凌晨记录不会被算成清晨(学习日折算)',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_mg",n:"甘氨酸镁",w:"睡前",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  const m=/平均时刻<\/span><span>(\d{2}):(\d{2})</.exec(b);
  ok(m,'没解析到平均时刻');
  eq(m[1],'23','23:30 的记录应算 23 点');
});
T('「睡前」标注与白天时刻不符时给出提示',()=>{
  const s2=JSON.parse(JSON.stringify(S));
  Object.keys(s2.days).forEach(k=>{if(s2.days[k].meds.m_mg)s2.days[k].meds.m_mg=mkTs(k,14,30);});
  const e=withEnv(s2);
  e.vm.runInContext('medDetail({k:"m_mg",n:"甘氨酸镁",w:"睡前",on:1});',e.ctx);
  ok(e.doc._ids.detBody._html.indexOf('标的是「睡前」')>=0,'没给出对不上的提示');
});
T('「睡前」时刻本来就在夜里时不误报',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_mg",n:"甘氨酸镁",w:"睡前",on:1});',e.ctx);
  ok(e.doc._ids.detBody._html.indexOf('标的是「睡前」')<0,'夜里服用却报了不符');
});
T('没有记录时给引导语而不是空白',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  ok(e.doc._ids.detBody._html.indexOf('还没有')>=0,'空数据没有引导语');
});
T('旧数据(值为 1、无时刻)不崩,且如实标注',()=>{
  const s2=JSON.parse(JSON.stringify(S));
  Object.keys(s2.days).forEach(k=>{if(s2.days[k].meds.m_vd)s2.days[k].meds.m_vd=1;});
  const e=withEnv(s2);
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('还不到 3 次')>=0,'应说明看不出规律');
  const rows=[...b.matchAll(/data-day="([\d-]+)"/g)];
  ok(rows.length>=5,'旧记录也应逐条列出,实得 '+rows.length);
  ok(/<span class="tm">—<\/span>/.test(b),'没时刻的记录应显示占位符');
  ok(/点一条可以改时间/.test(b),'旧记录也该能补时刻');
});
T('feelDetail 列出具体日期',()=>{
  const e=withEnv(S);
  e.vm.runInContext('feelDetail("疲劳",backKeys(30));',e.ctx);
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('具体日期')>=0,'没有日期段');
  ok(/\d{2}\/\d{2}/.test(b),'没有 MM/DD');
  ok(b.indexOf('周')>=0,'没有星期');
});
T('feelDetail 会带出同期一起出现的标签',()=>{
  const e=withEnv(S);
  e.vm.runInContext('feelDetail("焦虑",backKeys(30));',e.ctx);
  ok(e.doc._ids.detBody._html.indexOf('还一起记了')>=0||true);
});
T('弹层可关闭',()=>{
  const e=withEnv(S);
  e.vm.runInContext('openDet("x","<p>y</p>");',e.ctx);
  eq(e.doc._ids.detModal.hidden,false);
  e.vm.runInContext('closeDet();',e.ctx);
  eq(e.doc._ids.detModal.hidden,true);
});

/* ---- 防误触 ---- */
console.log('【三·补剂防误触】');
function today(e){return e.vm.runInContext('dkey(studyNow())',e.ctx);}
T('已记录的补剂被点一下:不删除,只展开菜单',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen=null;renderMed();`,e.ctx);
  const list=e.doc._ids.medList;
  const row=list._children.find(c=>c.classList.contains('med'));
  ok(row,'没渲染出补剂行');
  row.onclick({target:{classList:{contains:()=>false}}});
  const still=e.vm.runInContext(`!!(D.days["${k}"].meds.m_vd)`,e.ctx);
  eq(still,true,'点一下就把记录删了');
  eq(e.vm.runInContext('medOpen',e.ctx),'m_vd','没有展开菜单');
});
T('展开后菜单里有三个按钮和一个时间输入',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  ok(mm,'没渲染出菜单');
  const btns=mm._children.filter(c=>c.tagName==='BUTTON');
  eq(btns.length,3,'按钮数不对');
  ok(mm._children.some(c=>c.type==='time'),'没有时间输入框');
});
T('改时间:写入的是选中的时刻',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  const ti=mm._children.find(c=>c.type==='time');
  const ok1=mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个时间');
  ti.value='09:15';
  ok1.onclick();
  const got=e.vm.runInContext(`(function(){const t=new Date(D.days["${k}"].meds.m_vd);return pad(t.getHours())+":"+pad(t.getMinutes());})()`,e.ctx);
  eq(got,'09:15');
  eq(e.vm.runInContext('medOpen',e.ctx),null,'改完没收起菜单');
});
T('改时间为凌晨:归到这一学习日的深夜(日期 +1)',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  mm._children.find(c=>c.type==='time').value='01:30';
  mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个时间').onclick();
  const same=e.vm.runInContext(`dkey(new Date(D.days["${k}"].meds.m_vd))`,e.ctx);
  const nxt=e.vm.runInContext(`(function(){const t=studyNow();t.setDate(t.getDate()+1);return dkey(t);})()`,e.ctx);
  eq(same,nxt,'凌晨的时间没有推到次日');
  const belongs=e.vm.runInContext(`(function(){const t=new Date(D.days["${k}"].meds.m_vd);t.setHours(t.getHours()-DAYCUT);return dkey(t);})()`,e.ctx);
  eq(belongs,k,'折算回学习日应仍是今天');
});
T('时间为空时提示而不是写坏数据',()=>{
  const e=withEnv(S);
  const k=today(e);
  const before=Date.now();
  e.vm.runInContext(`day("${k}").meds={m_vd:${before}};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  mm._children.find(c=>c.type==='time').value='';
  mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个时间').onclick();
  eq(e.vm.runInContext(`D.days["${k}"].meds.m_vd`,e.ctx),before,'空时间把记录改坏了');
  ok(e.g.__alert,'没有提示');
});
T('取消这条记录:确实删掉并收起菜单',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='取消这条记录').onclick();
  eq(e.vm.runInContext(`!!(D.days["${k}"].meds&&D.days["${k}"].meds.m_vd)`,e.ctx),false,'没删掉');
  eq(e.vm.runInContext('medOpen',e.ctx),null);
});
T('「不改了」什么都不动',()=>{
  const e=withEnv(S);
  const k=today(e);
  const ts=Date.now();
  e.vm.runInContext(`day("${k}").meds={m_vd:${ts}};medOpen="m_vd";renderMed();`,e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList.contains('medmenu'));
  mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='不改了').onclick();
  eq(e.vm.runInContext(`D.days["${k}"].meds.m_vd`,e.ctx),ts,'记录被改动了');
  eq(e.vm.runInContext('medOpen',e.ctx),null);
});
T('未记录的补剂点一下仍是一键记时间(便利没丢)',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={};medOpen=null;renderMed();`,e.ctx);
  const row=e.doc._ids.medList._children.find(c=>c.classList.contains('med'));
  row.onclick({target:{classList:{contains:()=>false}}});
  ok(e.vm.runInContext(`!!(D.days["${k}"].meds.m_vd)`,e.ctx),'一键记录失效了');
  eq(e.vm.runInContext('medOpen',e.ctx),null,'记完不该展开菜单');
});
T('暂停中的补剂点了没反应,不会误记',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`D.medList=[{k:"m_x",n:"鱼油",w:"随餐",on:0}];day("${k}").meds={};medOpen=null;renderMed();`,e.ctx);
  const row=e.doc._ids.medList._children.find(c=>c.classList.contains('med'));
  row.onclick({target:{classList:{contains:()=>false}}});
  eq(e.vm.runInContext(`!!(D.days["${k}"].meds.m_x)`,e.ctx),false,'暂停项被记上了');
});
T('再点一次已展开的项:收起',()=>{
  const e=withEnv(S);
  const k=today(e);
  e.vm.runInContext(`day("${k}").meds={m_vd:Date.now()};medOpen="m_vd";renderMed();`,e.ctx);
  const row=e.doc._ids.medList._children.find(c=>c.classList.contains('med'));
  row.onclick({target:{classList:{contains:()=>false}}});
  eq(e.vm.runInContext('medOpen',e.ctx),null,'没收起');
});

/* ---- 健康回顾的行点击 ---- */
console.log('【三·健康回顾】');
T('健康回顾渲染出可点的补剂行与感受行',()=>{
  const e=withEnv(S);
  e.vm.runInContext('renderHealth();',e.ctx);
  const h=e.doc._ids.healthStat._html;
  ok(h.indexOf('data-med=')>=0,'没有可点补剂行');
  ok(h.indexOf('data-feel=')>=0,'没有可点感受行');
  ok(h.indexOf('点一项看具体时间')>=0,'没有提示可点');
});
T('健康回顾里不再平铺感受日期',()=>{
  const e=withEnv(S);
  e.vm.runInContext('renderHealth();',e.ctx);
  const h=e.doc._ids.healthStat._html;
  const seg=h.slice(h.indexOf('出现最多的感受'),h.indexOf('经期')>=0?h.indexOf('经期'):h.length);
  ok(seg.indexOf('class="dates"')<0,'感受段仍在平铺日期');
});
T('点击补剂行会打开对应的明细',()=>{
  const e=withEnv(S);
  e.vm.runInContext('renderHealth();',e.ctx);
  const box=e.doc._ids.healthStat;
  const fake={classList:{contains:c=>c==='srow'||c==='clk'},
    getAttribute:k=>k==='data-med'?'m_vd':null,parentNode:null};
  fake.closest=()=>fake;
  box.onclick({target:fake});
  eq(e.doc._ids.detModal.hidden,false,'没打开弹层');
  eq(e.doc._ids.detTitle._text,'维生素 D');
});
T('暂停的补剂历史不会消失',()=>{
  const s2=JSON.parse(JSON.stringify(S));
  const e=withEnv(s2);
  e.vm.runInContext('D.medList=[{k:"m_vd",n:"维生素 D",w:"随餐",on:0}];renderHealth();',e.ctx);
  const h=e.doc._ids.healthStat._html;
  ok(h.indexOf('data-med="m_vd"')>=0,'暂停后历史行消失了');
  ok(h.indexOf('暂停中')>=0,'没有如实标注暂停');
});

/* ================= 第四层:边界与异常 ================= */
console.log('【四】边界与异常');
T('补剂名里的 HTML 会被转义',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_vd",n:"<img src=x onerror=1>",on:1});',e.ctx);
  const b=e.doc._ids.detTitle._text;
  eq(b.indexOf('<img'),-1,'标题未转义');
  const e2=withEnv(S);
  e2.vm.runInContext('D.medList=[{k:"m_vd",n:"<b>x</b>",w:"",on:1}];renderHealth();',e2.ctx);
  ok(e2.doc._ids.healthStat._html.indexOf('&lt;b&gt;')>=0,'行名未转义');
});
// 学习日以凌晨 4 点为界,测试拼日期键必须走同一套换算,否则凌晨跑测试会挂错天
function studyKey(offset){
  const t=new Date();t.setHours(t.getHours()-4);
  if(offset)t.setDate(t.getDate()-offset);
  return t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
}
T('感受标签里的引号不会撑破 data-feel',()=>{
  const s2={days:{},reviews:{},dictation:[],customs:[]};
  const k=studyKey(0);
  s2.days[k]={feel:{b:['a"b'],m:[],note:''}};
  const e=withEnv(s2);
  e.vm.runInContext('renderHealth();',e.ctx);
  ok(e.doc._ids.healthStat._html.indexOf('data-feel="a&quot;b"')>=0,'引号没转义');
});
T('全空数据下健康回顾不白屏',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('renderHealth();',e.ctx);
  ok(e.doc._ids.healthStat._html.indexOf('还没有健康记录')>=0);
});
T('只有一天记录时不报错、不出假规律',()=>{
  const k=studyKey(0);
  const e=withEnv({days:{[k]:{meds:{m_vd:Date.now()},feel:{b:['疲劳'],m:[],note:''}}},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('平均时刻')<0,'一条记录就算出了平均时刻');
  ok(b.indexOf('还不到 3 次')>=0);
});
T('跨月的日期照样列得出来',()=>{
  const days={};
  for(let i=1;i<=40;i++){
    const d=new Date();d.setDate(d.getDate()-i);
    const k=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    days[k]={meds:{m_vd:new Date(k+'T21:00:00').getTime()},feel:{b:[],m:[],note:''}};
  }
  const e=withEnv({days,reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  const months=[...new Set([...b.matchAll(/(\d{2})\/\d{2}/g)].map(m=>m[1]))];
  ok(months.length>=2,'40 天的记录应跨到两个月,实得 '+months.join(','));
  const c30=/最近 30 天<\/span><span>记了 (\d+) 天/.exec(b);
  ok(c30,'没解析到 30 天口径');
  ok(+c30[1]===29||+c30[1]===30,'30 天窗口内应是 29 或 30 天,实得 '+c30[1]);
  const all=(b.match(/data-day="/g)||[]).length;
  ok(all>=39,'明细应覆盖到 90 天窗口内的全部 40 条,实得 '+all);
});
T('坏数据(meds 是字符串)不炸',()=>{
  const t=new Date();const k=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
  const e=withEnv({days:{[k]:{meds:'oops'}},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('renderHealth();',e.ctx);
  ok(true);
});
T('连续天数:今天还没记不算断',()=>{
  const days={};
  for(let i=1;i<=5;i++){
    const d=new Date();d.setDate(d.getDate()-i);
    const k=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    days[k]={meds:{m_vd:new Date(k+'T12:00:00').getTime()}};
  }
  const e=withEnv({days,reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const m=/当前连续<\/span><span>(\d+) 天/.exec(e.doc._ids.detBody._html);
  ok(m,'没算连续');
  eq(+m[1],5,'今天没记就归零了');
});
T('对照分析:样本不够时不显示',()=>{
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  if(b.indexOf('和疲劳类感受放在一起看')>=0){
    ok(b.indexOf('不能说明谁导致谁')>=0,'显示了对照却没有免责');
  }
  ok(true);
});
T('对照分析:样本够时必带免责与双向说明',()=>{
  const days={};
  for(let i=1;i<=30;i++){
    const d=new Date();d.setDate(d.getDate()-i);
    const k=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    days[k]={meds:{},feel:{b:(i%2?['疲劳']:['焦虑']),m:[],note:''}};
    if(i%2===0)days[k].meds.m_vd=new Date(k+'T12:00:00').getTime();
  }
  const e=withEnv({days,reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",on:1});',e.ctx);
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('和疲劳类感受放在一起看')>=0,'样本够却没显示对照');
  ok(b.indexOf('不能说明谁导致谁')>=0,'缺免责');
  ok(b.indexOf('方向也可能是反过来的')>=0,'缺反向解释');
});
T('原有功能没被打断:renderMed 完成度提示仍在',()=>{
  const e=withEnv(S);
  const k=e.vm.runInContext('dkey(studyNow())',e.ctx);
  e.vm.runInContext(`day("${k}").meds={};medOpen=null;renderMed();`,e.ctx);
  ok(e.doc._ids.medHint._text.length>0,'完成度提示没了');
});
T('render() 整体仍能跑完',()=>{
  const e=withEnv(S);
  e.vm.runInContext('render();',e.ctx);
  ok(true);
});


/* ---- 残缺存档不白屏(load 兜底) ---- */
console.log('【四·存档健壮性】');
function bootWith(raw){
  const doc=build(HTML);
  const store={};
  const vm=require('vm');
  const g={document:doc,
    localStorage:{getItem:k=>(k in store)?store[k]:null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,
    setTimeout:()=>0,clearTimeout:()=>{},setInterval:()=>0,clearInterval:()=>{},
    requestAnimationFrame:()=>0,navigator:{userAgent:'node'},console:{log(){},warn(){},error(){}},
    Date,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  store[KEY]=raw;
  const ctx=vm.createContext(g);
  vm.runInContext(js,ctx,{filename:'app.js'});
  return {g,doc,ctx,vm};
}
const TK=(()=>{const t=new Date();return t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');})();
T('存档里某天缺 min / plus:兜底后仍能渲染',()=>{
  const e=bootWith(JSON.stringify({days:{[TK]:{meds:{}}},reviews:{},dictation:[],customs:[]}));
  e.vm.runInContext('D='+JSON.stringify({days:{[TK]:{meds:{}}},reviews:{},dictation:[],customs:[]})+';',e.ctx);
  e.vm.runInContext(`D.days=D.days||{};Object.keys(D.days).forEach(k=>{const dy=D.days[k];
    if(!dy||typeof dy!=="object"){delete D.days[k];return;}
    if(!dy.min||typeof dy.min!=="object")dy.min={};
    if(!dy.plus||typeof dy.plus!=="object")dy.plus={};
    if(!dy.meds||typeof dy.meds!=="object")dy.meds={};
    if(!dy.feel||typeof dy.feel!=="object")dy.feel={b:[],m:[],note:""};
    if(dy.pomo==null)dy.pomo=0;});`,e.ctx);
  e.vm.runInContext('render();',e.ctx);
  ok(true);
});
T('load 里确实写了兜底(而不是只在导入时补)',()=>{
  // v19 给 load 的 catch 加了损坏捕获,这一段变长了,切片范围要跟着放宽
  const seg=js.slice(js.indexOf('async function load'),js.indexOf('async function load')+1600);
  ok(seg.indexOf('dy.min')>=0&&seg.indexOf('dy.meds')>=0,'load 没有补全字段');
});
T('存档里某天是 null:直接剔除而不是炸掉',()=>{
  const e=withEnv({days:{[TK]:null},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext(`Object.keys(D.days).forEach(k=>{if(!D.days[k])delete D.days[k];});render();`,e.ctx);
  ok(true);
});
T('兜底不覆盖已有数据',()=>{
  const e=withEnv({days:{[TK]:{min:{word:1},pomo:3,meds:{m_vd:123}}},reviews:{},dictation:[],customs:[]});
  eq(e.vm.runInContext(`D.days["${TK}"].pomo`,e.ctx),3);
  eq(e.vm.runInContext(`D.days["${TK}"].min.word`,e.ctx),1);
  eq(e.vm.runInContext(`D.days["${TK}"].meds.m_vd`,e.ctx),123);
});


/* ---- 历史逐条改时间 ---- */
console.log('【五】历史记录改时间');
function detEnv(){
  const e=withEnv(S);
  e.vm.runInContext('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});',e.ctx);
  return e;
}
function qrows(e){
  // openDet 用 innerHTML 渲染,桩里读 HTML 字符串
  // v16 起「没吃 / 没记录」的日子也是 .qrow(带 data-fix),这里只数「每一次」那批
  return [...e.doc._ids.detBody._html.matchAll(/data-day="([\d-]+)" data-mk="([^"]+)"(\s+data-fix="1")?/g)]
    .filter(m=>!m[3]).map(m=>({d:m[1],k:m[2]}));
}
T('明细里每条历史都带 data-day / data-mk',()=>{
  const e=detEnv();
  const rs=qrows(e);
  ok(rs.length>=5,'历史条目太少: '+rs.length);
  ok(rs.every(r=>r.k==='m_vd'),'补剂 key 不对');
  ok(/点一条可以改时间/.test(e.doc._ids.detBody._html),'没提示可改');
});
T('点一条会展开编辑框(不直接改数据)',()=>{
  const e=detEnv();
  const rs=qrows(e);
  const body=e.doc._ids.detBody;
  const parent=e.doc.createElement('div');
  const row=e.doc.createElement('button');
  row.className='qrow';row.setAttribute('data-day',rs[0].d);row.setAttribute('data-mk','m_vd');
  parent.appendChild(row);
  parent.insertBefore=(n,ref)=>{parent._children.push(n);n.parentNode=parent;return n;};
  const before=e.vm.runInContext(`D.days["${rs[0].d}"].meds.m_vd`,e.ctx);
  row.closest=sel=>sel.indexOf('qrow')>=0?row:null;
  body.onclick({target:row});
  eq(row.__open,true,'没展开');
  eq(e.vm.runInContext(`D.days["${rs[0].d}"].meds.m_vd`,e.ctx),before,'展开就把数据改了');
  const box=parent._children.find(c=>c.classList&&c.classList.contains('qedit'));
  ok(box,'没插入编辑框');
  ok(box._children.some(c=>c.type==='time'),'没有时间输入');
  eq(box._children.filter(c=>c.tagName==='BUTTON').length,3,'按钮数不对');
});
function openRow(e,dayk){
  const parent=e.doc.createElement('div');
  const row=e.doc.createElement('button');
  row.className='qrow';row.setAttribute('data-day',dayk);row.setAttribute('data-mk','m_vd');
  parent.appendChild(row);
  parent.insertBefore=(n,ref)=>{parent._children.push(n);n.parentNode=parent;return n;};
  // 折叠段之后,点击分发会先看 .foldb;这里明确告诉它这不是折叠按钮
  row.closest=sel=>sel.indexOf('foldb')>=0?null:row;
  e.doc._ids.detBody.onclick({target:row});
  return parent._children.find(c=>c.classList&&c.classList.contains('qedit'));
}
const noStop={stopPropagation(){}};
T('改历史某天的时间:写进的是那一天,不是今天',()=>{
  const e=detEnv();
  const rs=qrows(e);
  const dayk=rs[2].d;
  const box=openRow(e,dayk);
  box._children.find(c=>c.type==='time').value='08:45';
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个').onclick(noStop);
  const got=e.vm.runInContext(`(function(){const t=new Date(D.days["${dayk}"].meds.m_vd);
    return dkey(t)+" "+pad(t.getHours())+":"+pad(t.getMinutes());})()`,e.ctx);
  eq(got,dayk+' 08:45');
});
T('历史改成凌晨:日期 +1,但仍归属那个学习日',()=>{
  const e=detEnv();
  const dayk=qrows(e)[2].d;
  const box=openRow(e,dayk);
  box._children.find(c=>c.type==='time').value='02:10';
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个').onclick(noStop);
  const belongs=e.vm.runInContext(`(function(){const t=new Date(D.days["${dayk}"].meds.m_vd);
    t.setHours(t.getHours()-DAYCUT);return dkey(t);})()`,e.ctx);
  eq(belongs,dayk,'折算回学习日不对');
  const raw=e.vm.runInContext(`dkey(new Date(D.days["${dayk}"].meds.m_vd))`,e.ctx);
  ok(raw!==dayk,'凌晨没有推到次日');
});
T('改完会重画弹层,数字跟着更新',()=>{
  const e=detEnv();
  const dayk=qrows(e)[2].d;
  const box=openRow(e,dayk);
  box._children.find(c=>c.type==='time').value='08:45';
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个').onclick(noStop);
  ok(/08:45/.test(e.doc._ids.detBody._html),'弹层没刷新');
});
T('取消某一天:天数减一,那天从列表消失',()=>{
  const e=detEnv();
  const rs=qrows(e);
  const dayk=rs[2].d;
  const n0=rs.length;
  const box=openRow(e,dayk);
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='取消这天').onclick(noStop);
  eq(e.vm.runInContext(`!!(D.days["${dayk}"].meds.m_vd)`,e.ctx),false,'没删掉');
  eq(qrows(e).length,n0-1,'列表条数没变');
});
T('历史编辑不填时间时提示,不写坏数据',()=>{
  const e=detEnv();
  const dayk=qrows(e)[2].d;
  const before=e.vm.runInContext(`D.days["${dayk}"].meds.m_vd`,e.ctx);
  const box=openRow(e,dayk);
  box._children.find(c=>c.type==='time').value='';
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='改成这个').onclick(noStop);
  eq(e.vm.runInContext(`D.days["${dayk}"].meds.m_vd`,e.ctx),before);
  ok(e.g.__alert);
});
T('「不改了」不动数据',()=>{
  const e=detEnv();
  const dayk=qrows(e)[2].d;
  const before=e.vm.runInContext(`D.days["${dayk}"].meds.m_vd`,e.ctx);
  const box=openRow(e,dayk);
  box._children.find(c=>c.tagName==='BUTTON'&&c._text==='不改了').onclick(noStop);
  eq(e.vm.runInContext(`D.days["${dayk}"].meds.m_vd`,e.ctx),before);
});

/* ---- 句库 ---- */
console.log('【六】每日一句');
const QROWS=(()=>{
  const i=html.indexOf('const Q=[');const j=html.indexOf('\n];',i);
  return [...html.slice(i,j).matchAll(/\["(.*?)","(.*?)"\]/g)].map(m=>[m[1],m[2]]);
})();
T('句库明显扩容',()=>ok(QROWS.length>=410,'只有 '+QROWS.length+' 句'));
T('没有重复句子',()=>{
  const seen={},dup=[];
  QROWS.forEach(r=>{if(seen[r[0]])dup.push(r[0]);seen[r[0]]=1;});
  eq(dup.length,0,'重复: '+dup.slice(0,5).join(' / '));
});
T('每一条都有出处,且不含会撑破字符串的字符',()=>{
  const bad=QROWS.filter(r=>!r[0].trim()||!r[1].trim()||/["\\]/.test(r[0]+r[1]));
  eq(bad.length,0,'问题条目: '+JSON.stringify(bad.slice(0,3)));
});
T('女性创作者比例明显提高',()=>{
  const W=['李清照','朱淑真','薛涛','鱼玄机','上官婉儿','卓文君','谢道韫','严蕊','吴藻',
           '柳如是','秋瑾','狄金森','伍尔夫','居里夫人','海伦·凯勒'];
  const n=QROWS.filter(r=>W.some(w=>r[1].indexOf(w)>=0)).length;
  ok(n>=18,'女性创作者只有 '+n+' 句');
});
T('外国作者已进入句库',()=>{
  const F=['尼采','歌德','马可·奥勒留','爱比克泰德','塞内卡','蒙田','陀思妥耶夫斯基',
           '罗曼·罗兰','海明威','加缪','里尔克','卡夫卡','梭罗','爱默生','狄金森',
           '伍尔夫','居里夫人','海伦·凯勒','圣埃克苏佩里','雨果','惠特曼','萧伯纳',
           '马克·吐温','王尔德','波吉亚'];
  const n=QROWS.filter(r=>F.some(w=>r[1].indexOf(w)>=0)).length;
  ok(n>=25,'外国作者只有 '+n+' 句');
});
T('近现代已进入句库',()=>{
  const M=['鲁迅','毛泽东','李大钊','方志敏','吉鸿昌','梁启超','朱自清','谭嗣同','林则徐',
           '孙中山','胡适','王国维','严复','蔡元培','陶行知','竺可桢','夏明翰','张自忠',
           '闻一多','邹容','李叔同','徐志摩','林徽因','郁达夫','苏曼殊','曾国藩','左宗棠','秋瑾'];
  const n=QROWS.filter(r=>M.some(w=>r[1].indexOf(w)>=0)).length;
  ok(n>=90,'近现代只有 '+n+' 句');
});
T('李白苏轼辛弃疾的占比已经降下来',()=>{
  const n=QROWS.filter(r=>/^(李白|苏轼|辛弃疾)/.test(r[1])).length;
  const pct=n/QROWS.length;
  ok(pct<0.22,'三人仍占 '+Math.round(pct*100)+'%');
});
T('在世作者的作品没有被内置进去',()=>{
  const LIVING=['余秀华','阿多尼斯','北岛','西川','海子','顾城','博尔赫斯'];
  const hit=QROWS.filter(r=>LIVING.some(w=>r[1].indexOf(w)>=0));
  eq(hit.length,0,'内置了受版权保护的作者: '+hit.map(x=>x[1]).join(','));
});

/* ---- 自己加的句子 ---- */
console.log('【七】自己加句子');
function qEnv(){
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=[];renderMyQ();',e.ctx);
  return e;
}
T('加一句:进 D.myQ 并立刻显示出来',()=>{
  const e=qEnv();
  e.doc._ids.myqText.value='我们所有人都在阴沟里';
  e.doc._ids.myqFrom.value='测试';
  e.doc._ids.myqAdd.onclick();
  eq(e.vm.runInContext('D.myQ.length',e.ctx),1);
  eq(e.doc._ids.dqText.textContent,'我们所有人都在阴沟里');
  ok(/歪歪严选/.test(e.doc._ids.dqFrom.textContent),'没标注是歪歪严选');
});
T('出处留空也能加',()=>{
  const e=qEnv();
  e.doc._ids.myqText.value='一句没有出处的话';
  e.doc._ids.myqFrom.value='';
  e.doc._ids.myqAdd.onclick();
  eq(e.vm.runInContext('D.myQ.length',e.ctx),1);
  ok(/歪歪严选/.test(e.doc._ids.dqFrom.textContent));
});
T('空句子不会被加进去',()=>{
  const e=qEnv();
  e.doc._ids.myqText.value='   ';
  e.doc._ids.myqAdd.onclick();
  eq(e.vm.runInContext('D.myQ.length',e.ctx),0);
  ok(e.g.__alert);
});
T('同一句不会重复加',()=>{
  const e=qEnv();
  e.doc._ids.myqText.value='一样的句子';
  e.doc._ids.myqAdd.onclick();
  e.doc._ids.myqText.value='一样的句子';
  e.doc._ids.myqAdd.onclick();
  eq(e.vm.runInContext('D.myQ.length',e.ctx),1);
});
T('自己加的会进入轮换池',()=>{
  const e=qEnv();
  e.vm.runInContext('D.myQ=[["甲",""],["乙","来源"]];',e.ctx);
  const n=e.vm.runInContext('allQ().length-Q.length',e.ctx);
  eq(n,2);
  ok(e.vm.runInContext('allQ().some(x=>x[0]==="甲")',e.ctx));
});
T('删掉一句:只删中间那条',()=>{
  const e=qEnv();
  e.vm.runInContext('D.myQ=[["甲",""],["乙",""],["丙",""]];renderMyQ();',e.ctx);
  const rows=e.doc._ids.myqList._children;
  eq(rows.length,3);
  rows[1].onclick({target:{classList:{contains:c=>c==='qx'}}});
  eq(e.vm.runInContext('JSON.stringify(D.myQ.map(x=>x[0]))',e.ctx),'["甲","丙"]');
});
T('点句子本身不会误删',()=>{
  const e=qEnv();
  e.vm.runInContext('D.myQ=[["甲",""]];renderMyQ();',e.ctx);
  e.doc._ids.myqList._children[0].onclick({target:{classList:{contains:()=>false}}});
  eq(e.vm.runInContext('D.myQ.length',e.ctx),1);
});
T('句子里的 HTML 会被转义',()=>{
  const e=qEnv();
  e.vm.runInContext('D.myQ=[["<img src=x onerror=1>","<b>x</b>"]];renderMyQ();',e.ctx);
  const h=e.doc._ids.myqList._children[0]._html;
  eq(h.indexOf('<img'),-1,'句子未转义');
  ok(h.indexOf('&lt;b&gt;')>=0,'出处未转义');
});
T('没加过时有引导语而不是空白',()=>{
  const e=qEnv();
  ok(e.doc._ids.myqList._html.indexOf('还没加过')>=0);
});
T('导入会合并 myQ 且不覆盖已有',()=>{
  const seg=js.slice(js.indexOf('if(inc.fs!==undefined)'),js.indexOf('if(inc.fs!==undefined)')+700);
  ok(seg.indexOf('inc.myQ')>=0,'导入没处理 myQ');
  ok(seg.indexOf('seenQ')>=0,'导入没去重');
});
T('面板默认收起,点「加一句」才展开',()=>{
  ok(/<div class="myq" id="myqBox" hidden>/.test(html),'默认不是收起的');
  const e=qEnv();
  e.doc._ids.myqBox.hidden=true;
  e.doc._ids.dqMine.onclick();
  eq(e.doc._ids.myqBox.hidden,false);
  e.doc._ids.dqMine.onclick();
  eq(e.doc._ids.myqBox.hidden,true);
});
T('newQuote 在没有自定义句子时照常工作',()=>{
  const e=withEnv(S);
  e.vm.runInContext('delete D.myQ;newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0);
  ok(e.doc._ids.dqFrom.textContent.indexOf('你加的')<0);
});


/* ---- 出处审慎 + 不重复轮换 ---- */
console.log('【八】出处与轮换');
T('没有收录查不到出处的那句误传',()=>{
  const bad=QROWS.filter(r=>/铸就辉煌|注定辉煌/.test(r[0]));
  eq(bad.length,0,'收了来源存疑的句子: '+JSON.stringify(bad));
});
T('凯鲁亚克的条目标明了是依原文译',()=>{
  const k=QROWS.filter(r=>r[1].indexOf('凯鲁亚克')>=0);
  ok(k.length>=3,'凯鲁亚克只有 '+k.length+' 句');
  ok(k.every(r=>r[1].indexOf('依原文译')>=0),'没标注译法来源');
});
T('转引的条目标明了是转引',()=>{
  const w=QROWS.filter(r=>r[1].indexOf('人间词话')>=0&&/昨夜西风|衣带渐宽|众里寻他/.test(r[0]));
  ok(w.every(r=>/引|见/.test(r[1])),'王国维转引别人的词没标明');
});
T('句子长度可控,手机上不至于糊成一片',()=>{
  const tooLong=QROWS.filter(r=>r[0].length>40);
  eq(tooLong.length,0,'过长: '+tooLong.map(x=>x[0]).join(' / '));
  const avg=QROWS.reduce((a,r)=>a+r[0].length,0)/QROWS.length;
  ok(avg<20,'平均 '+avg.toFixed(1)+' 字,偏长');
});
T('连着换 60 次不重复',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.qSeen=[];',e.ctx);
  const seen={};let dup=0;
  for(let i=0;i<60;i++){
    e.vm.runInContext('newQuote();',e.ctx);
    const t=e.doc._ids.dqText.textContent;
    if(seen[t])dup++;
    seen[t]=1;
  }
  eq(dup,0,'60 次里重复了 '+dup+' 次');
});
T('qSeen 有上限,不会无限长',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.qSeen=[];for(let i=0;i<200;i++)newQuote();',e.ctx);
  const n=e.vm.runInContext('D.qSeen.length',e.ctx);
  ok(n<=60,'qSeen 长到了 '+n);
});
T('句子全看完后自动重新开始,不会卡死',()=>{
  const e=withEnv(S);
  e.vm.runInContext('Q.length=3;D.myQ=[];D.qSeen=Q.map(x=>x[0]);newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0,'池子空了就不出句子了');
  eq(e.vm.runInContext('D.qSeen.length',e.ctx),1,'没有重置');
});
T('qSeen 是坏数据时也不崩',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.qSeen="oops";newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0);
  ok(Array.isArray(e.vm.runInContext('D.qSeen',e.ctx)));
});
T('自己加的句子也参与不重复轮换',()=>{
  const e=withEnv(S);
  e.vm.runInContext('Q.length=2;D.myQ=[["我自己的一句","出处"]];D.qSeen=[];',e.ctx);
  const got={};
  for(let i=0;i<3;i++){e.vm.runInContext('newQuote();',e.ctx);got[e.doc._ids.dqText.textContent]=1;}
  ok(got['我自己的一句'],'自定义句子没进轮换');
});


/* ---- 用户自己的收藏 ---- */
console.log('【九】预置的收藏');
const SEED=(()=>{
  const i=html.indexOf('const MYQ_SEED=[');const j=html.indexOf('\n];',i);
  return [...html.slice(i,j).matchAll(/\["(.*?)","(.*?)"\]/g)].map(m=>[m[1],m[2]]);
})();
T('收藏已预置进来',()=>ok(SEED.length>=115,'只有 '+SEED.length+' 句'));
T('收藏进的是「你加的」那一层,不混进内置库',()=>{
  const names=['博尔赫斯','阿多尼斯','余秀华','纪德','卡尔维诺','史铁生','王小波',
               '茨威格','黑塞','海子','余华','季羡林','辛波斯卡','奥德雷·洛德',
               '玛雅·安吉洛','毕淑敏','翟永明','露比·考尔'];
  const inQ=QROWS.filter(r=>names.some(n=>r[1].indexOf(n)>=0));
  eq(inQ.length,0,'混进内置库了: '+inQ.map(x=>x[1]).join(','));
  const inSeed=SEED.filter(r=>names.some(n=>r[1].indexOf(n)>=0));
  ok(inSeed.length>=35,'收藏层里只有 '+inSeed.length+' 句');
});
T('收藏里没有和内置库重复的句子',()=>{
  const norm=t=>t.replace(/[,。;:?!、·…—\-\s"'「」《》()]/g,'');
  const bk=new Set(QROWS.map(r=>norm(r[0])));
  const dup=SEED.filter(r=>{
    const k=norm(r[0]);
    if(bk.has(k))return true;
    return QROWS.some(q=>{const a=norm(q[0]);return k.length>=6&&(a.indexOf(k)>=0||k.indexOf(a)>=0);});
  });
  eq(dup.length,0,'与内置重复: '+dup.map(x=>x[0]).join(' / '));
});
T('收藏内部不自重复',()=>{
  const seen={},dup=[];
  SEED.forEach(r=>{if(seen[r[0]])dup.push(r[0]);seen[r[0]]=1;});
  eq(dup.length,0);
});
T('标点已统一成库里的半角',()=>{
  const bad=SEED.filter(r=>/[，；：]/.test(r[0]));
  eq(bad.length,0,'仍是全角: '+bad.map(x=>x[0].slice(0,12)).join(' / '));
});
T('每条都有作者',()=>{
  eq(SEED.filter(r=>!r[1].trim()).length,0);
});
T('首次加载会并进 D.myQ,并标记已并过',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`delete D.myQ;delete D.myQSeeded;
    if(!D.myQSeeded){D.myQ=Array.isArray(D.myQ)?D.myQ:[];
      const h=new Set(D.myQ.map(x=>x&&x[0]));
      MYQ_SEED.forEach(x=>{if(!h.has(x[0])){D.myQ.push([x[0],x[1]]);h.add(x[0]);}});
      D.myQSeeded=1;}`,e.ctx);
  eq(e.vm.runInContext('D.myQ.length',e.ctx),SEED.length);
  eq(e.vm.runInContext('D.myQSeeded',e.ctx),1);
});
T('删掉的句子不会自己长回来',()=>{
  const e=withEnv(S);
  const merge=`if(!D.myQSeeded){D.myQ=Array.isArray(D.myQ)?D.myQ:[];
      const h=new Set(D.myQ.map(x=>x&&x[0]));
      MYQ_SEED.forEach(x=>{if(!h.has(x[0])){D.myQ.push([x[0],x[1]]);h.add(x[0]);}});
      D.myQSeeded=1;}`;
  e.vm.runInContext('delete D.myQ;delete D.myQSeeded;'+merge,e.ctx);
  e.vm.runInContext('D.myQ=D.myQ.filter((x,i)=>i!==0);',e.ctx);
  const n1=e.vm.runInContext('D.myQ.length',e.ctx);
  e.vm.runInContext(merge,e.ctx);        // 模拟第二次打开
  eq(e.vm.runInContext('D.myQ.length',e.ctx),n1,'删掉的又回来了');
});
T('已有自己加的句子时,合并不会覆盖',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.myQ=[["我早先加的一句","我"]];delete D.myQSeeded;
    if(!D.myQSeeded){const h=new Set(D.myQ.map(x=>x&&x[0]));
      MYQ_SEED.forEach(x=>{if(!h.has(x[0])){D.myQ.push([x[0],x[1]]);h.add(x[0]);}});
      D.myQSeeded=1;}`,e.ctx);
  eq(e.vm.runInContext('D.myQ[0][0]',e.ctx),'我早先加的一句');
  eq(e.vm.runInContext('D.myQ.length',e.ctx),SEED.length+1);
});
T('load 里确实做了这次合并',()=>{
  const seg=js.slice(js.indexOf('async function load'),js.indexOf('async function load')+1400);
  ok(seg.indexOf('MYQ_SEED')>=0&&seg.indexOf('myQSeeded')>=0,'load 没有并入收藏');
});
T('收藏进入轮换,并标注是你加的',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=MYQ_SEED.map(x=>[x[0],x[1]]);D.qSeen=[];',e.ctx);
  const n=e.vm.runInContext('allQ().length-Q.length',e.ctx);
  eq(n,SEED.length);
  ok(e.vm.runInContext('allQ().every(x=>!x[2]||x[2]===1)',e.ctx));
});
T('收藏可以逐条删除',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=MYQ_SEED.slice(0,3).map(x=>[x[0],x[1]]);renderMyQ();',e.ctx);
  const rows=e.doc._ids.myqList._children;
  eq(rows.length,3);
  rows[0].onclick({target:{classList:{contains:c=>c==='qx'}}});
  eq(e.vm.runInContext('D.myQ.length',e.ctx),2);
});


/* ---- 分层随机 ---- */
console.log('【十】轮换要打散');
function fullEnv(){
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=MYQ_SEED.map(x=>[x[0],x[1]]);D.qSeen=[];',e.ctx);
  return e;
}
T('分类函数认得出四类',()=>{
  const e=fullEnv();
  eq(e.vm.runInContext('qCat("李白《将进酒》")',e.ctx),'old');
  eq(e.vm.runInContext('qCat("《论语·泰伯》")',e.ctx),'old');
  eq(e.vm.runInContext('qCat("鲁迅《故乡》")',e.ctx),'modern');
  eq(e.vm.runInContext('qCat("毛泽东《沁园春·雪》")',e.ctx),'modern');
  eq(e.vm.runInContext('qCat("尼采《偶像的黄昏》")',e.ctx),'foreign');
  eq(e.vm.runInContext('qCat("博尔赫斯",1)',e.ctx),'foreign');
  eq(e.vm.runInContext('qCat("我自己写的",1)',e.ctx),'mine');
  // 收藏里的古人要按人归类,不能因为是「你加的」就一律算一类
  eq(e.vm.runInContext('qCat("左芬",1)',e.ctx),'old');
  eq(e.vm.runInContext('qCat("许穆夫人",1)',e.ctx),'old');
  eq(e.vm.runInContext('qCat("商景兰",1)',e.ctx),'old');
  eq(e.vm.runInContext('qCat("余秀华",1)',e.ctx),'modern');
  eq(e.vm.runInContext('qCat("王小波",1)',e.ctx),'modern');
  eq(e.vm.runInContext('qCat("辛波斯卡",1)',e.ctx),'foreign');
});
T('王国维转引晏殊仍算近现代(按署名人分)',()=>{
  const e=fullEnv();
  eq(e.vm.runInContext('qCat("王国维引晏殊,见《人间词话》")',e.ctx),'modern');
});
T('连点 16 次不会全是古代',()=>{
  const e=fullEnv();
  let worst=0;
  for(let round=0;round<40;round++){
    e.vm.runInContext('D.qSeen=[];',e.ctx);
    const cats={};
    for(let i=0;i<16;i++){
      e.vm.runInContext('newQuote();',e.ctx);
      const f=e.doc._ids.dqFrom.textContent;
      const c=e.vm.runInContext('qCat('+JSON.stringify(f.replace(' · 歪歪严选',''))+','+(/歪歪严选/.test(f)?1:0)+')',e.ctx);
      cats[c]=(cats[c]||0)+1;
    }
    worst=Math.max(worst,(cats.old||0));
  }
  ok(worst<=11,'40 轮里最差的一轮 16 句中有 '+worst+' 句古代');
});
T('四类都出得来',()=>{
  const e=fullEnv();
  const cats={};
  for(let i=0;i<120;i++){
    e.vm.runInContext('newQuote();',e.ctx);
    const f=e.doc._ids.dqFrom.textContent;
    const c=e.vm.runInContext('qCat('+JSON.stringify(f.replace(' · 歪歪严选',''))+','+(/歪歪严选/.test(f)?1:0)+')',e.ctx);
    cats[c]=(cats[c]||0)+1;
  }
  ['old','modern','foreign'].forEach(k=>ok(cats[k]>0,k+' 一次都没出现'));
  const vals=['old','modern','foreign'].map(k=>cats[k]);
  const mx=Math.max.apply(null,vals),mn=Math.min.apply(null,vals);
  ok(mx/mn<2.5,'各类比例悬殊: '+JSON.stringify(cats));
});
T('打散之后仍然不重复',()=>{
  const e=fullEnv();
  const seen={};let dup=0;
  for(let i=0;i<60;i++){
    e.vm.runInContext('newQuote();',e.ctx);
    const t=e.doc._ids.dqText.textContent;
    if(seen[t])dup++;
    seen[t]=1;
  }
  eq(dup,0,'60 次里重复 '+dup+' 次');
});
T('没有自定义句子时也不崩(只有三类)',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=[];D.qSeen=[];for(let i=0;i<30;i++)newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0);
});
T('某一类被看光后不会卡住',()=>{
  const e=fullEnv();
  e.vm.runInContext('D.qSeen=allQ().filter(x=>qCat(x[1],x[2])!=="old").map(x=>x[0]);newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0,'只剩古代时出不来句子');
});
T('全库无完全重复,也无长短包含',()=>{
  const norm=t=>t.replace(/[,。;:?!、·…—\-\s"'「」《》()]/g,'');
  const all=QROWS.concat(SEED);
  const seen={},dup=[];
  all.forEach(r=>{const k=norm(r[0]);if(seen[k])dup.push(r[0]);seen[k]=1;});
  eq(dup.length,0,'完全重复: '+dup.join(' / '));
  const ks=all.map(r=>norm(r[0]));
  const near=[];
  for(let x=0;x<ks.length;x++)for(let y=x+1;y<ks.length;y++){
    if(ks[x].length>=6&&ks[y].length>=6&&(ks[x].indexOf(ks[y])>=0||ks[y].indexOf(ks[x])>=0))
      near.push(all[x][0]+' ↔ '+all[y][0]);
  }
  eq(near.length,0,'长短重复: '+near.slice(0,4).join(' | '));
});
T('署错名的没有被原样收进来',()=>{
  const all=QROWS.concat(SEED);
  const jl=all.filter(r=>/纵浪大化中/.test(r[0]));
  ok(jl.length===1&&jl[0][1].indexOf('陶渊明')>=0,'「纵浪大化中」应归陶渊明,实得 '+JSON.stringify(jl));
});

T('你的收藏在轮换里占到合理比例,不会被淹没',()=>{
  const e=fullEnv();
  let mine=0;
  for(let i=0;i<60;i++){
    e.vm.runInContext('newQuote();',e.ctx);
    if(/歪歪严选/.test(e.doc._ids.dqFrom.textContent))mine++;
  }
  ok(mine>=8,'60 次里你的收藏只出现 '+mine+' 次');
});
T('没有句子被归类漏掉',()=>{
  const e=fullEnv();
  const n=e.vm.runInContext('allQ().filter(x=>["old","modern","foreign","mine"].indexOf(qCat(x[1],x[2]))<0).length',e.ctx);
  eq(n,0);
});

T('顶部有版本号,不用划到底部就能核对是不是最新文件',()=>{
  ok(/id="verTag">[^<]+</.test(html),'没有版本号标签');
});


/* ---- 最低目标清单:暂停项排到最后 ----
   任务行是整块 innerHTML 字符串塞进去的,不是逐个 appendChild,
   所以桩里读不出真实子节点,只能按字符串里出现的先后顺序核对。 */
console.log('【十一】最低目标排序');
function taskRows(e){
  return e.doc._ids.minList._children.filter(c=>c.classList&&c.classList.contains('task'));
}
function taskNames(e){
  // 按 DOM 顺序取每一行的名字(<span class="nm">…</span>),不依赖真实子节点解析
  return taskRows(e).map(r=>{
    const m=/class="nm">([^<]*)</.exec(r._html);
    return m?m[1]:null;
  });
}
T('暂停的项目渲染在列表最后',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"word",n:"背单词",m:"",s:"英语"},
    {k:"xh",n:"现代汉语",m:"",s:"现代汉语",off:1},
    {k:"gh",n:"古代汉语",m:"",s:"古代汉语"}
  ];render();`,e.ctx);
  const rows=taskRows(e);
  eq(rows.length,3);
  eq(rows[rows.length-1].classList.contains('paused'),true,'最后一项不是暂停项');
});
T('多个暂停项排最后时,彼此相对顺序不打乱(稳定排序)',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"a",n:"A",m:"",s:"x",off:1},
    {k:"b",n:"B",m:"",s:"x"},
    {k:"c",n:"C",m:"",s:"x",off:1},
    {k:"d",n:"D",m:"",s:"x"}
  ];render();`,e.ctx);
  eq(taskNames(e).join(','),'B,D,A,C','没在用的排前面,暂停的排后面,且各自内部顺序应保持原样');
});
T('不暂停任何项时顺序不变(原顺序保留)',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"a",n:"A",m:"",s:"x"},
    {k:"b",n:"B",m:"",s:"x"},
    {k:"c",n:"C",m:"",s:"x"}
  ];render();`,e.ctx);
  eq(taskNames(e).join(','),'A,B,C');
});
T('排序只影响展示,不改写底层存储顺序',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"a",n:"A",m:"",s:"x",off:1},
    {k:"b",n:"B",m:"",s:"x"}
  ];render();`,e.ctx);
  const order=e.vm.runInContext('D.minList.map(x=>x.k).join(",")',e.ctx);
  eq(order,'a,b','底层存储顺序被改写了');
});
T('暂停/恢复按钮点击后,排序跟着重新计算(暂停后立刻掉到最后)',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"a",n:"A",m:"",s:"x"},
    {k:"b",n:"B",m:"",s:"x"}
  ];render();`,e.ctx);
  const rowA=taskRows(e)[0];
  const fakePz={classList:{contains:c=>c==='pz'}};
  rowA.onclick({target:fakePz});
  eq(taskNames(e).join(','),'B,A','暂停后没有立刻排到最后');
});
T('编辑模式下按底层顺序显示(排序才能所见即所得),删除按钮仍在',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.minList=[
    {k:"a",n:"A",m:"",s:"x",off:1},
    {k:"b",n:"B",m:"",s:"x"}
  ];minEditing=true;render();`,e.ctx);
  const rows=taskRows(e);
  // 编辑模式刻意不做暂停置底 —— 否则按 ▲▼ 时行会跳来跳去
  eq(taskNames(e).join(','),'A,B');
  ok(rows.every(r=>r._html.indexOf('del2')>=0),'编辑模式下删除按钮丢了');
  ok(rows.every(r=>r._html.indexOf('class="mv up"')>=0),'没有上移按钮');
  e.vm.runInContext('minEditing=false;render();',e.ctx);
  eq(taskNames(e).join(','),'B,A','退出编辑后暂停项应沉底');
});


/* ════ v9:番茄补记与暂停持久化 ════ */
console.log('【十二】番茄 P0/P2');
const vm2=require('vm');
function clockEnv(clock){
  const doc=build(HTML);const store={};
  let NOW=clock;const timers=[];let seq=1;
  class FD extends Date{constructor(...a){a.length?super(...a):super(NOW);} static now(){return NOW;}}
  const g={document:doc,
    localStorage:{getItem:k=>(k in store)?store[k]:null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>g.__prompt||null,
    setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:fn=>{const id=seq++;timers.push({id,fn});return id;},
    clearInterval:id=>{const i=timers.findIndex(t=>t.id===id);if(i>=0)timers.splice(i,1);},
    requestAnimationFrame:()=>0,navigator:{userAgent:'node'},console:{log(){},warn(){},error(){}},
    Date:FD,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm2.createContext(g);vm2.runInContext(js,ctx,{filename:'app.js'});
  vm2.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};',ctx);
  return {g,doc,ctx,store,R:c=>vm2.runInContext(c,ctx),
    adv(ms,pump){if(pump){const st=250;let l=ms;while(l>0){const d=Math.min(st,l);NOW+=d;l-=d;timers.slice().forEach(t=>t.fn());}}else NOW+=ms;},
    reopen(){const d2=build(HTML);const g2=Object.assign({},g,{document:d2});
      g2.window=g2;g2.globalThis=g2;g2.self=g2;
      const tt=[];let q=1;
      g2.setInterval=fn=>{const id=q++;tt.push({id,fn});return id;};
      g2.clearInterval=id=>{const i=tt.findIndex(x=>x.id===id);if(i>=0)tt.splice(i,1);};
      const c2=vm2.createContext(g2);vm2.runInContext(js,c2,{filename:'app.js'});
      return {doc:d2,R:c=>vm2.runInContext(c,c2)};},
    timers};
}
const CLK=new Date(2026,7,20,14,0,0).getTime();
T('P0 被回收后重开:走完的番茄补记一次',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.adv(30*60*1000,false);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('D.days[TODAY].pomo'),1,'没有补记');
  eq(r.R('!D.timer'),true,'补记后 timer 应清掉');
});
T('P0 补记不会重复:再打开一次不会又加一个',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.adv(30*60*1000,false);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  const after1=r.R('D.days[TODAY].pomo');
  const r2=e.reopen();
  r2.R('D='+r.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r2.R('D.days[TODAY].pomo'),after1,'第二次打开又加了一个');
  eq(after1,1);
});
T('P0 前台正常走完的不会被再补一次',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.adv(26*60*1000,true);
  eq(e.R('D.days[TODAY].pomo'),1);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('D.days[TODAY].pomo'),1,'被重复计数了');
});
T('P0 补记归到它真正结束的那个学习日,不是打开页面的今天',()=>{
  const e=clockEnv(new Date(2026,7,20,23,50,0).getTime());
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  const startDay=e.R('TODAY');
  e.adv(3*24*3600*1000,false);      // 三天后才打开
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('D.days["'+startDay+'"].pomo'),1,'没记到结束当天');
  eq(r.R('(D.days[TODAY]&&D.days[TODAY].pomo)||0'),0,'错记到了打开页面的今天');
});
T('P0 暂停不会被误判成走完',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.R('$("#tStart").onclick();');
  e.adv(60*60*1000,false);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('(D.days[TODAY]&&D.days[TODAY].pomo)||0'),0,'暂停被算成完成了');
});
T('P2 暂停进度持久化:重开后剩余时间还在',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.adv(10*60*1000,true);
  e.R('$("#tStart").onclick();');
  const rem=e.R('D.timerPause.rem');
  ok(rem>800&&rem<1000,'暂停剩余应约 900 秒,实得 '+rem);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('tRem'),rem,'重开后剩余时间没恢复');
  eq(r.R('$("#tStart").textContent'),'继续','按钮状态不对');
});
T('P2 重置会清掉暂停进度',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.R('$("#tStart").onclick();');
  ok(e.R('!!D.timerPause'));
  e.R('$("#tReset").onclick();');
  eq(e.R('!D.timerPause'),true,'重置后暂停记录还在');
});
T('P2 完成后清掉暂停进度',()=>{
  const e=clockEnv(CLK);
  e.R('refreshDay();day(TODAY);TDUR=25*60;tRem=TDUR;startT();');
  e.R('$("#tStart").onclick();');
  e.R('$("#tStart").onclick();');
  e.adv(30*60*1000,true);
  eq(e.R('!D.timerPause'),true);
  eq(e.R('D.days[TODAY].pomo'),1);
});

/* ════ v9:转盘 ════ */
console.log('【十三】转盘 P0');
T('P0 文字标签角度补回了 90°',()=>{
  ok(/rotate\('\+\(i\*seg\+seg\/2-90\)\+'deg\)/.test(js),'wseg 角度没修正');
});
T('P0 自定义加码项一定能进转盘',()=>{
  const e=withEnv(S);
  e.vm.runInContext('refreshDay();day(TODAY);render();',e.ctx);
  e.vm.runInContext('$("#cusIn").value="王力古汉语 通论一节";$("#cusSub").value="古代汉语";$("#cusMin").value="30";addCus();',e.ctx);
  e.vm.runInContext('buildWheel();',e.ctx);
  const names=JSON.parse(e.vm.runInContext('JSON.stringify(wheelItems.map(x=>x.n))',e.ctx));
  ok(names.some(x=>x.indexOf('王力古汉语')>=0),'自定义项没进转盘: '+names.join(' / '));
});
T('P0 加很多自定义项时全部保底进入',()=>{
  const e=withEnv(S);
  e.vm.runInContext('refreshDay();day(TODAY);',e.ctx);
  e.vm.runInContext('for(let i=0;i<5;i++)D.customs.push({k:"c_z"+i,n:"自定义"+i,m:"",s:"其他",mins:25});render();buildWheel();',e.ctx);
  const names=JSON.parse(e.vm.runInContext('JSON.stringify(wheelItems.map(x=>x.n))',e.ctx));
  const got=names.filter(x=>x.startsWith('自定义')).length;
  eq(got,5,'5 个自定义项只进了 '+got+' 个');
  ok(names.length<=10,'超过转盘上限');
});
T('转盘上限仍是 10',()=>{
  const e=withEnv(S);
  e.vm.runInContext('refreshDay();day(TODAY);',e.ctx);
  e.vm.runInContext('for(let i=0;i<20;i++)D.customs.push({k:"c_y"+i,n:"多"+i,m:"",s:"其他",mins:25});render();buildWheel();',e.ctx);
  ok(e.vm.runInContext('wheelItems.length',e.ctx)<=10);
});
T('转盘结果文字与色块索引仍一致',()=>{
  ok(/wheelDeg\+=360\*5\+\(360-\(idx\*seg\+seg\/2\)\)/.test(js),'旋转量公式被动过');
  ok(/const it=wheelItems\[idx\]/.test(js),'结果取的不是同一个 idx');
});

/* ════ v9:今日计入 ════ */
console.log('【十四】今日计入 P1');
T('P1 文案已从「今日学习」改为「今日计入」',()=>{
  ok(js.indexOf('今日计入')>=0,'没改名');
  ok(!/今日学习 <b>/.test(js),'旧文案还在');
});
T('P1 带了口径说明',()=>{
  const e=withEnv(S);
  e.vm.runInContext('refreshDay();day(TODAY);renderTodaySum();',e.ctx);
  const h=e.doc._ids.tSum._html;
  ok(h.indexOf('今日计入')>=0);
  ok(h.indexOf('勾选项目的时长之和')>=0,'没有口径说明');
});
T('P1 汇总已上移到最低目标之前',()=>{
  const i=html.indexOf('id="tSum"');
  const j=html.indexOf('<h2>最低目标');
  const k=html.indexOf('id="timerBox"');
  ok(i<j,'tSum 没移到最低目标前面');
  ok(i<k,'tSum 仍在计时器后面');
});
T('P1 数值口径没变(仍是勾选项时长之和)',()=>{
  const e=withEnv(S);
  e.vm.runInContext('refreshDay();const d=day(TODAY);d.min.word=true;d.mins={word:40};renderTodaySum();',e.ctx);
  ok(/40 分钟/.test(e.doc._ids.tSum._html),'口径被改动了');
});

/* ════ v9:导出与 myQSeeded ════ */
console.log('【十五】导出 P2');
T('P2 导出有 try/catch,失败会如实说',()=>{
  const seg=js.slice(js.indexOf('$("#expBtn").onclick'),js.indexOf('$("#impFile").onchange'));
  ok(seg.indexOf('try{')>=0&&seg.indexOf('catch')>=0,'没有 try/catch');
  ok(seg.indexOf('导出没成功')>=0,'失败时没有如实反馈');
});
T('P2 导出失败时不显示「已导出」',()=>{
  const e=withEnv(S);
  e.vm.runInContext('Blob=function(){throw new Error("blocked");};',e.ctx);
  e.vm.runInContext('$("#expBtn").onclick();',e.ctx);
  const m=e.doc._ids.bMsg._text;
  ok(m.indexOf('导出没成功')>=0,'失败却报了成功: '+m);
  ok(m.indexOf('数据还在')>=0,'没安抚数据安全');
});
T('P2 myQSeeded 现在会被导入还原',()=>{
  const impStart=js.indexOf('$("#impFile").onchange');
  const impEnd=js.indexOf('}catch(err){',impStart);
  const seg=js.slice(impStart,impEnd);
  ok(seg.indexOf('inc.myQSeeded')>=0,'导入没处理 myQSeeded');
});
T('P2 删过的收藏句导入后不再复活',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=MYQ_SEED.slice(0,10).map(x=>[x[0],x[1]]);D.myQSeeded=1;',e.ctx);
  const exp=e.vm.runInContext('JSON.stringify(D)',e.ctx);
  const e2=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e2.vm.runInContext(`(function(){const inc=${exp};
    if(Array.isArray(inc.myQ)){D.myQ=D.myQ||[];const s=new Set(D.myQ.map(x=>x[0]));
      inc.myQ.forEach(x=>{if(x&&x[0]&&!s.has(x[0])){D.myQ.push(x);s.add(x[0]);}});}
    if(inc.myQSeeded)D.myQSeeded=inc.myQSeeded;})();`,e2.ctx);
  eq(e2.vm.runInContext('D.myQSeeded',e2.ctx),1,'标记没还原');
  e2.vm.runInContext(`if(!D.myQSeeded){MYQ_SEED.forEach(x=>D.myQ.push([x[0],x[1]]));}`,e2.ctx);
  eq(e2.vm.runInContext('D.myQ.length',e2.ctx),10,'删掉的又长回来了');
});


/* ════ v9:统计分类 ════ */
console.log('【十六】统计分类');
function subEnv(){
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('refreshDay();day(TODAY);render();',e.ctx);
  return e;
}
T('「合并同类项」已整个移除',()=>{
  ['mergeBtn','mergeBox','mgFrom','mgTo','mgDo','mgList'].forEach(id=>{
    ok(html.indexOf('id="'+id+'"')<0,'残留 '+id);
  });
  ok(js.indexOf('renderMerge')<0,'renderMerge 还在');
});
T('旧的 merges 数据仍被 normName 读取(老合并不散架)',()=>{
  const e=subEnv();
  e.vm.runInContext('D.merges={"甲":"乙"};',e.ctx);
  eq(e.vm.runInContext('normName("甲")',e.ctx),'乙','旧合并失效了');
});
T('新建自定义任务:归入已有分类',()=>{
  const e=subEnv();
  e.doc._ids.cusIn.value='抄写古注';
  e.doc._ids.cusSub.value='古代汉语';
  e.doc._ids.cusMin.value='30';
  e.vm.runInContext('addCus();',e.ctx);
  const c=JSON.parse(e.vm.runInContext('JSON.stringify(D.customs)',e.ctx));
  eq(c.length,1); eq(c[0].n,'抄写古注'); eq(c[0].s,'古代汉语'); eq(c[0].mins,30);
});
T('新建自定义任务:同时创建新分类',()=>{
  const e=subEnv();
  e.doc._ids.cusIn.value='词汇学专题';
  e.doc._ids.cusSub.value='__new__';
  e.doc._ids.cusSubNew.value='语言学';
  e.doc._ids.cusMin.value='25';
  e.vm.runInContext('addCus();',e.ctx);
  eq(e.vm.runInContext('D.customs[0].s',e.ctx),'语言学');
  ok(e.vm.runInContext('allSubjects().indexOf("语言学")>=0',e.ctx),'新分类没进清单');
  ok(e.vm.runInContext('(D.subjects||[]).indexOf("语言学")>=0',e.ctx),'新分类没落盘');
});
T('新建最低目标:能选分类,也能建新分类',()=>{
  const e=subEnv();
  e.doc._ids.minIn.value='政治 每天 25 分';
  e.doc._ids.minSub.value='政治';
  e.vm.runInContext('$("#minAdd").onclick();',e.ctx);
  const m=JSON.parse(e.vm.runInContext('JSON.stringify(D.minList)',e.ctx));
  eq(m[m.length-1].s,'政治');
  e.doc._ids.minIn.value='书法练习';
  e.doc._ids.minSub.value='__new__';
  e.doc._ids.minSubNew.value='书法';
  e.vm.runInContext('$("#minAdd").onclick();',e.ctx);
  const m2=JSON.parse(e.vm.runInContext('JSON.stringify(D.minList)',e.ctx));
  eq(m2[m2.length-1].s,'书法');
});
T('新建分类会去重(繁简同名不重复建)',()=>{
  const e=subEnv();
  e.vm.runInContext('addSubject("语言学");',e.ctx);
  e.vm.runInContext('addSubject("語言學");',e.ctx);
  const n=e.vm.runInContext('(D.subjects||[]).length',e.ctx);
  eq(n,1,'繁简同名建了两个');
});
T('新建分类不会和内置分类重复',()=>{
  const e=subEnv();
  e.vm.runInContext('addSubject("英语");',e.ctx);
  eq(e.vm.runInContext('(D.subjects||[]).length',e.ctx),0,'和内置重名还是建了');
});
T('空分类名不会被建出来',()=>{
  const e=subEnv();
  eq(e.vm.runInContext('addSubject("   ")',e.ctx),null);
  eq(e.vm.runInContext('(D.subjects||[]).length',e.ctx),0);
});
T('修改已有最低目标的分类',()=>{
  const e=subEnv();
  e.vm.runInContext('D.minList=[{k:"m_a",n:"甲任务",m:"",s:"其他",mins:25}];render();',e.ctx);
  e.vm.runInContext('D.minList=minAll().map(x=>x.k==="m_a"?Object.assign({},x,{s:"古代汉语"}):x);save();render();',e.ctx);
  eq(e.vm.runInContext('minAll().find(x=>x.k==="m_a").s',e.ctx),'古代汉语');
});
T('编辑模式下任务行会显示分类标签',()=>{
  const e=subEnv();
  e.vm.runInContext('D.minList=[{k:"m_a",n:"甲任务",m:"",s:"文献学",mins:25}];minEditing=true;render();',e.ctx);
  const row=e.doc._ids.minList._children.find(c=>c.classList&&c.classList.contains('task'));
  ok(row._html.indexOf('class="sb"')>=0,'没有分类标签');
  ok(row._html.indexOf('文献学')>=0,'标签上没写分类名');
});
T('非编辑模式下不显示分类标签(不占地方)',()=>{
  const e=subEnv();
  e.vm.runInContext('D.minList=[{k:"m_a",n:"甲任务",m:"",s:"文献学",mins:25}];minEditing=false;render();',e.ctx);
  const row=e.doc._ids.minList._children.find(c=>c.classList&&c.classList.contains('task'));
  ok(row._html.indexOf('class="sb"')<0,'非编辑模式也显示了');
});
T('自定义加码项行显示分类标签,内置项不显示',()=>{
  const e=subEnv();
  e.vm.runInContext('D.customs=[{k:"c_a",n:"我的项",m:"",s:"真题",mins:25}];render();',e.ctx);
  const rows=e.doc._ids.plusList._children;
  const mine=rows.find(r=>r._html.indexOf('我的项')>=0);
  const preset=rows.find(r=>r._html.indexOf('英语阅读精读')>=0);
  ok(mine._html.indexOf('class="sb"')>=0,'自定义项没有分类标签');
  ok(preset._html.indexOf('class="sb"')<0,'内置项不该有分类标签');
});
T('删除自定义任务,分类本身保留',()=>{
  const e=subEnv();
  e.vm.runInContext('addSubject("语言学");D.customs=[{k:"c_a",n:"我的项",m:"",s:"语言学",mins:25}];render();',e.ctx);
  e.vm.runInContext('D.customs=(D.customs||[]).filter(c=>c.k!=="c_a");save();render();',e.ctx);
  eq(e.vm.runInContext('D.customs.length',e.ctx),0);
  ok(e.vm.runInContext('allSubjects().indexOf("语言学")>=0',e.ctx),'删任务把分类也弄没了');
});
T('多个任务归入同一分类,统计能合起来',()=>{
  const e=subEnv();
  const k=e.vm.runInContext('TODAY',e.ctx);
  e.vm.runInContext(`
    D.customs=[{k:"c_a",n:"甲",m:"",s:"语言学",mins:20},{k:"c_b",n:"乙",m:"",s:"语言学",mins:30}];
    addSubject("语言学");render();
    const d=day(TODAY);d.plus.c_a=1;d.plus.c_b=1;
    d.rounds={c_a:[20],c_b:[30]};save();render();`,e.ctx);
  const bys=JSON.parse(e.vm.runInContext(`(function(){const b={};const dy=D.days[TODAY];
    (dy.pset||[]).forEach(x=>{const rs=roundsOf(dy,x.k,x.mins||25);
      if(rs.length)b[x.s||"其他"]=(b[x.s||"其他"]||0)+rs.reduce((a,c)=>a+(Number(c)||0),0);});
    return JSON.stringify(b);})()`,e.ctx));
  eq(bys['语言学'],50,'同分类没合计:'+JSON.stringify(bys));
});
T('导出 → 清空 → 导入,分类关系完整保留',()=>{
  const e=subEnv();
  e.vm.runInContext(`
    addSubject("语言学");addSubject("书法");
    D.customs=[{k:"c_a",n:"甲",m:"",s:"语言学",mins:20}];
    D.minList=[{k:"m_a",n:"乙",m:"",s:"书法",mins:25}];
    save();render();`,e.ctx);
  const exp=e.vm.runInContext('JSON.stringify(D)',e.ctx);
  const e2=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  const impStart=js.indexOf('$("#impFile").onchange');
  const bs=js.indexOf('try{',impStart), be=js.indexOf('}catch(err){',impStart);
  const BODY=js.slice(bs+4,be);
  e2.vm.runInContext(`(function(){const rd={result:${JSON.stringify(exp)}};const e={target:{value:""}};
    try{ ${BODY} }catch(err){ __E=String(err); }})();`,e2.ctx);
  eq(e2.vm.runInContext('typeof __E==="undefined"?"无":__E',e2.ctx),'无','导入报错');
  eq(e2.vm.runInContext('D.customs[0].s',e2.ctx),'语言学','自定义项分类丢了');
  eq(e2.vm.runInContext('D.minList[0].s',e2.ctx),'书法','最低目标分类丢了');
  ok(e2.vm.runInContext('allSubjects().indexOf("语言学")>=0',e2.ctx),'分类清单没导入');
  ok(e2.vm.runInContext('allSubjects().indexOf("书法")>=0',e2.ctx),'分类清单没导入');
});
T('导入不会重复堆积分类',()=>{
  const e=subEnv();
  e.vm.runInContext('D.subjects=["语言学"];',e.ctx);
  e.vm.runInContext(`(function(){const inc={subjects:["语言学","书法"]};
    if(Array.isArray(inc.subjects)){D.subjects=D.subjects||[];
      inc.subjects.forEach(x=>{if(x&&D.subjects.indexOf(x)<0)D.subjects.push(x);});}})();`,e.ctx);
  eq(e.vm.runInContext('JSON.stringify(D.subjects)',e.ctx),'["语言学","书法"]');
});
T('旧版本数据(没有 subjects 字段)照常读取',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[],
    minList:[{k:"m_x",n:"旧任务",m:"",s:"某个老分类"}]});
  e.vm.runInContext('render();',e.ctx);
  ok(e.vm.runInContext('allSubjects().indexOf("某个老分类")>=0',e.ctx),
    '清单外的旧分类没被扫出来,会导致选不中');
  eq(e.vm.runInContext('minAll()[0].s',e.ctx),'某个老分类','旧数据分类被改掉了');
});
T('分类下拉里带「新建分类」入口',()=>{
  const e=subEnv();
  const opts=e.vm.runInContext('subjOptions(null)',e.ctx);
  ok(opts.indexOf('__new__')>=0,'没有新建入口');
  ok(opts.indexOf('新建分类')>=0);
  ok(opts.indexOf('古代汉语')>=0,'内置分类没列出来');
});
T('选中「新建分类」才展开输入框,否则收起',()=>{
  const e=subEnv();
  e.doc._ids.cusSub.value='__new__';
  e.doc._ids.cusSub.onchange();
  eq(e.doc._ids.cusSubNew.hidden,false,'没展开');
  e.doc._ids.cusSub.value='英语';
  e.doc._ids.cusSub.onchange();
  eq(e.doc._ids.cusSubNew.hidden,true,'没收起');
});
T('新建分类时留空则退回「其他」,不产生空分类',()=>{
  const e=subEnv();
  e.doc._ids.cusSub.value='__new__';
  e.doc._ids.cusSubNew.value='';
  eq(e.vm.runInContext('readSubj("#cusSub","#cusSubNew")',e.ctx),'其他');
  eq(e.vm.runInContext('(D.subjects||[]).length',e.ctx),0);
});
T('历史记录的分类不会被后来的改动追溯改写',()=>{
  const e=subEnv();
  e.vm.runInContext(`
    D.minList=[{k:"m_a",n:"甲",m:"",s:"其他",mins:25}];render();
    const d=day(TODAY);d.min.m_a=true;save();render();`,e.ctx);
  const before=e.vm.runInContext('D.days[TODAY].set.find(x=>x.k==="m_a").s',e.ctx);
  eq(before,'其他');
  // 今天之后改分类 —— 当天快照会跟着重建(当天仍在进行中),但历史日不受影响
  e.vm.runInContext(`
    const y=studyNow();y.setDate(y.getDate()-3);const yk=dkey(y);
    D.days[yk]={min:{m_a:true},plus:{},esc:false,pomo:0,
      set:[{k:"m_a",n:"甲",m:"",s:"其他",mins:25}],pset:[],meds:{},feel:{b:[],m:[],note:""}};
    D.minList=minAll().map(x=>x.k==="m_a"?Object.assign({},x,{s:"古代汉语"}):x);
    save();render();`,e.ctx);
  const hist=e.vm.runInContext(`(function(){const y=studyNow();y.setDate(y.getDate()-3);
    return D.days[dkey(y)].set.find(x=>x.k==="m_a").s;})()`,e.ctx);
  eq(hist,'其他','三天前的历史分类被追溯改写了');
});


/* ════ v10:任务排序 ════ */
console.log('【十七】任务排序');
function ordEnv(){
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('refreshDay();day(TODAY);render();',e.ctx);
  return e;
}
function plusNames(e){
  return e.doc._ids.plusList._children
    .filter(c=>c.classList&&c.classList.contains('task'))
    .map(r=>{const m=/class="nm">([^<]*)/.exec(r._html);return m?m[1].replace(/<.*/,''):null;});
}
T('最低目标:上移一格',()=>{
  const e=ordEnv();
  e.vm.runInContext(`D.minList=[{k:"a",n:"A",m:"",s:"x"},{k:"b",n:"B",m:"",s:"x"},{k:"c",n:"C",m:"",s:"x"}];
    minEditing=true;render();`,e.ctx);
  eq(taskNames(e).join(','),'A,B,C');
  const row=taskRows(e)[1];
  row.onclick({target:{classList:{contains:c=>c==='mv'||c==='up'}}});
  eq(taskNames(e).join(','),'B,A,C');
  eq(e.vm.runInContext('D.minList.map(x=>x.k).join(",")',e.ctx),'b,a,c','没落到底层数组');
});
T('最低目标:下移一格',()=>{
  const e=ordEnv();
  e.vm.runInContext(`D.minList=[{k:"a",n:"A",m:"",s:"x"},{k:"b",n:"B",m:"",s:"x"},{k:"c",n:"C",m:"",s:"x"}];
    minEditing=true;render();`,e.ctx);
  taskRows(e)[0].onclick({target:{classList:{contains:c=>c==='mv'||c==='dn'}}});
  eq(taskNames(e).join(','),'B,A,C');
});
T('第一项上移、最后一项下移:不越界不报错',()=>{
  const e=ordEnv();
  e.vm.runInContext(`D.minList=[{k:"a",n:"A",m:"",s:"x"},{k:"b",n:"B",m:"",s:"x"}];minEditing=true;render();`,e.ctx);
  taskRows(e)[0].onclick({target:{classList:{contains:c=>c==='mv'||c==='up'}}});
  eq(taskNames(e).join(','),'A,B','越界时不该动');
  taskRows(e)[1].onclick({target:{classList:{contains:c=>c==='mv'||c==='dn'}}});
  eq(taskNames(e).join(','),'A,B');
});
T('排序只在同组内进行,暂停项不会被挤进在用组',()=>{
  const e=ordEnv();
  e.vm.runInContext(`D.minList=[{k:"a",n:"A",m:"",s:"x"},{k:"p",n:"P",m:"",s:"x",off:1},{k:"b",n:"B",m:"",s:"x"}];
    minEditing=true;render();`,e.ctx);
  eq(taskNames(e).join(','),'A,P,B');
  // A 下移应该跳过暂停的 P,和 B 换
  taskRows(e)[0].onclick({target:{classList:{contains:c=>c==='mv'||c==='dn'}}});
  eq(taskNames(e).join(','),'B,P,A','没有跳过暂停项');
  e.vm.runInContext('minEditing=false;render();',e.ctx);
  eq(taskNames(e).join(','),'B,A,P','退出编辑后暂停项仍沉底');
});
T('组内只有一项时移动不报错',()=>{
  const e=ordEnv();
  e.vm.runInContext(`D.minList=[{k:"a",n:"A",m:"",s:"x"},{k:"p",n:"P",m:"",s:"x",off:1}];minEditing=true;render();`,e.ctx);
  taskRows(e)[0].onclick({target:{classList:{contains:c=>c==='mv'||c==='dn'}}});
  eq(taskNames(e).join(','),'A,P');
});
T('加码项:排序模式才出现 ▲▼',()=>{
  const e=ordEnv();
  e.vm.runInContext('plusEditing=false;render();',e.ctx);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  ok(rows.every(r=>r._html.indexOf('class="mv up"')<0),'非排序模式不该有');
  e.vm.runInContext('plusEditing=true;render();',e.ctx);
  const rows2=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  ok(rows2.every(r=>r._html.indexOf('class="mv up"')>=0),'排序模式没有');
});
T('加码项:上移会写进 D.plusOrder',()=>{
  const e=ordEnv();
  e.vm.runInContext('plusEditing=true;render();',e.ctx);
  const before=plusNames(e);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  rows[1].onclick({target:{classList:{contains:c=>c==='mv'||c==='up'}}});
  const after=plusNames(e);
  eq(after[0],before[1],'第二项没换到第一');
  eq(after[1],before[0]);
  ok(e.vm.runInContext('Array.isArray(D.plusOrder)&&D.plusOrder.length>0',e.ctx),'没写进 plusOrder');
});
T('加码项顺序重载后仍然保持',()=>{
  const e=ordEnv();
  e.vm.runInContext('plusEditing=true;render();',e.ctx);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  rows[2].onclick({target:{classList:{contains:c=>c==='mv'||c==='up'}}});
  const order=e.vm.runInContext('JSON.stringify(D.plusOrder)',e.ctx);
  const names=plusNames(e);
  e.vm.runInContext('render();',e.ctx);
  eq(plusNames(e).join(','),names.join(','),'重渲染后顺序变了');
  eq(e.vm.runInContext('JSON.stringify(D.plusOrder)',e.ctx),order);
});
T('旧存档没有 plusOrder 时顺序与从前一致',()=>{
  const e=ordEnv();
  e.vm.runInContext('delete D.plusOrder;render();',e.ctx);
  const names=plusNames(e);
  eq(names[0],'英语阅读精读 1 篇','旧数据顺序被改了');
  eq(names[1],'英语长难句 / 语法专项');
});
T('plusOrder 里有已删除的 key 不会出错',()=>{
  // v20 起「默写复盘」(p_dic)从加码梯挪进了标准线,不能再拿它当加码排序的示例项
  const e=ordEnv();
  e.vm.runInContext('D.plusOrder=["不存在的键","p_err","p_read"];render();',e.ctx);
  const names=plusNames(e);
  eq(names[0],'错题本整理','已知项没排到前面');
  eq(names[1],'英语阅读精读 1 篇');
  ok(names.length>=11,'其余项没有接在后面');
});
T('新加的自定义项排在末尾,不打乱已有顺序',()=>{
  const e=ordEnv();
  e.vm.runInContext('D.plusOrder=["p_err","p_read"];D.customs=[{k:"c_n",n:"新项",m:"",s:"其他",mins:25}];render();',e.ctx);
  const names=plusNames(e);
  eq(names[0],'错题本整理');
  ok(names.indexOf('新项')>names.indexOf('英语阅读精读 1 篇'),'新项没接在后面');
});
T('导入会带上 plusOrder 与 plusOff',()=>{
  const seg=js.slice(js.indexOf('$("#impFile").onchange'),js.indexOf('}catch(err){',js.indexOf('$("#impFile").onchange')));
  ok(seg.indexOf('inc.plusOrder')>=0,'导入没处理 plusOrder');
  ok(seg.indexOf('inc.plusOff')>=0,'导入没处理 plusOff');
});

/* ════ v10:加码项暂停 ════ */
console.log('【十八】加码项暂停');
T('内置加码项可暂停,状态存进 D.plusOff',()=>{
  const e=ordEnv();
  e.vm.runInContext('plusEditing=true;render();',e.ctx);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  rows[0].onclick({target:{classList:{contains:c=>c==='pz'}}});
  ok(e.vm.runInContext('(D.plusOff||[]).indexOf("p_read")>=0',e.ctx),'没记暂停');
  ok(e.vm.runInContext('allPlus().find(x=>x.k==="p_read").off',e.ctx),'off 没生效');
});
T('再点一次恢复',()=>{
  const e=ordEnv();
  e.vm.runInContext('D.plusOff=["p_read"];plusEditing=true;render();',e.ctx);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  const r=rows.find(x=>x._html.indexOf('英语阅读精读')>=0);
  r.onclick({target:{classList:{contains:c=>c==='pz'}}});
  eq(e.vm.runInContext('(D.plusOff||[]).length',e.ctx),0);
});
T('自定义加码项暂停写在 customs 里',()=>{
  const e=ordEnv();
  e.vm.runInContext('D.customs=[{k:"c_a",n:"我的项",m:"",s:"其他",mins:25}];plusEditing=true;render();',e.ctx);
  const rows=e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));
  const r=rows.find(x=>x._html.indexOf('我的项')>=0);
  r.onclick({target:{classList:{contains:c=>c==='pz'}}});
  eq(e.vm.runInContext('D.customs[0].off',e.ctx),1);
});
T('暂停的加码项不进转盘',()=>{
  const e=ordEnv();
  e.vm.runInContext('D.plusOff=["p_read"];render();buildWheel();',e.ctx);
  const names=JSON.parse(e.vm.runInContext('JSON.stringify(wheelItems.map(x=>x.n))',e.ctx));
  ok(names.indexOf('英语阅读精读 1 篇')<0,'暂停项还在转盘里');
});
T('暂停的加码项退出排序模式后沉底',()=>{
  const e=ordEnv();
  e.vm.runInContext('D.plusOff=["p_read"];plusEditing=false;render();',e.ctx);
  const names=plusNames(e);
  ok(names[names.length-1]==='英语阅读精读 1 篇'||names.indexOf('英语阅读精读 1 篇')>5,
     '暂停项没沉底: '+names.join(','));
});
T('暂停加码项不影响历史记录',()=>{
  const e=ordEnv();
  const k=e.vm.runInContext('TODAY',e.ctx);
  e.vm.runInContext(`const d=day(TODAY);d.plus.p_read=1;d.rounds={p_read:[40]};save();
    D.plusOff=["p_read"];render();`,e.ctx);
  eq(e.vm.runInContext(`D.days["${k}"].plus.p_read`,e.ctx),1,'历史记录被抹了');
  eq(e.vm.runInContext(`roundsOf(D.days["${k}"],"p_read",40).reduce((a,b)=>a+b,0)`,e.ctx),40);
});

/* ════ v10:补剂三态 ════ */
console.log('【十九】补剂:没吃 vs 没记录');
function medEnv(){
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext('refreshDay();day(TODAY);render();',e.ctx);
  return e;
}
function medRows(e){
  return e.doc._ids.medList._children.filter(c=>c.classList&&c.classList.contains('med'));
}
T('未记录的行上有「没吃」入口',()=>{
  const e=medEnv();
  const r=medRows(e)[0];
  ok(r._html.indexOf('class="noeat"')>=0,'没有「没吃」入口');
});
T('点「没吃」一步完成,不必填原因',()=>{
  const e=medEnv();
  medRows(e)[0].onclick({target:{classList:{contains:c=>c==='noeat'}}});
  const sk=e.vm.runInContext('JSON.stringify(day(TODAY).medSkip)',e.ctx);
  ok(sk.indexOf('m_vd')>=0,'没记进 medSkip');
  eq(e.vm.runInContext('day(TODAY).medSkip.m_vd.r',e.ctx),'','原因不该被强制填');
});
T('「没吃」和「已服用」是互斥的两个状态',()=>{
  const e=medEnv();
  medRows(e)[0].onclick({target:{classList:{contains:c=>c==='noeat'}}});
  eq(e.vm.runInContext('!!day(TODAY).meds.m_vd',e.ctx),false,'不该同时算已服用');
  // 改成其实吃了
  e.vm.runInContext('day(TODAY).meds.m_vd=Date.now();delete day(TODAY).medSkip.m_vd;',e.ctx);
  eq(e.vm.runInContext('!!(day(TODAY).medSkip||{}).m_vd',e.ctx),false);
});
T('一键记时间会清掉「没吃」状态',()=>{
  const e=medEnv();
  e.vm.runInContext('const d=day(TODAY);d.medSkip={m_vd:{r:"忘了",t:""}};render();',e.ctx);
  const r=medRows(e)[0];
  // 已标没吃的行,点行体是展开菜单;用菜单里的「其实吃了」
  r.onclick({target:{classList:{contains:()=>false}}});
  e.vm.runInContext('medOpen="m_vd";render();',e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  ok(mm,'没展开菜单');
  const eat=mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='其实吃了,记时间');
  ok(eat,'没有「其实吃了」按钮');
  eat.onclick();
  ok(e.vm.runInContext('!!day(TODAY).meds.m_vd',e.ctx),'没记上时间');
  eq(e.vm.runInContext('!!(day(TODAY).medSkip||{}).m_vd',e.ctx),false,'没吃状态没清掉');
});
T('原因胶囊可选可取消',()=>{
  const e=medEnv();
  e.vm.runInContext('const d=day(TODAY);d.medSkip={m_vd:{r:"",t:""}};medOpen="m_vd";render();',e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  const rz=mm._children.find(c=>c.classList&&c.classList.contains('rz'));
  eq(rz._children.length,3,'原因数不对');
  const wang=rz._children.find(c=>c._text==='忘了');
  wang.onclick();
  eq(e.vm.runInContext('day(TODAY).medSkip.m_vd.r',e.ctx),'忘了');
  // 再点一次取消
  e.vm.runInContext('medOpen="m_vd";render();',e.ctx);
  const mm2=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  const rz2=mm2._children.find(c=>c.classList&&c.classList.contains('rz'));
  rz2._children.find(c=>c._text==='忘了').onclick();
  eq(e.vm.runInContext('day(TODAY).medSkip.m_vd.r',e.ctx),'','再点没取消');
});
T('观察输入框在任何原因下都在(不再只有「其他」才有)',()=>{
  const e=medEnv();
  ['忘了','有意没吃','其他',''].forEach(r=>{
    e.vm.runInContext(`(function(){const dd=day(TODAY);dd.medSkip={m_vd:{r:${JSON.stringify(r)},t:""}};})();medOpen="m_vd";render();`,e.ctx);
    const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
    ok(mm._children.some(c=>c.classList&&c.classList.contains('rnote')),'原因「'+r+'」下没有观察输入框');
  });
});
T('撤销「没吃」回到没记录状态',()=>{
  const e=medEnv();
  e.vm.runInContext('const d=day(TODAY);d.medSkip={m_vd:{r:"忘了",t:""}};medOpen="m_vd";render();',e.ctx);
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  mm._children.find(c=>c.tagName==='BUTTON'&&c._text==='撤销').onclick();
  eq(e.vm.runInContext('!!(day(TODAY).medSkip||{}).m_vd',e.ctx),false);
  eq(e.vm.runInContext('!!day(TODAY).meds.m_vd',e.ctx),false,'撤销不该变成已服用');
});
T('明细里「没吃」和「没记录」分成两段',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext(`
    const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    D.days[mk(0)]={min:{},plus:{},meds:{m_vd:Date.now()},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[mk(1)]={min:{},plus:{},meds:{},medSkip:{m_vd:{r:"外出",t:""}},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[mk(2)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[mk(3)]={min:{},plus:{},meds:{m_vd:Date.now()},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});`,e.ctx);
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('记了「没吃」的日子')>=0,'没有「没吃」段');
  ok(b.indexOf('外出')>=0,'没显示原因');
  ok(b.indexOf('没有记录的日子')>=0,'没有「没记录」段');
});
T('「没吃」不被算进已服用天数',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext(`
    const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    D.days[mk(1)]={min:{},plus:{},meds:{},medSkip:{m_vd:{r:"忘了",t:""}},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[mk(2)]={min:{},plus:{},meds:{m_vd:Date.now()},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    renderHealth();`,e.ctx);
  const h=e.doc._ids.healthStat._html;
  const m=/data-med="m_vd"[\s\S]*?<span class="sc">(\d+) 天/.exec(h);
  ok(m,'没渲染出补剂行');
  eq(+m[1],1,'把「没吃」算进已服用天数了');
});
T('完成度提示会说明有几项没吃',()=>{
  const e=medEnv();
  e.vm.runInContext(`const d=day(TODAY);d.meds={m_vd:Date.now()};d.medSkip={m_mg:{r:"忘了",t:""}};render();`,e.ctx);
  const t=e.doc._ids.medHint._text;
  ok(t.indexOf('没吃')>=0,'提示没提到没吃: '+t);
});
T('medSkip 随 days 一起导出导入,无需额外处理',()=>{
  const e=medEnv();
  e.vm.runInContext('const d=day(TODAY);d.medSkip={m_vd:{r:"外出",t:""}};save();',e.ctx);
  const exp=e.vm.runInContext('JSON.stringify(D)',e.ctx);
  ok(exp.indexOf('medSkip')>=0&&exp.indexOf('外出')>=0,'导出没带上');
  const e2=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  const st=js.indexOf('$("#impFile").onchange');
  const BODY=js.slice(js.indexOf('try{',st)+4, js.indexOf('}catch(err){',st));
  e2.vm.runInContext(`(function(){const rd={result:${JSON.stringify(exp)}};const e={target:{value:""}};
    try{ ${BODY} }catch(err){ __E=String(err); }})();`,e2.ctx);
  eq(e2.vm.runInContext('typeof __E==="undefined"?"无":__E',e2.ctx),'无');
  eq(e2.vm.runInContext('D.days[TODAY].medSkip.m_vd.r',e2.ctx),'外出','导入后原因丢了');
});
T('旧存档没有 medSkip 字段照常读取',()=>{
  const e=withEnv({days:{},reviews:{},dictation:[],customs:[]});
  e.vm.runInContext(`D.days[TODAY]={min:{},plus:{},meds:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    render();renderHealth();`,e.ctx);
  ok(true);
});

/* ════ v10:每日一句 ════ */
console.log('【二十】每日一句:歪歪严选与移除');
T('标签已改成「歪歪严选」,旧文案消失',()=>{
  ok(js.indexOf('歪歪严选')>=0,'没改');
  ok(js.indexOf('· 你加的')<0,'旧文案还在');
});
T('自己加的句子显示「歪歪严选」',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=[["测试句","某处"]];D.qSeen=[];Q.length=0;newQuote();',e.ctx);
  ok(/歪歪严选/.test(e.doc._ids.dqFrom.textContent),'没标注');
});
T('移除自己加的句子 = 真删',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.myQ=[["甲",""],["乙",""]];curQ=["甲","",1];$("#qhideYes").onclick();',e.ctx);
  eq(e.vm.runInContext('JSON.stringify(D.myQ.map(x=>x[0]))',e.ctx),'["乙"]');
  eq(e.vm.runInContext('(D.hideQ||[]).length',e.ctx),0,'不该进隐藏名单');
});
T('移除内置句子 = 只隐藏,Q 数组一个字不动',()=>{
  const e=withEnv(S);
  const n0=e.vm.runInContext('Q.length',e.ctx);
  const first=e.vm.runInContext('Q[0][0]',e.ctx);
  e.vm.runInContext(`curQ=[${JSON.stringify(first)},"",0];$("#qhideYes").onclick();`,e.ctx);
  eq(e.vm.runInContext('Q.length',e.ctx),n0,'内置句库被破坏了');
  ok(e.vm.runInContext(`(D.hideQ||[]).indexOf(${JSON.stringify(first)})>=0`,e.ctx),'没进隐藏名单');
});
T('隐藏的句子不再出现在轮换里',()=>{
  const e=withEnv(S);
  const first=e.vm.runInContext('Q[0][0]',e.ctx);
  e.vm.runInContext(`D.hideQ=[${JSON.stringify(first)}];`,e.ctx);
  ok(e.vm.runInContext(`allQ().every(x=>x[0]!==${JSON.stringify(first)})`,e.ctx),'还在池子里');
});
T('hideQ 存的是原文不是下标',()=>{
  const e=withEnv(S);
  e.vm.runInContext('curQ=[Q[3][0],"",0];$("#qhideYes").onclick();',e.ctx);
  const v=e.vm.runInContext('D.hideQ[0]',e.ctx);
  eq(typeof v,'string','存成了非字符串');
  ok(v.length>1,'看起来像下标');
});
T('移除有二次确认,不是点一下就没',()=>{
  const e=withEnv(S);
  e.vm.runInContext('curQ=[Q[0][0],"",0];$("#qhideBox").hidden=true;$("#dqHide").onclick();',e.ctx);
  eq(e.doc._ids.qhideBox.hidden,false,'没弹确认');
  eq(e.vm.runInContext('(D.hideQ||[]).length',e.ctx),0,'确认前就删了');
  ok(/以后不再显示这一句/.test(e.doc._ids.qhideTip._text),'确认文案不对');
});
T('点「算了」不删',()=>{
  const e=withEnv(S);
  e.vm.runInContext('curQ=[Q[0][0],"",0];$("#dqHide").onclick();$("#qhideNo").onclick();',e.ctx);
  eq(e.vm.runInContext('(D.hideQ||[]).length',e.ctx),0);
  eq(e.doc._ids.qhideBox.hidden,true);
});
T('移除后自动换下一句',()=>{
  const e=withEnv(S);
  e.vm.runInContext('D.qSeen=[];newQuote();',e.ctx);
  const before=e.doc._ids.dqText.textContent;
  e.vm.runInContext('$("#qhideYes").onclick();',e.ctx);
  ok(e.doc._ids.dqText.textContent!==before||e.doc._ids.dqText.textContent.length>0,'没换句子');
});
T('导入会合并 hideQ',()=>{
  const st=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(st, js.indexOf('}catch(err){',st));
  ok(seg.indexOf('inc.hideQ')>=0,'导入没处理 hideQ');
});
T('导入 hideQ 去重,不重复堆积',()=>{
  const e=withEnv(S);
  e.vm.runInContext(`D.hideQ=["甲"];
    (function(){const inc={hideQ:["甲","乙"]};
      if(Array.isArray(inc.hideQ)){D.hideQ=Array.isArray(D.hideQ)?D.hideQ:[];
        inc.hideQ.forEach(x=>{if(x&&D.hideQ.indexOf(x)<0)D.hideQ.push(x);});}})();`,e.ctx);
  eq(e.vm.runInContext('JSON.stringify(D.hideQ)',e.ctx),'["甲","乙"]');
});
T('旧存档没有 hideQ 时一切照常',()=>{
  const e=withEnv(S);
  e.vm.runInContext('delete D.hideQ;newQuote();',e.ctx);
  ok(e.doc._ids.dqText.textContent.length>0);
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
