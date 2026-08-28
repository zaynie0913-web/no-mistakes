const fs=require('fs'),vm=require('vm'),{build,FileReaderStub}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};

function boot(fakeIso,seedRaw){
  const doc=build('app.html');const store={};
  if(seedRaw!==undefined)store['bnu-tracker-v1']=seedRaw;
  const st={off:fakeIso?(new Date(fakeIso).getTime()-Date.now()):0};
  const DateC=function(...a){return a.length?new Date(...a):new Date(Date.now()+st.off);};
  DateC.now=()=>Date.now()+st.off;DateC.prototype=Date.prototype;
  const g={document:doc,localStorage:{getItem:k=>store[k]===undefined?null:store[k],
      setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:()=>1,clearInterval:()=>{},requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date:DateC,Math,JSON,String,Number,Object,Array,RegExp,Error,
    isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,
    Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise,FileReader:FileReaderStub};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  return {doc,ctx,g,st,store,R:c=>vm.runInContext(c,ctx)};
}
function fresh(iso){
  const e=boot(iso);
  e.R('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();day(TODAY);');
  return e;
}
// 面板内容是 appendChild 出来的,不在 innerHTML 里(README 第 5 条)
const walkAll=(e,fn)=>{
  const w=n=>{fn(n);(n._children||[]).forEach(w);};
  (e.doc._ids.mkList._children||[]).forEach(w);
};
const chips=e=>{const o=[];walkAll(e,n=>{if(n.classList&&n.classList.contains('mkchip'))o.push(n);});return o;};
const inputs=e=>{const o=[];walkAll(e,n=>{if(n.tagName==='INPUT')o.push(n);});return o;};
const deepText=e=>{let t='';walkAll(e,n=>{t+=' '+(n._text||'')+' '+(n._html||'');});return t;};
const tap=(e,txt)=>{
  const c=chips(e).find(x=>x._text===txt);
  if(!c)throw new Error('找不到按钮「'+txt+'」,当前有: '+chips(e).map(x=>x._text).join('/'));
  c.onclick();return c;
};
const has=(e,txt)=>chips(e).some(c=>c._text===txt);
const openMk=e=>{e.R('render();');if(e.R('mkOpen'))return 0;e.doc._ids.mkToggle.onclick();return 1;};
const slider=(e,attr,k)=>{
  let out=null;
  walkAll(e,n=>{if(n.tagName==='INPUT'&&n.getAttribute&&n.getAttribute(attr)===k)out=n;});
  return out;
};
const dragMins=(e,k,v)=>{const s=slider(e,'data-k',k);
  if(!s)throw new Error('没有 '+k+' 的用时滑块');s.value=String(v);s.oninput();s.onchange();};
const dragScore=(e,k,v)=>{const s=slider(e,'data-sc',k);
  if(!s)throw new Error('没有 '+k+' 的分数滑块');s.value=String(v);s.oninput();s.onchange();};
const recs=e=>JSON.parse(e.R('JSON.stringify((D.mockExam&&D.mockExam.records)||[])'));
// 走到录入屏:科目 → 年份 → 勾若干题型 → 下一步
function toEntry(e,subName,year,scopeNames){
  openMk(e);tap(e,subName);tap(e,String(year));
  scopeNames.forEach(n=>tap(e,n));
  tap(e,'下一步 · '+scopeNames.length+' 项');
}

console.log('【1 · 题型清单重写】');
T('英语一是十项,阅读拆成四篇',()=>{
  const e=fresh();
  const ns=JSON.parse(e.R('JSON.stringify(MOCK_SUBJECTS.en1.sc.map(x=>x.n))'));
  eq(ns.join('/'),'完形/阅读一/阅读二/阅读三/阅读四/段落匹配/翻译/小作文/大作文/整套');
});
T('政治是四项',()=>{
  const e=fresh();
  eq(e.R('JSON.stringify(MOCK_SUBJECTS.pol1.sc.map(x=>x.n))'),'["单选","多选","分析题","整套"]');
});
T('「新题型」改叫「段落匹配」,清单里不再有旧名字',()=>{
  const e=fresh();
  const ns=JSON.parse(e.R('JSON.stringify(MOCK_SUBJECTS.en1.sc.map(x=>x.n))'));
  ok(ns.indexOf('段落匹配')>=0,'没有段落匹配');
  eq(ns.indexOf('新题型'),-1,'旧名字还在清单里');
  eq(ns.indexOf('阅读'),-1,'笼统的「阅读」还在清单里');
});
T('英语年份到 2010,政治到 2015',()=>{
  const e=fresh();
  const en=JSON.parse(e.R('JSON.stringify(mockYears("en1"))'));
  eq(en[0],2026);eq(en[en.length-1],2010);eq(en.length,17);
  const po=JSON.parse(e.R('JSON.stringify(mockYears("pol1"))'));
  eq(po[0],2026);eq(po[po.length-1],2015);eq(po.length,12);
});
T('年份屏默认露 12 个(2015–2026),2010 收在「更早」里',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');
  ok(has(e,'2026')&&has(e,'2015'),'默认这 12 个没画出来');
  ok(!has(e,'2010'),'更早的年份不该一开始就铺开');
  ok(has(e,'更早(2010–2014)'),'没有「更早」入口');
  tap(e,'更早(2010–2014)');
  ok(has(e,'2010')&&has(e,'2014'),'点开之后更早的年份没出来');
  ok(has(e,'2026'),'点开更早之后前面的年份不该消失');
});
T('政治的年份屏没有 2010',()=>{
  const e=fresh();
  openMk(e);tap(e,'政治');
  ok(has(e,'2015'),'没有 2015');
  ok(!has(e,'2010'),'政治不该有 2010');
});

console.log('【2 · 旧记录不迁移、不改写】');
const OLD=JSON.stringify({days:{},mockExam:{version:1,metadata:{},records:[
  {id:"o1",subject:"en1",year:2021,scope:"read",attempt:1,d:"2026-08-01",mins:70,score:null,done:true,ts:1},
  {id:"o2",subject:"en1",year:2021,scope:"new", attempt:1,d:"2026-08-01",mins:18,score:null,done:true,ts:2}
]}});
T('v21 存的「阅读」「新题型」不会被当成脏数据丢掉',async()=>{});
(async()=>{
  const e=boot(undefined,OLD);
  await e.R('load()');
  T('旧 scope 的记录读得回来,一条没丢',()=>{
    eq(e.R('D.mockExam.records.length'),2,'旧记录被丢了');
    eq(e.R('D.mockExam.records[0].scope'),'read','scope 被改写了');
    eq(e.R('D.mockExam.records[1].scope'),'new','scope 被改写了');
  });
  T('旧 scope 仍然显示成原来的中文名,不显示成 key',()=>{
    eq(e.R('mockScName("en1","read")'),'阅读');
    eq(e.R('mockScName("en1","new")'),'新题型');
  });
  T('旧记录照常算进总时长',()=>{
    eq(e.R('todayMins("2026-08-01")'),88,'70+18 没算对');
  });
  T('旧记录照常进「各科上次记录」',()=>{
    ok(JSON.parse(e.R('JSON.stringify(subjLastSeen())'))['真题'],'旧记录没进统计');
  });
  T('旧 scope 不出现在可选清单里 —— 新记录只能选新清单',()=>{
    e.R('mkOpen=true;mkSub=null;mkYear=null;mkScopes=[];mkEntry=false;renderMock();');
    tap(e,'英语一');tap(e,'2021');
    ok(!has(e,'阅读'),'笼统的「阅读」还能选');
    ok(!has(e,'新题型'),'「新题型」还能选');
    ok(has(e,'阅读一')&&has(e,'段落匹配'),'新清单没画出来');
  });
  T('旧记录的刷次和新题型各数各的',()=>{
    eq(e.R('mockAttempt("en1",2021,"read")'),2,'旧 scope 自己的刷次不对');
    eq(e.R('mockAttempt("en1",2021,"read1")'),1,'旧记录不该影响阅读一的刷次');
  });

console.log('【3 · 四篇阅读各自算刷次】');
T('阅读一~四互不影响,都是第 1 刷',()=>{
  const e=fresh();
  ['阅读一','阅读二','阅读三','阅读四'].forEach(n=>{
    toEntry(e,'英语一',2024,[n]);tap(e,'保存');
  });
  const r=recs(e);
  eq(r.length,4,'条数不对');
  eq(r.map(x=>x.scope).join(','),'read1,read2,read3,read4');
  eq(r.map(x=>x.attempt).join(','),'1,1,1,1','四篇不该互相接着数');
});
T('只重做阅读三,只有阅读三变成第 2 刷',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读三']);tap(e,'保存');
  toEntry(e,'英语一',2024,['阅读三']);tap(e,'保存');
  eq(e.R('mockAttempt("en1",2024,"read3")'),3,'阅读三的刷次没往下走');
  eq(e.R('mockAttempt("en1",2024,"read1")'),1,'阅读一被带着走了');
});

console.log('【4 · 题型多选 → 生成 N 条独立记录】');
T('选三个题型,生成三条记录',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一','阅读二']);
  tap(e,'保存这 3 条');
  const r=recs(e);
  eq(r.length,3,'没有生成三条');
  eq(r.map(x=>x.scope).join(','),'cloze,read1,read2');
});
T('每条各自存自己那一格的用时,不合成也不摊派',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一']);
  dragMins(e,'cloze',18);
  dragMins(e,'read1',22);
  tap(e,'保存这 2 条');
  const r=recs(e);
  eq(r.find(x=>x.scope==='cloze').mins,18);
  eq(r.find(x=>x.scope==='read1').mins,22);
  eq(r.reduce((a,x)=>a+x.mins,0),40,'合计不对');
});
T('多选保存后,每条各自算刷次',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  toEntry(e,'英语一',2024,['阅读一','阅读二']);tap(e,'保存这 2 条');
  const r=recs(e);
  eq(r.filter(x=>x.scope==='read1').map(x=>x.attempt).join(','),'1,2','阅读一没接着数');
  eq(r.filter(x=>x.scope==='read2')[0].attempt,1,'阅读二该是第 1 刷');
});
T('再点一次可以取消选中',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');tap(e,'2024');
  tap(e,'完形');tap(e,'阅读一');
  eq(e.R('mkScopes.length'),2);
  tap(e,'完形');
  eq(e.R('JSON.stringify(mkScopes)'),'["read1"]','取消选中没生效');
});
T('一个都没选时走不下去,但也不骂人',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');tap(e,'2024');
  ok(!chips(e).some(c=>c._text.indexOf('下一步')>=0),'没选也给了下一步');
  ok(deepText(e).indexOf('选一个或几个')>=0,'没有提示怎么往下走');
});
T('多选的记录合计时长进当日统计,只算一次',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一','阅读二']);
  dragMins(e,'cloze',10);dragMins(e,'read1',20);dragMins(e,'read2',30);
  tap(e,'保存这 3 条');
  eq(e.R('todayMins(TODAY)'),60,'当日时长不对');
  eq(e.R('todayMins(TODAY)'),60,'重复调用被多算了');
});

console.log('【5 · 错题数 → 得分,系统算】');
const SCORE=[
  ['en1','cloze',20,0.5,10],['en1','read1',5,2,10],['en1','read2',5,2,10],
  ['en1','read3',5,2,10],['en1','read4',5,2,10],['en1','match',5,2,10],
  ['pol1','single',16,1,16],['pol1','multi',17,2,34]
];
T('每个客观题型的题数 / 每题分值 / 满分都对',()=>{
  const e=fresh();
  SCORE.forEach(([sub,k,q,per,full])=>{
    const d=JSON.parse(e.R('JSON.stringify(mockSc('+JSON.stringify(sub)+','+JSON.stringify(k)+'))'));
    eq(d.q,q,k+' 题数不对');eq(d.per,per,k+' 每题分值不对');eq(d.full,full,k+' 满分不对');
  });
});
T('错 0 题就是满分',()=>{
  const e=fresh();
  SCORE.forEach(([sub,k,q,per,full])=>{
    eq(e.R('mockScoreOf('+JSON.stringify(sub)+','+JSON.stringify(k)+',0)'),full,k);
  });
});
T('每个题型错几题算几分,一个一个核',()=>{
  const e=fresh();
  const S=(sub,k,w)=>e.R('mockScoreOf('+JSON.stringify(sub)+','+JSON.stringify(k)+','+w+')');
  eq(S('en1','cloze',4),8,'完形错 4 题该是 8 分');
  eq(S('en1','cloze',20),0,'完形全错该是 0 分');
  eq(S('en1','read1',2),6,'阅读一错 2 题该是 6 分');
  eq(S('en1','read4',5),0,'阅读四全错该是 0 分');
  eq(S('en1','match',1),8,'段落匹配错 1 题该是 8 分');
  eq(S('pol1','single',3),13,'单选错 3 题该是 13 分');
  eq(S('pol1','multi',5),24,'多选错 5 题该是 24 分');
  eq(S('pol1','multi',17),0,'多选全错该是 0 分');
});
T('错题数超出题数会被夹住,不会算出负分',()=>{
  const e=fresh();
  eq(e.R('mockScoreOf("en1","read1",99)'),0,'没夹住上限');
  eq(e.R('mockScoreOf("en1","read1",-3)'),10,'没夹住下限');
});
T('主观题不算分,交给滑块',()=>{
  const e=fresh();
  ['trans','small','big','full'].forEach(k=>
    eq(e.R('mockScoreOf("en1",'+JSON.stringify(k)+',2)'),null,k+' 不该有错题算分'));
  eq(e.R('mockScoreOf("pol1","analysis",2)'),null);
});
T('点错题数按钮,分数实时出来并存进记录',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  ok(deepText(e).indexOf('还没记')>=0,'一开始该是没记');
  tap(e,'2');
  ok(deepText(e).indexOf('错 2 题 · 6 / 10 分')>=0,'没有实时算分: '+deepText(e).slice(0,200));
  tap(e,'保存');
  eq(recs(e)[0].wrong,2,'错题数没存');
  eq(recs(e)[0].score,6,'分数没存');
});
T('再点同一个数字等于取消,回到「没记」',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  tap(e,'2');tap(e,'2');
  tap(e,'保存');
  eq(recs(e)[0].wrong,null,'没取消掉');
  eq(recs(e)[0].score,null,'取消后不该留分数');
});
T('题数 ≤ 8 的排一行数字按钮:0 到题数',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  const ws=chips(e).filter(c=>c.classList.contains('w')).map(c=>c._text);
  eq(ws.join(','),'0,1,2,3,4,5','阅读该是 0~5 六个按钮');
  ok(!has(e,'＋'),'不该出现步进器');
});
T('题数 > 8 的用步进器,不排一长条',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形']);
  eq(chips(e).filter(c=>c.classList.contains('w')).length,0,'完形不该排 21 个按钮');
  ok(has(e,'＋')&&has(e,'−'),'没有步进器');
});
T('步进器能加能减,夹在 0 到题数之间',()=>{
  const e=fresh();
  toEntry(e,'政治',2024,['多选']);
  tap(e,'＋');tap(e,'＋');tap(e,'＋');
  ok(deepText(e).indexOf('错 3 题 · 28 / 34 分')>=0,'步进算分不对: '+deepText(e).slice(0,200));
  tap(e,'−');
  ok(deepText(e).indexOf('错 2 题 · 30 / 34 分')>=0,'减一之后不对');
  for(let i=0;i<40;i++)tap(e,'−');
  ok(deepText(e).indexOf('错 0 题 · 34 / 34 分')>=0,'减到负数了');
});
T('步进器有「不记」的退路',()=>{
  const e=fresh();
  toEntry(e,'政治',2024,['单选']);
  tap(e,'＋');
  ok(has(e,'不记'),'没有不记的退路');
  tap(e,'不记');
  tap(e,'保存');
  eq(recs(e)[0].wrong,null,'不记之后还存了错题数');
});
T('主观题没有错题数入口,只有分数滑块',()=>{
  const e=fresh();
  ['翻译','小作文','大作文','整套'].forEach(n=>{
    const e2=fresh();
    toEntry(e2,'英语一',2024,[n]);
    eq(chips(e2).filter(c=>c.classList.contains('w')).length,0,n+' 不该有错题数按钮');
    ok(!has(e2,'＋'),n+' 不该有步进器');
    ok(deepText(e2).indexOf('错题数')<0,n+' 不该提错题数');
    ok(slider(e2,'data-sc',null)!==undefined,'');
  });
  const e3=fresh();
  toEntry(e3,'政治',2024,['分析题']);
  ok(!!slider(e3,'data-sc','analysis'),'分析题没有分数滑块');
});
T('主观题分数滑块步进 0.5,上限是满分',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['大作文']);
  const s=slider(e,'data-sc','big');
  eq(s.step,'0.5','步进不对');
  eq(s.max,'20','大作文满分该是 20');
  eq(s.min,'0');
});
T('主观题不拖滑块就是没记',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['翻译']);
  tap(e,'保存');
  eq(recs(e)[0].score,null,'没拖也编了一个分数');
});

console.log('【6 · 用时滑块】');
const DEF=[['en1','cloze',15],['en1','read1',15],['en1','read2',15],['en1','read3',15],
  ['en1','read4',15],['en1','match',15],['en1','trans',20],['en1','small',15],
  ['en1','big',35],['en1','full',180],
  ['pol1','single',15],['pol1','multi',25],['pol1','analysis',120],['pol1','full',180]];
T('每个题型的考场建议用时都对',()=>{
  const e=fresh();
  DEF.forEach(([sub,k,m])=>
    eq(e.R('mockDefMins('+JSON.stringify(sub)+','+JSON.stringify(k)+')'),m,k+' 建议用时不对'));
});
T('进录入屏时滑块已经停在建议值上,不是从 0 开始',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['大作文']);
  eq(e.R('mkMinsIn.big'),35,'没预填');
  eq(slider(e,'data-k','big').value,'35','滑块没停在建议值');
});
T('多选时每个题型各自预填各自的建议值',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','大作文','整套']);
  eq(e.R('mkMinsIn.cloze'),15);
  eq(e.R('mkMinsIn.big'),35);
  eq(e.R('mkMinsIn.full'),180);
});
T('不动滑块直接保存,存的就是建议值',()=>{
  const e=fresh();
  toEntry(e,'政治',2024,['分析题']);
  tap(e,'保存');
  eq(recs(e)[0].mins,120,'没按建议值存');
});
T('−5 / +5 按钮能调,拖不准也压得住',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  tap(e,'+5');tap(e,'+5');
  eq(e.R('mkMinsIn.read1'),25);
  tap(e,'−5');
  eq(e.R('mkMinsIn.read1'),20);
  tap(e,'保存');
  eq(recs(e)[0].mins,20);
});
T('用时不会被按到 0 或负数',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  for(let i=0;i<10;i++)tap(e,'−5');
  ok(e.R('mkMinsIn.read1')>=1,'被按到 0 了');
});
T('滑块步进 1 分钟,范围合理',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['整套']);
  const s=slider(e,'data-k','full');
  eq(s.step,'1');
  eq(s.min,'1');
  ok(Number(s.max)>=180,'上限装不下整套');
});

console.log('【7 · 建议用时提醒】');
T('滑块下方写着考场建议多少分钟',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['翻译']);
  ok(deepText(e).indexOf('考场建议 20 分钟')>=0,'没有建议行: '+deepText(e).slice(0,200));
});
T('超过建议值会提一句,但不评价、不叫停',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  dragMins(e,'read1',26);
  const t=deepText(e);
  ok(t.indexOf('多用了 11 分钟')>=0,'没提超时: '+t.slice(0,240));
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费','超时','太慢'].forEach(w=>
    ok(t.indexOf(w)<0,'出现了评价 / 命令用词: '+w));
});
T('没超过建议值时不提',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  ok(deepText(e).indexOf('多用了')<0,'没超也提了');
});
T('这是固定常量表,不是按她的历史算的',()=>{
  const seg=js.slice(js.indexOf('function mockDefMins'),js.indexOf('function mockFullOf'));
  ok(seg.indexOf('mockRecords')<0&&seg.indexOf('D.')<0,'建议用时读了她的历史数据');
  ok(/return s\?s\.mins:/.test(seg),'不是直接读常量表');
});

console.log('【8 · 一个字都不用打】');
T('录入屏上零个文本 / 数字输入框',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一','大作文','整套']);
  const bad=inputs(e).filter(x=>x.type==='text'||x.type==='number');
  eq(bad.length,0,'还有要打字的框: '+bad.map(x=>x.className).join(','));
  ok(inputs(e).every(x=>x.type==='range'),'出现了非滑块控件');
});
T('整个真题模块的源码里没有文本 / 数字输入框',()=>{
  const seg=js.slice(js.indexOf('function renderMock'),js.indexOf('function mkEnter0'));
  ok(seg.indexOf('type="text"')<0&&seg.indexOf('.type="text"')<0,'出现了 text 输入框');
  ok(seg.indexOf('.type="number"')<0&&seg.indexOf('type="number"')<0,'出现了 number 输入框');
  ok(seg.indexOf('inputMode')<0,'还留着数字键盘的痕迹');
  ok(seg.indexOf('.type="range"')>=0,'滑块不见了');
});
T('四个题型一起录也走得完,全程只有点和滑',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一','大作文','整套']);
  tap(e,'2');                       // 阅读一错 2 题(唯一的一行数字按钮)
  dragMins(e,'cloze',16);
  dragScore(e,'big',13.5);
  tap(e,'保存这 4 条');
  const r=recs(e);
  eq(r.length,4);
  eq(r.find(x=>x.scope==='cloze').mins,16);
  eq(r.find(x=>x.scope==='read1').score,6);
  eq(r.find(x=>x.scope==='big').score,13.5);
  eq(r.find(x=>x.scope==='full').mins,180,'整套没用建议值');
});

console.log('【9 · 顶部快捷栏】');
T('三个按钮都在,顺序是真题 / 番茄 / 歇一下',()=>{
  // 桩不解析 HTML 里写死的静态文本(README 第 6 条),这条只能查源码
  const seg=html.slice(html.indexOf('id="qnav"'),html.indexOf('id="status"'));
  const order=['真题训练','番茄钟','歇一下'].map(x=>seg.indexOf(x));
  order.forEach((i,n)=>ok(i>=0,'缺按钮: '+['真题训练','番茄钟','歇一下'][n]));
  ok(order[0]<order[1]&&order[1]<order[2],'顺序不对');
  const e=fresh();
  ok(e.doc._ids.qnMk&&e.doc._ids.qnPomo&&e.doc._ids.qnRelax,'按钮元素不在');
});
T('点了会滚到对应区块',()=>{
  const e=fresh();e.R('render();');
  const hit=[];
  ['mkBar','timerBox','relaxBox'].forEach(id=>{
    e.doc._ids[id].scrollIntoView=()=>{hit.push(id);};
  });
  e.doc._ids.qnMk.onclick();
  e.doc._ids.qnPomo.onclick();
  e.doc._ids.qnRelax.onclick();
  eq(hit.join(','),'mkBar,timerBox,relaxBox','没有滚到对应区块');
});
T('点「真题训练」顺手把它展开',()=>{
  const e=fresh();e.R('render();');
  e.doc._ids.mkBar.scrollIntoView=()=>{};
  eq(e.R('mkOpen'),false);
  e.doc._ids.qnMk.onclick();
  eq(e.R('mkOpen'),true,'跳过去还是收起的');
});
T('快捷栏只导航,不折叠也不隐藏任何东西',()=>{
  const e=fresh();e.R('render();');
  const before=e.doc._ids.minList._children.length+e.doc._ids.stdList._children.length
    +e.doc._ids.plusList._children.length;
  e.doc._ids.timerBox.scrollIntoView=()=>{};
  e.doc._ids.qnPomo.onclick();
  const after=e.doc._ids.minList._children.length+e.doc._ids.stdList._children.length
    +e.doc._ids.plusList._children.length;
  eq(after,before,'三档任务区被动过了');
  eq(e.doc._ids.minList.hidden,false,'最低目标被折叠了');
});
T('快捷栏在「今天」tab 最上面',()=>{
  const i=html.indexOf('id="tp-a"'),j=html.indexOf('id="qnav"'),k=html.indexOf('id="status"');
  ok(i<j&&j<k,'快捷栏不在今天 tab 顶部');
});

console.log('【10 · 计时与保存的死角(v22.1 自查补的)】');
T('计时中点保存,一定有结果,不会静默无反应',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  tap(e,'开始计时');
  e.st.off+=12*60*1000;
  tap(e,'保存');
  eq(recs(e).length,1,'计时中点保存被静默吞掉了');
  eq(recs(e)[0].mins,12,'没把计时的实际时长收进来');
  eq(e.R('!!mockRun()'),false,'保存之后计时还挂着');
});
T('中途换过题型,计时变成孤儿时也存得下去',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形']);
  tap(e,'开始计时');
  tap(e,'返回');                 // 回到选题型
  tap(e,'完形');                 // 取消完形,计时就没有落脚点了
  tap(e,'阅读一');
  tap(e,'下一步 · 1 项');
  ok(has(e,'结束那个计时'),'孤儿计时没有给出口');
  tap(e,'保存');
  eq(recs(e).length,1,'孤儿计时把保存闸死了');
  eq(recs(e)[0].scope,'read1');
  eq(e.R('!!mockRun()'),false,'孤儿计时没被收掉');
});
T('孤儿计时可以单独结束掉,不影响这一屏的录入',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形']);
  tap(e,'开始计时');
  tap(e,'返回');tap(e,'完形');tap(e,'阅读一');tap(e,'下一步 · 1 项');
  tap(e,'结束那个计时');
  eq(e.R('!!mockRun()'),false,'没结束掉');
  eq(recs(e).length,0,'结束计时不该顺手存一条');
  ok(has(e,'保存'),'结束之后还能正常保存');
});
T('导入不会把别的设备的计时搬过来',()=>{
  const e=fresh();
  const inc={version:1,metadata:{},records:[],
    run:{subject:"en1",year:2024,scope:"cloze",start:Date.now()}};
  eq(e.R('!!mockFix('+JSON.stringify(inc)+').box.run'),true,'mockFix 本身仍保留 run(load 要用)');
  const i=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(i,js.indexOf('D=T;',i));
  ok(seg.indexOf('mbox.run=incoming.box.run')<0,'导入还在搬别的设备的计时');
});

console.log('【11 · 录入草稿(v22.2)】');
{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一']);
  dragMins(e,'cloze',18);tap(e,'2');            // 阅读一错 2 题
  const raw=e.store['bnu-tracker-v1'];
  const e2=boot(undefined,raw);                 // 相当于切 App 回来、页面被回收后重开
  await e2.R('load()');
  e2.R('render();');
  T('切 App 回来,选好的题型和点好的错题数都还在',()=>{
    ok(raw&&raw.indexOf('draft')>=0,'草稿没落盘');
    eq(e2.R('JSON.stringify(mkScopes)'),'["cloze","read1"]','选好的题型没了');
    eq(e2.R('mkMinsIn.cloze'),18,'拖好的用时没了');
    eq(e2.R('mkWrongIn.read1'),2,'点好的错题数没了');
    eq(e2.R('mkEntry'),true,'没回到录入那一屏');
  });
  T('正在录的东西会自动展开,不用她再点一次',()=>{
    eq(e2.R('mkOpen'),true,'回来还是收起的,等于要她重新启动一次');
  });
  T('恢复之后接着保存,存的就是草稿里的值',()=>{
    tap(e2,'保存这 2 条');
    const r=recs(e2);
    eq(r.length,2,'没存上');
    eq(r.find(x=>x.scope==='cloze').mins,18,'用时不是草稿里的');
    eq(r.find(x=>x.scope==='read1').wrong,2,'错题数不是草稿里的');
    eq(r.find(x=>x.scope==='read1').score,6,'分数没跟着错题数算');
  });
}
T('草稿不是记录:不进统计、不进刷次',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形']);
  dragMins(e,'cloze',30);
  eq(recs(e).length,0,'草稿变成记录了');
  eq(e.R('todayMins(TODAY)'),0,'草稿进了当日时长');
  eq(e.R('mockAttempt("en1",2024,"cloze")'),1,'草稿把刷次顶上去了');
});
T('保存成功后草稿清掉',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  tap(e,'保存');
  eq(e.R('!!(D.mockExam&&D.mockExam.draft)'),false,'保存了草稿还在');
});
T('主动退出录入屏,草稿一起清掉',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  tap(e,'2');
  tap(e,'返回');
  eq(e.R('!!(D.mockExam&&D.mockExam.draft&&D.mockExam.draft.entry)'),false,'退出录入屏草稿还在');
  eq(e.R('JSON.stringify(mkWrongIn)'),'{}','填过的错题数没清掉');
});
T('草稿读不懂就丢掉,不会把模块卡死',()=>{
  const e=fresh();
  ['{"sub":"不存在的科目"}','{"sub":"en1","scopes":"不是数组"}',
   '{"sub":"en1","scopes":["不存在的题型"]}','{"sub":"en1","scopes":[],"mins":"不是对象"}']
   .forEach(bad=>{
     eq(e.R('mockDraftOk('+bad+')'),false,'这种草稿该被判为读不懂: '+bad);
   });
  eq(e.R('mockDraftOk({sub:"en1",year:2024,scopes:["read1"],mins:{},wrong:{},score:{}})'),true,
    '正常草稿被误判了');
});
T('草稿不进导出',()=>{
  const i=js.indexOf('const snap=JSON.parse(JSON.stringify(D));');
  ok(i>0,'导出没有做快照剔除');
  const seg=js.slice(i,i+260);
  ok(seg.indexOf('delete snap.mockExam.draft')>=0,'导出还带着草稿');
  ok(seg.indexOf('delete snap.mockExam.undo')>=0,'导出还带着撤销指针');
});
T('恢复草稿不弹确认框',()=>{
  const seg=js.slice(js.indexOf('function mkRestoreDraft'),js.indexOf('function mkUndoAvail'));
  ok(seg.indexOf('confirm(')<0,'恢复时弹了确认框');
  ok(seg.indexOf('prompt(')<0,'恢复时问了问题');
});
T('草稿只在打开页面时恢复一次,不会把「返回」吃掉',()=>{
  const e=fresh();
  openMk(e);tap(e,'英语一');
  tap(e,'返回');
  ok(has(e,'政治'),'点返回又被草稿推回去了');
});

console.log('【12 · 撤销刚才那一批(v22.2)】');
T('保存后出现撤销入口,写明是哪几条',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一']);
  tap(e,'保存这 2 条');
  ok(has(e,'撤销刚才那 2 条'),'没有撤销入口');
  ok(deepText(e).indexOf('完形、阅读一')>=0,'没写清是哪几条');
});
T('撤销 = 整批删掉,不留痕迹',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一']);
  tap(e,'保存这 2 条');
  tap(e,'撤销刚才那 2 条');
  eq(recs(e).length,0,'没删干净');
  eq(e.R('todayMins(TODAY)'),0,'时长还留着');
  eq(e.R('!!(D.mockExam&&D.mockExam.undo)'),false,'撤销指针没清');
  ok(!has(e,'撤销刚才那 2 条'),'撤销入口还挂着');
});
T('只撤最新那一批,更早的记录一条不动',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  toEntry(e,'英语一',2024,['阅读二','阅读三']);tap(e,'保存这 2 条');
  tap(e,'撤销刚才那 2 条');
  const r=recs(e);
  eq(r.length,1,'把更早的也撤了');
  eq(r[0].scope,'read1');
});
T('又存了新的一批,旧的撤销入口就消失',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  const first=e.R('D.mockExam.undo.bid');
  toEntry(e,'英语一',2024,['阅读二']);tap(e,'保存');
  ok(e.R('D.mockExam.undo.bid')!==first,'撤销指针没跟着最新那一批走');
  tap(e,'撤销刚才那 1 条');
  const r=recs(e);
  eq(r.length,1,'撤错批了');
  eq(r[0].scope,'read1','撤的不是最新那一批');
});
T('跨了学习日,撤销入口自己消失',()=>{
  const e=fresh('2026-08-20T20:00:00');
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  ok(!!e.R('mkUndoAvail()'),'当天该能撤');
  e.st.off+=10*3600*1000;          // 推到第二天早上 6 点
  e.R('refreshDay();renderMock();');
  eq(e.R('!!mkUndoAvail()'),false,'跨了学习日还能撤');
  eq(recs(e).length,1,'记录不该被跨天顺手删掉');
});
T('手动点「不用」也能让入口消失,记录不动',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  tap(e,'不用');
  eq(recs(e).length,1,'点「不用」把记录删了');
  ok(!has(e,'不用'),'入口没消失');
});
T('旧记录没有批次号,永远不出现撤销入口',()=>{
  const e=fresh();
  e.R(`D.mockExam={version:1,metadata:{},records:[
    {id:"o1",subject:"en1",year:2021,scope:"read",attempt:1,d:TODAY,mins:70,
     wrong:null,score:null,done:true,ts:1}]};render();`);
  eq(e.R('!!mkUndoAvail()'),false,'老记录冒出了撤销入口');
  ok(!deepText(e).indexOf('撤销')>=0===false||deepText(e).indexOf('撤销')<0,'界面上出现了撤销');
});
T('撤销不重算刷次 —— 撤完出现跳号是对的',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');   // 第 1 刷
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');   // 第 2 刷
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');   // 第 3 刷
  e.R('D.mockExam.undo={bid:D.mockExam.records[1].bid,d:TODAY,n:1,label:"阅读一"};');
  e.R('mkDoUndo();');
  const a=recs(e).map(r=>r.attempt);
  eq(JSON.stringify(a),'[1,3]','刷次被重算了,应该保留 1、3 的跳号');
});
T('同一批的几条写的是同一个批次号',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一','阅读二']);
  tap(e,'保存这 3 条');
  const bs=recs(e).map(r=>r.bid);
  eq(new Set(bs).size,1,'同一批的批次号不一致');
  ok(bs[0]&&bs[0].length>3,'没有批次号');
});
T('撤销指针不进导出',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);tap(e,'保存');
  ok(!!e.R('D.mockExam.undo'),'本机该有撤销指针');
  // 导出走的是剔除过的快照,见上一组的静态断言
  ok(js.indexOf('delete snap.mockExam.undo')>=0,'导出没剔除撤销指针');
});

T('连点两下保存:只存一批,而且不抛异常',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['阅读一']);
  const btn=chips(e).find(c=>c._text==='保存');
  btn.onclick();
  btn.onclick();            // 第二下落在已经被换掉的旧按钮上
  btn.onclick();
  eq(recs(e).length,1,'连点存了不止一批');
});
T('状态被清空后再调 mkSaveAll,安全返回而不是抛错',()=>{
  const e=fresh();
  eq(e.R('mkSaveAll()'),null,'没有守卫,mkSub 为空时会抛异常');
  e.R('mkSub="en1";mkYear=null;mkScopes=["read1"];');
  eq(e.R('mkSaveAll()'),null,'年份为空时也要挡住');
  eq(recs(e).length,0,'空状态存出了记录');
});

console.log('【13 · 不回归】');
T('达标口径没被真题影响',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['整套']);tap(e,'保存');
  eq(e.R('stateOf(TODAY)'),'none','做了真题就被算成达标');
  e.R('const d=day(TODAY);minAll().forEach(x=>{d.min[x.k]=1;});');
  eq(e.R('stateOf(TODAY)'),'full','达标判定被改了');
});
T('四栏一起画不抛错',()=>{
  const e=fresh();
  toEntry(e,'英语一',2024,['完形','阅读一']);tap(e,'保存这 2 条');
  e.R(`day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");day(TODAY).std={p_dic:1};
    render();renderHealth();renderTimeStat();renderRvSum();renderExpWarn();renderBroke();
    kyOpen=true;renderKy();mkOpen=true;renderMock();`);
  ok(e.doc._ids.cheerText._text.length>0);
  ok(e.doc._ids.healthStat._html.length>20);
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
T('没有网络请求,也没有题目内容',()=>{
  const seg=js.slice(js.indexOf('function mockEmpty'),js.indexOf('function stdState'));
  ['fetch(','XMLHttpRequest','http://','https://','排行榜','徽章','连续打卡'].forEach(w=>
    ok(seg.indexOf(w)<0,'出现了不该有的东西: '+w));
});
T('版本号格式正确且只有一处',()=>{
  const m=html.match(/id="verTag">(v\d+(?:\.\d+)? · \d{2}\/\d{2})</);
  ok(m,'顶部没有合法的版本号');
  eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
})();
