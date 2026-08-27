const fs=require('fs'),vm=require('vm'),{build,FileReaderStub}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};
const tick=()=>new Promise(r=>setImmediate(r));

/* fakeIso 造「现在几点」,st.off 可以在会话中间继续推进(跨 DAYCUT / 计时用)。
   seedRaw 走真实的 localStorage 键,用来测「关掉页面再打开」。 */
function boot(fakeIso,seedRaw){
  const doc=build('app.html');const store={};
  if(seedRaw!==undefined)store['bnu-tracker-v1']=seedRaw;
  const st={off:fakeIso?(new Date(fakeIso).getTime()-Date.now()):0};
  const DateC=function(...a){return a.length?new Date(...a):new Date(Date.now()+st.off);};
  DateC.now=()=>Date.now()+st.off;DateC.prototype=Date.prototype;
  const timers=[];let seq=1;
  const g={document:doc,localStorage:{getItem:k=>store[k]===undefined?null:store[k],
      setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:f=>{const id=seq++;timers.push({id,fn:f});return id;},
    clearInterval:id=>{const i=timers.findIndex(t=>t.id===id);if(i>=0)timers.splice(i,1);},
    requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date:DateC,Math,JSON,String,Number,Object,Array,RegExp,Error,
    isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,
    Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise,FileReader:FileReaderStub};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  return {doc,ctx,g,st,store,timers,R:c=>vm.runInContext(c,ctx)};
}
// 没有种子存档时的常规起点:和别的套路一致,直接给一个干净的 D
function fresh(fakeIso){
  const e=boot(fakeIso);
  e.R('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();day(TODAY);');
  return e;
}
// 面板里的按钮是 appendChild 出来的,不在 innerHTML 里(见 README 第 5 条)
const chips=e=>{
  const out=[];
  const walk=n=>{if(n.classList&&n.classList.contains('mkchip'))out.push(n);
    (n._children||[]).forEach(walk);};
  (e.doc._ids.mkList._children||[]).forEach(walk);
  return out;
};
const inputs=e=>{
  const out=[];
  const walk=n=>{if(n.tagName==='INPUT')out.push(n);(n._children||[]).forEach(walk);};
  (e.doc._ids.mkList._children||[]).forEach(walk);
  return out;
};
const deepText=e=>{
  let t='';
  const walk=n=>{t+=' '+(n._text||'')+' '+(n._html||'');(n._children||[]).forEach(walk);};
  (e.doc._ids.mkList._children||[]).forEach(walk);
  return t;
};
// 点一个按钮 = 一次点击。返回点击次数,方便数录入负担
const tap=(e,txt)=>{
  const c=chips(e).find(x=>x._text===txt);
  if(!c)throw new Error('找不到按钮「'+txt+'」,当前有: '+chips(e).map(x=>x._text).join('/'));
  c.onclick();
  return c;
};
const openMk=e=>{
  e.R('render();');
  if(e.R('mkOpen'))return 0;          // 已经开着就不用再点
  e.doc._ids.mkToggle.onclick();
  return 1;
};
// 走完整路径记一条(计时版),返回点击次数
function record(e,sub,year,scope,minutes){
  let n=openMk(e);                 // 展开(已经开着就是 0 次)
  tap(e,sub);n++;
  tap(e,String(year));n++;
  tap(e,scope);n++;
  tap(e,'开始计时');n++;
  e.st.off+=minutes*60*1000;       // 这段时间真的过去了
  tap(e,'完成');n++;
  return n;
}
const recs=e=>JSON.parse(e.R('JSON.stringify((D.mockExam&&D.mockExam.records)||[])'));

(async()=>{

console.log('【1 · 记一条真题】');
T('英语一整套:年份题型都记住了',()=>{
  const e=fresh();
  record(e,'英语一',2024,'整套',180);
  const r=recs(e);
  eq(r.length,1,'没记进去');
  eq(r[0].subject,'en1');eq(r[0].year,2024);eq(r[0].scope,'full');
  eq(r[0].mins,180,'用时不对');
});
T('英语一阅读也能记',()=>{
  const e=fresh();
  record(e,'英语一',2022,'阅读',67);
  const r=recs(e);
  eq(r[0].scope,'read');eq(r[0].mins,67);
});
T('政治一整套',()=>{
  const e=fresh();
  record(e,'政治一',2023,'整套',150);
  eq(recs(e)[0].subject,'pol1');eq(recs(e)[0].scope,'full');
});
T('政治一多选',()=>{
  const e=fresh();
  record(e,'政治一',2021,'多选',25);
  eq(recs(e)[0].scope,'multi');eq(recs(e)[0].mins,25);
});
T('英语一的题型是七项,政治一是四项',()=>{
  const e=fresh();
  eq(e.R('MOCK_SUBJECTS.en1.sc.length'),7);
  eq(e.R('MOCK_SUBJECTS.pol1.sc.length'),4);
  eq(e.R('JSON.stringify(MOCK_SUBJECTS.pol1.sc.map(x=>x.n))'),'["整套","单选","多选","分析题"]');
});
T('年份是 2015 到 2026',()=>{
  const e=fresh();
  const y=JSON.parse(e.R('JSON.stringify(MOCK_YEARS)'));
  eq(y.length,12);eq(y[0],2026);eq(y[11],2015);
});
T('记完之后会告诉她记下了什么',()=>{
  const e=fresh();
  record(e,'英语一',2024,'翻译',20);
  ok(deepText(e).indexOf('记下了')>=0,'没有回执: '+deepText(e).slice(0,120));
  ok(deepText(e).indexOf('2024 英语一 · 翻译')>=0,'回执没说清是哪一份');
});
T('每条记录都带创建时间和唯一 id',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',30);
  const r=recs(e)[0];
  ok(r.id&&String(r.id).length>3,'没有 id');
  ok(Number(r.ts)>0,'没有创建时间');
  eq(r.done,true,'完成状态不对');
});

console.log('【2 · 刷次系统自己算】');
T('第一次是第 1 刷',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  eq(recs(e)[0].attempt,1);
});
T('同年份同题型第二次自动变第 2 刷',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'英语一',2024,'阅读',35);
  eq(recs(e)[1].attempt,2,'第二次没变 2 刷');
});
T('连着三刷,刷次是 1→2→3',()=>{
  const e=fresh();
  record(e,'英语一',2020,'完形',15);
  record(e,'英语一',2020,'完形',12);
  record(e,'英语一',2020,'完形',10);
  eq(JSON.stringify(recs(e).map(r=>r.attempt)),'[1,2,3]','刷次不连续');
});
T('换了年份就重新从第 1 刷开始',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'英语一',2023,'阅读',40);
  eq(recs(e)[1].attempt,1,'不同年份不该接着数');
});
T('换了题型也重新从第 1 刷开始',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'英语一',2024,'翻译',20);
  eq(recs(e)[1].attempt,1,'不同题型不该接着数');
});
T('英语和政治各数各的',()=>{
  const e=fresh();
  record(e,'英语一',2024,'整套',180);
  record(e,'政治一',2024,'整套',150);
  eq(recs(e)[1].attempt,1,'两门课的刷次混在一起了');
});
T('刷次是保存那一刻固化的数字,不是每次重算的公式',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'英语一',2024,'阅读',30);
  // 把第一条删掉,第二条仍然是「第 2 刷」—— 历史不因为后来的删改而变
  e.R('D.mockExam.records.splice(0,1);');
  eq(recs(e)[0].attempt,2,'刷次被重算了,历史不再不可变');
});
T('面板上把第几刷显示出来,不用她自己数',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'阅读');
  ok(deepText(e).indexOf('第 2 刷')>=0,'没显示刷次: '+deepText(e).slice(0,150));
});

console.log('【3 · 计时与手动填】');
T('计时得出的分钟数按真实经过的时间算',()=>{
  const e=fresh('2026-08-20T14:00:00');
  record(e,'英语一',2024,'阅读',67);
  eq(recs(e)[0].mins,67,'计时算错');
});
T('政治真题计时同样准',()=>{
  const e=fresh('2026-08-20T14:00:00');
  record(e,'政治一',2019,'分析题',33);
  eq(recs(e)[0].mins,33);
});
{
  const e=fresh('2026-08-20T14:00:00');
  openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'整套');tap(e,'开始计时');
  const raw=e.store['bnu-tracker-v1'];
  const e2=boot('2026-08-20T14:20:00',raw);
  await e2.R('load()');
  e2.R('render();');
  T('计时中途关掉页面再打开,计时还在走',()=>{
    ok(raw&&raw.indexOf('"run"')>=0,'计时状态没存进去');
    ok(e2.R('!!mockRun()'),'重开之后计时丢了');
    ok(e2.doc._ids.mkToggle.textContent.indexOf('正在计时')>=0,'条上没提示还在计时');
  });
  T('重开之后按完成,记的是从头到现在的时间',()=>{
    e2.R('mkOpen=true;renderMock();');
    tap(e2,'完成');
    eq(recs(e2).length,1,'没记上');
    eq(recs(e2)[0].mins,20,'跨页面的计时算错了');
  });
}
T('手动填用时,不用计时也能记',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');tap(e,'2018');tap(e,'新题型');
  const inp=inputs(e).find(x=>x.classList.contains('mkmin'));
  ok(inp,'没有填用时的输入框');
  inp.value='18';inp.onchange();
  tap(e,'记下来');
  eq(recs(e).length,1,'没记进去');
  eq(recs(e)[0].mins,18);
});
T('用时填空或填 0 时不会记一条空记录',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');tap(e,'2018');tap(e,'新题型');
  tap(e,'记下来');
  eq(recs(e).length,0,'空的也记进去了');
  const inp=inputs(e).find(x=>x.classList.contains('mkmin'));
  inp.value='0';inp.onchange();tap(e,'记下来');
  eq(recs(e).length,0,'0 分钟也记进去了');
});
T('取消:计时中放弃,什么都不留下',()=>{
  const e=fresh('2026-08-20T14:00:00');
  openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'阅读');tap(e,'开始计时');
  e.st.off+=10*60*1000;
  tap(e,'取消');
  eq(recs(e).length,0,'取消了还记了一条');
  eq(e.R('!!mockRun()'),false,'计时没停');
  ok(deepText(e).indexOf('没有记下来')>=0,'没说清这次没记');
});
T('计时忘了结束、隔了一整天才回来,不会记成十几个小时',()=>{
  const e=fresh('2026-08-20T14:00:00');
  openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'整套');tap(e,'开始计时');
  e.st.off+=20*3600*1000;
  e.R('renderMock();');
  eq(e.R('!!mockRun()'),false,'超时的计时还留着');
  eq(recs(e).length,0,'编了一个数存进去');
  ok(deepText(e).indexOf('没有记进去')>=0,'没有说清楚');
});
T('得分可填可不填 —— 不填照样能记',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  eq(recs(e)[0].score,null,'不填时不该编一个分数');
});
T('填了得分就存下来',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'阅读');
  const sc=inputs(e).find(x=>x.classList.contains('mksc'));
  ok(sc,'没有得分输入框');
  sc.value='34';sc.onchange();
  const mi=inputs(e).find(x=>x.classList.contains('mkmin'));
  mi.value='40';mi.onchange();
  tap(e,'记下来');
  eq(recs(e)[0].score,34,'得分没存住');
});
T('得分是 0 分也如实存,不当成没填',()=>{
  const e=fresh();
  e.R('mockAdd("en1",2024,"read",30,"0");');
  eq(recs(e)[0].score,0,'0 分被吞了');
});

console.log('【4 · 归属哪一天:走 DAYCUT，不用 new Date】');
T('凌晨 3:30 记的,算前一天',()=>{
  const e=fresh('2026-08-20T03:30:00');
  record(e,'英语一',2024,'阅读',10);   // 3:40 结束,还没过 4 点
  eq(recs(e)[0].d,'2026-08-19','凌晨被记成了新的一天');
});
T('3:55 开始、4:20 结束的一节,归到按下完成的那一天',()=>{
  // 和番茄一致:所有记录以「确认的那一刻」归属学习日,不按开始时刻算
  const e=fresh('2026-08-20T03:55:00');
  record(e,'英语一',2024,'阅读',25);
  eq(recs(e)[0].d,'2026-08-20','跨过 4 点的一节归错了天');
});
T('凌晨 4:30 记的,算新的一天',()=>{
  const e=fresh('2026-08-20T04:30:00');
  record(e,'英语一',2024,'阅读',40);
  eq(recs(e)[0].d,'2026-08-20','过了 4 点还算前一天');
});
T('日期在保存那一刻固化在记录上,不靠时间戳事后反推',()=>{
  const e=fresh('2026-08-20T23:30:00');
  record(e,'英语一',2024,'阅读',40);
  const r=recs(e)[0];
  eq(r.d,'2026-08-20');
  ok(/^\d{4}-\d{2}-\d{2}$/.test(r.d),'存的不是学习日键');
  ok(js.indexOf('const dk=refreshDay();')>=0,'mockAdd 没有走 refreshDay 取学习日');
});
T('跨过 4 点之后再记,进的是新的一天',()=>{
  const e=fresh('2026-08-20T03:00:00');
  record(e,'英语一',2024,'阅读',10);
  e.st.off+=2*3600*1000;          // 推进到 05:00
  record(e,'英语一',2024,'翻译',10);
  const r=recs(e);
  eq(r[0].d,'2026-08-19');
  eq(r[1].d,'2026-08-20','跨了 4 点还写在旧的一天');
});

console.log('【5 · 并入现有统计,不另起一套】');
T('今日时长把真题算进去了',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',67);
  eq(e.R('todayMins(TODAY)'),67,'真题时长没进今日时长');
});
T('真题时长只被算一次',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',67);
  eq(e.R('todayMins(TODAY)'),67,'第一次就翻倍了');
  eq(e.R('todayMins(TODAY)'),67,'多算了一次');
  e.R('render();renderTimeStat();');
  eq(e.R('todayMins(TODAY)'),67,'渲染之后被重复累加');
  eq(recs(e).length,1,'一次操作产生了两条记录');
});
T('真题和最低目标的时长相加,互不吞没',()=>{
  const e=fresh();
  e.R('const d=day(TODAY);const t=minAll()[0];d.min[t.k]=1;');
  const base=e.R('todayMins(TODAY)');
  ok(base>0,'底子就是 0,这条测不出东西');
  record(e,'英语一',2024,'翻译',20);
  eq(e.R('todayMins(TODAY)'),base+20,'两边没有相加');
});
T('没有真题记录时,今日时长和从前一模一样',()=>{
  const e=fresh();
  e.R('const d=day(TODAY);const t=minAll()[0];d.min[t.k]=1;');
  const a=e.R('todayMins(TODAY)');
  e.R('D.mockExam={version:1,records:[],metadata:{}};');
  eq(e.R('todayMins(TODAY)'),a,'空记录也改动了旧口径');
});
T('别的日子的真题不会算进今天',()=>{
  const e=fresh();
  e.R('mockAdd("en1",2024,"read",40);D.mockExam.records[0].d="2026-01-01";');
  eq(e.R('todayMins(TODAY)'),0,'别的天算进今天了');
});
T('本机没打开过的那一天(比如从别的设备导入的)也算得出时长',()=>{
  const e=fresh();
  e.R('mockAdd("en1",2024,"read",40);D.mockExam.records[0].d="2026-01-01";');
  eq(e.R('!!D.days["2026-01-01"]'),false,'这天在 D.days 里不该有行');
  eq(e.R('todayMins("2026-01-01")'),40,'D.days 里没有行就把时长吞了');
});
T('各科上次记录能看到真题,归到既有的「真题」分类',()=>{
  const e=fresh();
  e.R(`(function(){const t=studyNow();t.setDate(t.getDate()-2);
    const rec=mockAdd("en1",2024,"read",40);rec.d=dkey(t);})();`);
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  ok(r['真题'],'做过真题却被当成没碰过');
  eq(r['真题'].gap,2,'间隔天数不对');
});
T('没有新建第二个分类概念',()=>{
  const e=fresh();
  eq(e.R('MOCK_SUBJ'),'真题');
  ok(e.R('SUBJECTS_BUILTIN.indexOf(MOCK_SUBJ)>=0'),'用的不是内置分类');
});
T('只做了真题的一天,不算空壳日',()=>{
  const e=fresh();
  eq(e.R('weekFacts().days'),0,'一开始就不是 0');
  e.R('mockAdd("en1",2024,"read",40);');
  eq(e.R('weekFacts().days'),1,'只做真题的一天被当成没记录');
});
T('本周学习时长把真题带进去了',()=>{
  const e=fresh();
  e.R('mockAdd("en1",2024,"read",45);');
  eq(e.R('weekFacts().mins'),45,'周合计漏了真题');
});
T('按科目排行里出现真题,且不按年份题型拆成一堆',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'英语一',2023,'翻译',20);
  record(e,'政治一',2024,'多选',15);
  e.R('render();');
  const rows=e.doc._ids.statList._children.map(r=>r._html||'').join('|');
  ok(rows.indexOf('英语一真题')>=0,'排行里没有英语真题');
  ok(rows.indexOf('政治一真题')>=0,'排行里没有政治真题');
  eq((rows.match(/英语一真题/g)||[]).length,1,'英语真题被拆成了多行');
});
T('这四处用的都是同一份记录,没有第二套统计函数',()=>{
  ['mockTodayMins','mockStudyStats','mockStat','mockSubjLastSeen'].forEach(n=>
    ok(js.indexOf('function '+n)<0,'出现了第二套统计函数: '+n));
  ok(js.indexOf('let t=mockMins(k);')>=0,'todayMins 没接进来');
  ok(js.indexOf('mockRecords().forEach(r=>{if(r&&r.d)put(MOCK_SUBJ,r.d);});')>=0,
    'subjLastSeen 没接进来');
});
T('统计函数不会顺手改数据',()=>{
  const e=fresh();
  e.R('D.mockExam={version:1,records:[],metadata:{}};');
  const before=e.R('JSON.stringify(D.mockExam)');
  e.R('todayMins(TODAY);subjLastSeen();weekFacts();');
  eq(e.R('JSON.stringify(D.mockExam)'),before,'读一下就把数据改了');
});

console.log('【6 · 存得住 · 关掉再打开还在】');
T('记完就写进存档',()=>{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  const raw=JSON.parse(e.store['bnu-tracker-v1']);
  eq(raw.mockExam.records.length,1,'没落盘');
  eq(raw.mockExam.records[0].mins,40);
});
{
  const e=fresh();
  record(e,'英语一',2024,'阅读',40);
  record(e,'政治一',2023,'多选',15);
  const e2=boot(undefined,e.store['bnu-tracker-v1']);
  await e2.R('load()');
  e2.R('render();');
  T('关掉页面重新打开,记录还在',()=>{
    eq(e2.R('D.mockExam.records.length'),2,'重开之后记录少了');
    eq(e2.R('D.mockExam.records[0].mins'),40);
    ok(e2.doc._ids.mkToggle.textContent.indexOf('共 2 次')>=0,'条上没有摘要');
  });
  T('重开之后刷次接着往下数',()=>{
    eq(e2.R('mockAttempt("en1",2024,"read")'),2,'刷次没接上');
  });
}
{
  const e=boot(undefined,'{"days":{"2026-08-01":{"min":{},"pl');
  await e.R('load()');
  const before=e.store['bnu-tracker-v1'];
  e.R('mockAdd("en1",2024,"read",40);');
  await e.R('save()');
  T('存档损坏冻结期间不会偷偷写真题记录',()=>{
    ok(e.R('LOAD_BROKEN!==null'),'没有识别成损坏');
    eq(e.store['bnu-tracker-v1'],before,'冻结期间原文被覆盖了');
  });
}

console.log('【7 · 兜底与迁移:load 和导入同一套规则】');
{
  const e=boot(undefined,JSON.stringify({days:{},reviews:{},dictation:[],customs:["旧的"]}));
  await e.R('load()');
  e.R('render();');
  T('老存档没有 mockExam 也能正常打开',()=>{
    eq(e.R('typeof D.mockExam'),'object','没有给默认值');
    eq(e.R('D.mockExam.records.length'),0);
    eq(e.R('D.mockExam.version'),1);
    eq(e.R('JSON.stringify(D.customs)'),'["旧的"]','老数据被冲掉了');
    ok(e.doc._ids.mkToggle.textContent.indexOf('还没有记录')>=0,'条上文案不对');
  });
}
{
  const e=boot(undefined,JSON.stringify({days:{},mockExam:{records:"不是数组"}}));
  await e.R('load()');
  T('mockExam 是个残废对象时就地补齐,不整个丢掉',()=>{
    eq(e.R('Array.isArray(D.mockExam.records)'),true,'records 没兜住');
    eq(e.R('D.mockExam.records.length'),0);
  });
}
{
  const e=boot(undefined,JSON.stringify({days:{},mockExam:{records:[
    {id:"a",subject:"en1",year:2024,scope:"read",attempt:1,d:"2026-08-01",mins:40},
    null,
    {id:"b",subject:"化学",year:2024,scope:"read",attempt:1,d:"2026-08-01",mins:40},
    {id:"c",subject:"en1",year:2024,scope:"不存在的题型",attempt:1,d:"2026-08-01",mins:40},
    {id:"d",subject:"en1",year:2024,scope:"read",attempt:1,d:"八月一号",mins:40},
    {id:"e",subject:"en1",year:2024,scope:"read",attempt:1,d:"2026-08-01",mins:0}
  ]}}));
  await e.R('load()');
  T('读不懂的单条记录被丢掉,好的留下',()=>{
    eq(e.R('D.mockExam.records.length'),1,'该留的没留住,或该丢的没丢');
    eq(e.R('D.mockExam.records[0].id'),'a');
  });
}
T('load 和导入共用同一个兜底函数,不是各写一遍',()=>{
  ok(js.indexOf('function mockFix(box)')>=0,'没有共用函数');
  eq(js.split('function mockFix(').length-1,1,'兜底函数被写了不止一份');
  ok(js.indexOf('D.mockExam=mockFix(D.mockExam).box;')>=0,'load 没用它');
  const st=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(st,js.indexOf('D=T;',st));
  ok(seg.indexOf('mockFix(T.mockExam)')>=0&&seg.indexOf('mockFix(inc.mockExam)')>=0,
    '导入没用它');
});

console.log('【8 · 导入导出】');
const doImport=(e,text)=>{
  e.g.__f={__text:text};
  e.R('$("#impFile").onchange({target:{files:[__f],value:""}});');
};
{
  await (async()=>{
    const e=fresh();await tick();
    doImport(e,JSON.stringify({days:{"2026-08-12":{min:{},plus:{},pomo:2}},customs:["老的"]}));
    await tick();
    T('旧 JSON(没有 mockExam)照常导入',()=>{
      eq(e.R('D.days["2026-08-12"].pomo'),2,'旧数据没进来');
      eq(e.R('typeof D.mockExam'),'object','没有补出命名空间');
      eq(e.R('D.mockExam.records.length'),0);
      ok(e.doc._ids.bMsg._text.indexOf('导入完成')>=0,'没报成功');
      ok(e.doc._ids.bMsg._text.indexOf('真题')<0,'没有真题记录却提了真题');
    });
  })();

  await (async()=>{
    const e=fresh();
    record(e,'英语一',2024,'阅读',40);
    record(e,'政治一',2023,'多选',15);
    const dump=e.R('JSON.stringify(D)');
    await tick();
    T('导出的快照里带着真题记录',()=>{
      const o=JSON.parse(dump);
      eq(o.mockExam.records.length,2,'导出漏了真题');
      eq(o.mockExam.records[0].subject,'en1');
      ok(o.mockExam.metadata!==undefined,'metadata 没带上');
    });
    const e2=fresh();await tick();
    doImport(e2,dump);await tick();
    T('新 JSON 再导入,记录完好',()=>{
      eq(e2.R('D.mockExam.records.length'),2,'导入之后条数不对');
      eq(e2.R('D.mockExam.records[0].mins'),40);
      eq(e2.R('D.mockExam.records[1].scope'),'multi');
      eq(e2.R('D.mockExam.records[0].attempt'),1,'刷次没跟着过来');
    });
    T('导入之后真题时长照样进今日时长',()=>{
      eq(e2.R('todayMins(TODAY)'),55,'导入回来的记录不进统计');
    });
    doImport(e2,dump);await tick();
    T('同一份文件导入两次,记录不会变成两倍',()=>{
      eq(e2.R('D.mockExam.records.length'),2,'重复导入变多了');
    });
    T('重复导入之后时长也没有翻倍',()=>{
      eq(e2.R('todayMins(TODAY)'),55,'时长翻倍了');
    });
  })();

  await (async()=>{
    const e=fresh();
    record(e,'英语一',2024,'阅读',40);
    await tick();
    doImport(e,JSON.stringify({days:{},mockExam:{records:[
      {id:"new1",subject:"pol1",year:2022,scope:"single",attempt:1,d:"2026-08-10",mins:20}]}}));
    await tick();
    T('本机记录和导入文件的记录合并,谁也不吃掉谁',()=>{
      eq(e.R('D.mockExam.records.length'),2,'合并之后条数不对');
      ok(e.R('D.mockExam.records.some(r=>r.id==="new1")'),'导入的那条丢了');
      ok(e.R('D.mockExam.records.some(r=>r.subject==="en1")'),'本机那条被冲掉了');
    });
  })();

  await (async()=>{
    const e=fresh();await tick();
    doImport(e,JSON.stringify({days:{},mockExam:{records:[
      {id:"good",subject:"en1",year:2024,scope:"read",attempt:1,d:"2026-08-10",mins:40},
      {id:"bad",subject:"化学",year:2024,scope:"read",attempt:1,d:"2026-08-10",mins:40},
      null]}}));
    await tick();
    T('导入时读不懂的真题记录被跳过,并且如实报数',()=>{
      eq(e.R('D.mockExam.records.length'),1,'脏记录混进来了');
      ok(e.doc._ids.bMsg._text.indexOf('跳过读不懂的 2 条真题记录')>=0,
        '没如实报告: '+e.doc._ids.bMsg._text);
      ok(e.doc._ids.bMsg._text.indexOf('未发现异常')<0,'跳过了却说未发现异常');
    });
    T('天和记录分别计数,单位没有混在一起',()=>{
      ok(e.doc._ids.bMsg._text.indexOf('2 条真题记录')>=0);
      ok(e.doc._ids.bMsg._text.indexOf('2 天')<0,'把记录数说成了天数');
    });
  })();

  await (async()=>{
    const e=fresh();await tick();
    e.R('D.customs=["原有"];');
    const before=e.R('JSON.stringify(D)');
    doImport(e,'{"mockExam":{"records":[ 这不是 json');
    await tick();
    T('导入文件坏掉时,真题记录和别的数据一样一个字节都没动',()=>{
      eq(e.R('JSON.stringify(D)'),before,'D 被改了');
      ok(e.doc._ids.bMsg._text.indexOf('文件读不了')>=0,'没有提示');
    });
  })();

  T('导入路径里的真题合并写在暂存区 T 上,没有直接写 D',()=>{
    const i=js.indexOf('$("#impFile").onchange');
    const seg=js.slice(i,js.indexOf('D=T;',i));
    ok(/T\.mockExam=mbox;/.test(seg),'没有写在 T 上');
    ok(!/(^|[^.\w])D\.mockExam\s*=/.test(seg),'违反两阶段提交,直接写了 D.mockExam');
    const body=seg.slice(seg.indexOf('const T=JSON.parse'));
    const bad=body.split('\n').filter(l=>/(^|[^.\w])D\.\w+\s*=/.test(l));
    eq(bad.length,0,'仍有直接写 D 的行:\n'+bad.join('\n'));
  });

  console.log('【9 · 录入负担与文案】');
  T('记一篇阅读:从打开到记完是 6 次点击',()=>{
    const e=fresh('2026-08-20T22:00:00');
    const n=record(e,'英语一',2024,'阅读',67);
    eq(n,6,'点击次数变了');
    eq(recs(e).length,1,'点完了却没记上');
  });
  T('全程没有任何非填不可的东西',()=>{
    const e=fresh();
    openMk(e);tap(e,'英语一');tap(e,'2024');tap(e,'阅读');
    // 面板上只有「用时」和「得分」两个输入框,都可以空着走计时那条路
    const ins=inputs(e);
    eq(ins.length,2,'面板上的输入框数量变了: '+ins.map(x=>x.className).join(','));
    ok(ins.every(x=>!x.required),'出现了必填项');
    const txt=deepText(e);
    ['难度','错因','备注'].forEach(w=>ok(txt.indexOf(w)<0,'冒出了要填的东西: '+w));
    tap(e,'开始计时');tap(e,'完成');
    eq(recs(e).length,1,'什么都不填就记不下来');
  });
  T('每一层都能退回上一层',()=>{
    const e=fresh();
    openMk(e);tap(e,'英语一');
    tap(e,'返回');
    ok(chips(e).some(c=>c._text==='政治一'),'退不回选科目那层');
    tap(e,'英语一');tap(e,'2024');tap(e,'返回');
    ok(chips(e).some(c=>c._text==='2023'),'退不回选年份那层');
  });
  T('收起时只有一行,列表是藏起来的',()=>{
    const e=fresh();
    e.R('render();');
    eq(e.doc._ids.mkList.hidden,true,'默认就展开了');
    ok(e.doc._ids.mkToggle.textContent.length>0,'条上没有文字');
    ok(e.doc._ids.mkToggle._html.indexOf('class="k3"')>=0,'没有展开标记');
  });
  T('点一下展开,再点收起',()=>{
    const e=fresh();
    e.R('render();');
    e.doc._ids.mkToggle.onclick();
    eq(e.doc._ids.mkList.hidden,false,'没展开');
    e.doc._ids.mkToggle.onclick();
    eq(e.doc._ids.mkList.hidden,true,'没收起');
  });
  T('真题区没有禁用词,也没有 emoji',()=>{
    ok(js.indexOf('const MOCK_SUBJECTS=')>=0&&js.indexOf('function mockEmpty')>=0
      &&js.indexOf('function stdState')>=0,'锚点不在了,这条测试需要重新核实');
    const seg=js.slice(js.indexOf('const MOCK_SUBJECTS='),js.indexOf('const KY_NODES='))
      +js.slice(js.indexOf('function mockEmpty'),js.indexOf('function stdState'));
    ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>
      ok(seg.indexOf(w)<0,'出现禁用词: '+w));
    const bad=seg.match(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu);
    eq(bad?bad.join(''):'','','出现 emoji: '+(bad||[]).join(''));
  });
  T('这一阶段不碰难度,一个字都没有',()=>{
    const seg=js.slice(js.indexOf('const MOCK_SUBJECTS='),js.indexOf('const KY_NODES='))
      +js.slice(js.indexOf('function mockEmpty'),js.indexOf('function stdState'));
    ['difficultySnapshot','难度','平均分','建议','预测'].forEach(w=>
      ok(seg.indexOf(w)<0,'提前动了后面阶段的东西: '+w));
    const e=fresh();
    record(e,'英语一',2024,'阅读',40);
    const r=recs(e)[0];
    eq(Object.keys(r).sort().join(','),
      'attempt,d,done,id,mins,scope,score,subject,ts,year','记录字段和约定不一致');
  });
  T('没有引入网络请求',()=>{
    const seg=js.slice(js.indexOf('function mockEmpty'),js.indexOf('function stdState'));
    ['fetch(','XMLHttpRequest','http://','https://'].forEach(w=>
      ok(seg.indexOf(w)<0,'出现了网络依赖: '+w));
  });

  console.log('【10 · 不回归】');
  T('四栏一起画不抛错',()=>{
    const e=fresh();
    record(e,'英语一',2024,'阅读',40);
    e.R(`day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");
      day(TODAY).std={p_dic:1};
      day(TODAY).feel={b:["手脚冰凉"],m:["伤春悲秋"],note:""};
      render();renderHealth();renderTimeStat();renderRvSum();renderExpWarn();
      renderBroke();kyOpen=true;renderKy();mkOpen=true;renderMock();`);
    ok(e.doc._ids.cheerText._text.length>0);
    ok(e.doc._ids.rvSum._html.length>20);
  });
  T('三档结构和达标口径没被真题影响',()=>{
    const e=fresh();
    record(e,'英语一',2024,'整套',180);
    eq(e.R('stateOf(TODAY)'),'none','做了真题就被算成达标了');
    e.R('const d=day(TODAY);minAll().forEach(x=>{d.min[x.k]=1;});');
    eq(e.R('stateOf(TODAY)'),'full','达标判定被真题改动了');
  });
  T('轮换指针没受影响',()=>{
    const e=fresh();
    record(e,'英语一',2024,'阅读',40);
    ok(['gh','xh'].indexOf(e.R('D.rotGX'))>=0,'轮换指针被弄坏了');
    e.R('refreshDay();refreshDay();');
    ok(['gh','xh'].indexOf(e.R('D.rotGX'))>=0);
  });
  T('版本号格式正确且只有一处',()=>{
    const m=html.match(/id="verTag">(v\d+ · \d{2}\/\d{2})</);
    ok(m,'顶部没有合法的版本号');
    eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
  });

  console.log('\n通过 '+pass+' / 失败 '+fail);
  process.exit(fail?1:0);
}

})();
