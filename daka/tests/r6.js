const fs=require('fs'),vm=require('vm'),{build}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};
function boot(clock){
  const doc=build('app.html');const store={};
  let NOW=clock||Date.now();const timers=[];let seq=1;
  class FD extends Date{constructor(...a){a.length?super(...a):super(NOW);} static now(){return NOW;}}
  const g={document:doc,localStorage:{getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:f=>{const id=seq++;timers.push({id,fn:f});return id;},
    clearInterval:id=>{const i=timers.findIndex(t=>t.id===id);if(i>=0)timers.splice(i,1);},
    requestAnimationFrame:()=>0,navigator:{userAgent:'node'},console:{log(){},warn(){},error(){}},
    Date:FD,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  vm.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();',ctx);
  return {doc,R:c=>vm.runInContext(c,ctx),
    adv(ms,pump){if(pump){const st=250;let l=ms;while(l>0){const d=Math.min(st,l);NOW+=d;l-=d;timers.slice().forEach(t=>t.fn());}}else NOW+=ms;},
    reopen(){const d2=build('app.html');const g2=Object.assign({},g,{document:d2});
      g2.window=g2;g2.globalThis=g2;g2.self=g2;const tt=[];let q=1;
      g2.setInterval=f=>{const id=q++;tt.push({id,fn:f});return id;};
      g2.clearInterval=id=>{const i=tt.findIndex(x=>x.id===id);if(i>=0)tt.splice(i,1);};
      const c2=vm.createContext(g2);vm.runInContext(js,c2,{filename:'app.js'});
      return {doc:d2,R:c=>vm.runInContext(c,c2)};},
    timers};
}
const CLK=new Date(2026,7,15,14,0,0).getTime();

console.log('【1 · 歇一下】');
const RELAX=(()=>{const i=html.indexOf('const RELAX=[');const j=html.indexOf('\n];',i);
  return [...html.slice(i,j).matchAll(/"([^"]+)"/g)].map(m=>m[1]);})();
T('条数明显增加',()=>ok(RELAX.length>=30,'只有 '+RELAX.length+' 条'));
T('没有重复',()=>{const s2=new Set(RELAX);eq(s2.size,RELAX.length,'有重复');});
T('都是短句,适合学习间隙',()=>{
  const long=RELAX.filter(x=>x.length>40);
  eq(long.length,0,'过长: '+long.join(' | '));
});
T('原有 14 条一条没丢',()=>{
  ['闭眼十分钟,什么都不做。不是睡觉,就是闭着。','做十个深呼吸,吸四秒、屏七秒、呼八秒。',
   '抄一句你喜欢的词。辛弃疾「我见青山多妩媚」,或者随便哪句。']
    .forEach(x=>ok(RELAX.indexOf(x)>=0,'丢了: '+x));
});
T('随机逻辑没被改',()=>ok(/while\(RELAX\.length>1&&i===lastRelax\)/.test(js),'随机逻辑动了'));
T('能正常抽出来',()=>{
  const e=boot();e.R('newRelax();');
  ok(e.doc._ids.relaxText.textContent.length>0);
});

console.log('【2 · 字号】');
T('标准档 = 从前的「更大」17.5px',()=>{
  const m=/body\{[^}]*font-size:([\d.]+)px/.exec(html);
  eq(m[1],'17.5','标准档不是 17.5');
});
T('其余档位合理递进',()=>{
  const f1=+/body\.fs1\{font-size:([\d.]+)px/.exec(html)[1];
  const f2=+/body\.fs2\{font-size:([\d.]+)px/.exec(html)[1];
  ok(f1>17.5&&f2>f1,'递进不对: 17.5 / '+f1+' / '+f2);
  ok(f2-f1<=2&&f1-17.5<=2,'跨度过大');
});
T('原来选「更大」的会迁到标准,视觉零变化',()=>{
  const e=boot();
  e.R('D.fs=2;delete D.fsMigrated;migrateFs();');
  eq(e.R('D.fs'),0,'没迁移');
  eq(e.R('D.fsMigrated'),1);
});
T('只迁一次,之后手动改档不会被覆盖',()=>{
  const e=boot();
  e.R('D.fs=2;delete D.fsMigrated;migrateFs();D.fs=2;migrateFs();');
  eq(e.R('D.fs'),2,'第二次又被改回去了');
});
T('原来选标准/大的不受迁移影响',()=>{
  const e=boot();
  e.R('D.fs=1;delete D.fsMigrated;migrateFs();');
  eq(e.R('D.fs'),1);
});
T('迁移后 body class 正确',()=>{
  const e=boot();
  e.R('D.fs=2;delete D.fsMigrated;migrateFs();applyPrefs();');
  ok(e.doc.body.className.indexOf('fs2')<0,'还挂着 fs2');
});

console.log('【3 · 加码项第二轮】');
function plusRows(e){return e.doc._ids.plusList._children.filter(c=>c.classList&&c.classList.contains('task'));}
T('点一次 = 第一轮',()=>{
  const e=boot();e.R('render();');
  plusRows(e)[0].onclick({target:{classList:{contains:()=>false}}});
  eq(e.R('day(TODAY).plus.p_read'),1);
  eq(e.R('JSON.stringify(roundsOf(day(TODAY),"p_read",40))'),'[40]');
});
T('点「＋」= 第二轮,不覆盖第一轮',()=>{
  const e=boot();e.R('render();');
  plusRows(e)[0].onclick({target:{classList:{contains:()=>false}}});
  e.R('day(TODAY).rounds.p_read=[55];render();');   // 第一轮改成 55 分
  plusRows(e)[0].onclick({target:{classList:{contains:c=>c==='inc'}}});
  eq(e.R('day(TODAY).plus.p_read'),2);
  eq(e.R('JSON.stringify(day(TODAY).rounds.p_read)'),'[55,40]','第一轮被覆盖了');
});
T('可以连续多轮',()=>{
  const e=boot();e.R('render();');
  plusRows(e)[0].onclick({target:{classList:{contains:()=>false}}});
  for(let i=0;i<3;i++){e.R('render();');plusRows(e)[0].onclick({target:{classList:{contains:c=>c==='inc'}}});}
  eq(e.R('day(TODAY).plus.p_read'),4);
  eq(e.R('day(TODAY).rounds.p_read.length'),4);
});
T('每轮时长可分别填',()=>{
  const e=boot();e.R('render();');
  plusRows(e)[0].onclick({target:{classList:{contains:()=>false}}});
  e.R('render();');
  plusRows(e)[0].onclick({target:{classList:{contains:c=>c==='inc'}}});
  e.R('day(TODAY).rounds.p_read=[30,60];render();');
  const h=plusRows(e)[0]._html;
  eq((h.match(/class="ri"/g)||[]).length,2,'没有两个独立输入框');
  ok(h.indexOf('value="30"')>=0&&h.indexOf('value="60"')>=0,'两轮时长没分别显示');
});
T('轮次标记自解释:显示「第 N 轮」',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.plus.p_read=2;d.rounds={p_read:[40,40]};render();');
  const h=plusRows(e)[0]._html;
  ok(h.indexOf('第 2 轮')>=0,'没显示第 N 轮: '+h.slice(0,160));
  ok(h.indexOf('×2')<0,'旧的 ×N 还在');
});
T('总时长按各轮相加',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.plus.p_read=2;d.rounds={p_read:[30,60]};render();');
  ok(plusRows(e)[0]._html.indexOf('共 90 分')>=0,'总时长不对');
});
T('没有引入排序/队列/强制流程',()=>{
  ok(!/queue|强制|必须先/.test(js),'出现了流程约束');
});

console.log('【4 · 说明文字精简】');
T('四段长说明已删',()=>{
  ['同一件事做第二轮就点它','加码项没有先后顺序','自己加的项会留在常用里','每轮时长可以分别填']
    .forEach(x=>ok(html.indexOf(x)<0,'还留着: '+x));
});
T('核心提醒保留',()=>ok(html.indexOf('别让喜欢的科目')>=0,'提醒被删了'));
T('提醒是轻量样式,不是教程块',()=>{
  ok(/class="tip soft"/.test(html),'没用轻量样式');
  const seg=/\.tip\.soft\{([^}]*)\}/.exec(html)[1];
  ok(/opacity:\.?0?\.7/.test(seg)||/opacity:\.75/.test(seg),'权重没降低');
});
T('加码项功能没被说明改动波及',()=>{
  // v20 起「默写复盘」从加码梯挪进了标准线,默认加码项从 12 条变成 11 条
  const e=boot();e.R('render();');
  ok(plusRows(e).length>=11,'加码项列表异常');
  ok(plusRows(e)[0]._html.indexOf('class="mdim"')>=0,'默认时长显示丢了');
});

console.log('【5 · 番茄提前完成】');
T('正常完成:pomo+1,不计提前',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(25*60*1000+500,true);
  eq(e.R('day(TODAY).pomo'),1);
  eq(e.R('Number(day(TODAY).pomoEarly)||0'),0,'正常完成被算成提前了');
});
T('提前完成:算一节有效番茄,并记一笔提前',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(6*60*1000,true);
  e.R('$("#tDone").onclick();');
  eq(e.R('day(TODAY).pomo'),1,'没计入番茄');
  eq(e.R('day(TODAY).pomoEarly'),1,'没记提前');
  eq(e.R('!D.timer'),true,'计时没结束');
  eq(e.R('$("#tStart").textContent'),'开始');
});
T('提前完成后可以马上再开一轮',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(5*60*1000,true);
  e.R('$("#tDone").onclick();');
  eq(e.R('tRem'),25*60,'剩余时间没归位');
  e.R('startT();');
  e.adv(25*60*1000+500,true);
  eq(e.R('day(TODAY).pomo'),2,'第二节没计');
  eq(e.R('day(TODAY).pomoEarly'),1,'第二节被误记成提前');
});
T('暂停中也能提前完成',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(8*60*1000,true);
  e.R('$("#tStart").onclick();');       // 暂停
  ok(e.R('!!D.timerPause'));
  e.R('$("#tDone").onclick();');
  eq(e.R('day(TODAY).pomo'),1);
  eq(e.R('day(TODAY).pomoEarly'),1);
  eq(e.R('!D.timerPause'),true,'暂停记录没清');
});
T('没在计时时按钮是藏起来的',()=>{
  const e=boot(CLK);
  e.R('paint();');
  eq(e.doc._ids.tDone.hidden,true,'没计时也显示');
  e.R('TDUR=25*60;tRem=TDUR;startT();paint();');
  eq(e.doc._ids.tDone.hidden,false,'计时中却不显示');
});
T('归零不算番茄',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(5*60*1000,true);
  e.R('$("#tReset").onclick();');
  eq(e.R('Number(day(TODAY).pomo)||0'),0,'归零被算成完成');
  eq(e.R('Number(day(TODAY).pomoEarly)||0'),0);
});
T('跨页面回收的补记不受影响,也不算提前',()=>{
  const e=boot(CLK);
  e.R('TDUR=25*60;tRem=TDUR;startT();');
  e.adv(30*60*1000,false);
  const r=e.reopen();
  r.R('D='+e.R('JSON.stringify(D)')+';refreshDay();resumeT();');
  eq(r.R('day(TODAY).pomo'),1,'补记没生效');
  eq(r.R('Number(day(TODAY).pomoEarly)||0'),0,'补记被误算成提前');
});
T('手动 +1 不算提前',()=>{
  const e=boot(CLK);
  e.R('$("#tManual").onclick();');
  eq(e.R('day(TODAY).pomo'),1);
  eq(e.R('Number(day(TODAY).pomoEarly)||0'),0);
});
T('今日汇总如实标出提前次数',()=>{
  const e=boot(CLK);
  e.R('const d=day(TODAY);d.pomo=3;d.pomoEarly=2;renderTodaySum();');
  const h=e.doc._ids.tSum._html;
  ok(h.indexOf('番茄')>=0&&h.indexOf('3')>=0,'番茄数没显示');
  ok(h.indexOf('2 次提前完成')>=0,'没标出提前次数: '+h);
});
T('没有提前完成时不显示这行',()=>{
  const e=boot(CLK);
  e.R('const d=day(TODAY);d.pomo=2;d.pomoEarly=0;renderTodaySum();');
  ok(e.doc._ids.tSum._html.indexOf('提前完成')<0,'没提前也显示了');
});
T('旧存档没有 pomoEarly 字段不出错',()=>{
  const e=boot(CLK);
  e.R('D.days[TODAY]={min:{},plus:{},meds:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:2};renderTodaySum();render();');
  ok(e.doc._ids.tSum._html.indexOf('番茄')>=0);
});

console.log('【6 · 每日一句字号】');
T('正文回到 17px',()=>{
  const m=/\.daily \.dt\{[^}]*font-size:(\d+)px/.exec(html);
  eq(m[1],'17');
});
T('行高与留白保持',()=>{
  const seg=/\.daily \.dt\{([^}]*)\}/.exec(html)[1];
  ok(/line-height:2\.05/.test(seg),'行高被改了');
  ok(/max-width:\d+em/.test(seg),'宽度上限没了');
});
T('层级关系不变:仍是最大字号',()=>{
  const dt=+/\.daily \.dt\{[^}]*font-size:(\d+)px/.exec(html)[1];
  const da=parseFloat(/\.daily \.da\{[^}]*font-size:([\d.]+)px/.exec(html)[1]);
  const bt=parseFloat(/\.daily \.dops button\{[^}]*font-size:([\d.]+)px/.exec(html)[1]);
  ok(dt>da&&dt>bt,'层级乱了');
  ok(dt-da>=5,'与出处差距不足');
});
T('歪歪严选样式没动',()=>{
  const yy=/\.daily \.da \.yy\{([^}]*)\}/.exec(html)[1];
  ok(/font-size:10\.5px/.test(yy)&&/opacity:0?\.62/.test(yy));
});
T('操作文字仍是轻量且点击区够大',()=>{
  const seg=/\.daily \.dops button\{([^}]*)\}/.exec(html)[1];
  ok(/border:none/.test(seg));
  ok(+/padding:(\d+)px/.exec(seg)[1]>=12,'点击区缩了');
});
T('分行逻辑一个字没动',()=>{
  ok(/function quoteLines\(t\)/.test(js));
  const e=boot();
  eq(e.R('JSON.stringify(quoteLines("捐躯赴国难,视死忽如归"))'),'["捐躯赴国难,","视死忽如归"]');
  eq(e.R('quoteLines("有志者事竟成").length'),1);
});

console.log('【没动别的】');
T('每日一句随机与去重未改',()=>ok(/const QMEM=60/.test(js)&&/function qCat\(from,mine\)/.test(js)));
T('hideQ / 自定义句 / 删除确认未改',()=>{
  ok(/const hid=new Set\(D\.hideQ\|\|\[\]\)/.test(js));
  ok(/以后不再显示这一句/.test(js));
});
T('导入导出未动',()=>{
  const st=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(st,js.indexOf('}catch(err){',st));
  ['inc.myQ','inc.hideQ','inc.plusOrder','inc.plusOff','inc.subjects'].forEach(x=>ok(seg.indexOf(x)>=0,'丢了 '+x));
});
T('补剂三态未动',()=>ok(/medSkip/.test(js)&&/SKIP_REASONS/.test(js)));
T('任务排序未动',()=>ok(/function moveIn/.test(js)&&/function applyOrder/.test(js)));

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
