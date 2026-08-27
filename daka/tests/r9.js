const fs=require('fs'),vm=require('vm'),{build}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};
function boot(hour){
  const doc=build('app.html');const store={};
  // 需要造「现在几点」的场景时,把 Date 换成偏移过的
  let DateC=Date;
  if(hour!==undefined){
    const now=new Date();now.setHours(hour,30,0,0);
    const off=now.getTime()-Date.now();
    DateC=function(...a){return a.length?new Date(...a):new Date(Date.now()+off);};
    DateC.now=()=>Date.now()+off;
    DateC.prototype=Date.prototype;
  }
  const g={document:doc,localStorage:{getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:()=>1,clearInterval:()=>{},requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date:DateC,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  vm.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();day(TODAY);',ctx);
  return {doc,ctx,R:c=>vm.runInContext(c,ctx)};
}
// 造本周内的天:用 wkey 校验,避免跨周边界时假失败
const thisWeek=n=>`(function(){const mk=i=>{const t=studyNow();t.setDate(t.getDate()-i);return dkey(t);};
  const out=[];for(let i=0;i<14&&out.length<${n};i++){const k=mk(i);
    if(wkey(new Date(k+"T12:00:00"))!==TW)continue;out.push(k);}return out;})()`;

console.log('【1 · 每周复盘自动带数字】');
T('本周没记录时如实说,不硬凑',()=>{
  // render() 会把今天建成空壳,空壳不该被当成「这天有记录」
  const e=boot();e.R('D.days={};render();');
  ok(e.doc._ids.rvSum._html.indexOf('还没有记录')>=0,'空周文案不对: '+e.doc._ids.rvSum._html.slice(0,80));
});
T('只有空壳的一天不算有记录',()=>{
  const e=boot();e.R('D.days={};day(TODAY);');
  eq(e.R('weekFacts().days'),0,'空壳被算成一天了');
  e.R('day(TODAY).pomo=1;');
  eq(e.R('weekFacts().days'),1,'有内容却没算');
});
T('数得出达标天数',()=>{
  const e=boot();
  e.R(`const ks=${thisWeek(3)};ks.forEach(k=>{D.days[k]={min:{},plus:{},meds:{},medSkip:{},
    set:[{k:"a",n:"单词",s:"英语",mins:20}],pset:[],feel:{b:[],m:[],note:""},pomo:0,min:{a:1}};});`);
  ok(e.R('weekFacts().full')>=1,'一天都没数出来');
});
T('全勾的一天算作达标',()=>{
  const e=boot();
  // TW 是 const 改不了,而周一时本周只有今天一天 ——
  // 所以拆成两个单日断言,任何星期几都成立
  e.R(`const ks=${thisWeek(1)};D.days[ks[0]]={min:{a:1},plus:{},meds:{},medSkip:{},
    set:[{k:"a",n:"x",s:"英语",mins:20}],pset:[],feel:{b:[],m:[],note:""},pomo:0};`);
  const w=JSON.parse(e.R('JSON.stringify(weekFacts())'));
  eq(w.full,1,'全勾天数不对');
  eq(w.part,0,'不该算进部分完成');
});
T('只做了加码的一天算作部分完成',()=>{
  const e=boot();
  e.R(`const ks=${thisWeek(1)};D.days[ks[0]]={min:{},plus:{p:1},meds:{},medSkip:{},
    set:[{k:"a",n:"x",s:"英语",mins:20}],pset:[{k:"p",n:"y",s:"英语",mins:20}],
    feel:{b:[],m:[],note:""},pomo:0};`);
  const w=JSON.parse(e.R('JSON.stringify(weekFacts())'));
  eq(w.full,0,'不该算全勾');
  eq(w.part,1,'部分完成天数不对');
});
T('番茄和奶茶也进这张表',()=>{
  const e=boot();
  const n=JSON.parse(e.R(`JSON.stringify(${thisWeek(3)})`)).length;
  e.R(`const ks=${thisWeek(3)};ks.forEach(k=>{D.days[k]={min:{},plus:{},meds:{},medSkip:{},
    set:[],pset:[],feel:{b:[],m:[],note:""},pomo:2,drink:{tea:1}};});`);
  const w=JSON.parse(e.R('JSON.stringify(weekFacts())'));
  eq(w.pomo,n*2,'番茄合计不对');eq(w.tea,n,'奶茶合计不对');
});
T('心绪低落天数带进来',()=>{
  const e=boot();
  const n=JSON.parse(e.R(`JSON.stringify(${thisWeek(3)})`)).length;
  e.R(`const ks=${thisWeek(3)};ks.forEach(k=>{D.days[k]={min:{},plus:{},meds:{},medSkip:{},
    set:[],pset:[],feel:{b:[],m:["心情低落"],note:""},pomo:0};});`);
  const w=JSON.parse(e.R('JSON.stringify(weekFacts())'));
  eq(w.feelDay,n,'记了感受的天数不对');eq(w.low,n,'心绪低落天数不对');
});
T('上周的记录不会算进本周',()=>{
  const e=boot();
  e.R(`(function(){const t=studyNow();t.setDate(t.getDate()-10);
    D.days[dkey(t)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:9};})();`);
  eq(e.R('weekFacts().pomo'),0,'把上周的算进来了');
});
T('渲染出表格且带最久没碰',()=>{
  const e=boot();
  e.R('D.minList=[{k:"x1",n:"现代汉语 1 节",s:"现代汉语",mins:25}];');
  e.R(`const ks=${thisWeek(2)};ks.forEach(k=>{D.days[k]={min:{},plus:{},meds:{},medSkip:{},
    set:[],pset:[],feel:{b:[],m:[],note:""},pomo:1};});`);
  e.R('renderRvSum();');
  const h=e.doc._ids.rvSum._html;
  ok(h.indexOf('本周到今天为止')>=0,'没有标题');
  ok(h.indexOf('达标天数')>=0,'没有达标行');
  ok(h.indexOf('最久没碰')>=0,'没有久未触碰行');
  ok(h.indexOf('还没开始')>=0,'零记录的科目没标出来');
});
T('第一题会被自动填上',()=>{
  const e=boot();
  // 两个坑:一是本周天数随星期几变化,期望值要动态算;
  // 二是 render() 会把「今天」的清单快照刷成当前 minAll(),
  // 所以种子必须用真实存在的任务 key,不能自己编一个。
  const n=JSON.parse(e.R(`JSON.stringify(${thisWeek(3)})`)).length;
  e.R(`const kk=minAll().filter(x=>!x.off).map(x=>x.k);
    const ks=${thisWeek(3)};ks.forEach(k=>{
      const m={};kk.forEach(x=>{m[x]=1;});
      D.days[k]={min:m,plus:{},meds:{},medSkip:{},
        set:snap(minAll()),pset:[],feel:{b:[],m:[],note:""},pomo:0};});`);
  e.R('rvFilled=false;render();');
  eq(e.doc._ids.r1.value,String(n),'没自动填');
});
T('已经写过的复盘不会被覆盖',()=>{
  const e=boot();
  e.R(`const kk=minAll().filter(x=>!x.off).map(x=>x.k);
    const ks=${thisWeek(2)};ks.forEach(k=>{const m={};kk.forEach(x=>{m[x]=1;});
      D.days[k]={min:m,plus:{},meds:{},medSkip:{},set:snap(minAll()),pset:[],
        feel:{b:[],m:[],note:""},pomo:0};});
    D.reviews={};D.reviews[TW]=["我自己写的","","",""];rvFilled=false;render();`);
  eq(e.doc._ids.r1.value,'我自己写的','把她写的冲掉了');
});
T('自动填只发生一次,后续 render 不覆盖',()=>{
  const e=boot();
  e.R(`const kk=minAll().filter(x=>!x.off).map(x=>x.k);
    const ks=${thisWeek(2)};ks.forEach(k=>{const m={};kk.forEach(x=>{m[x]=1;});
      D.days[k]={min:m,plus:{},meds:{},medSkip:{},set:snap(minAll()),pset:[],
        feel:{b:[],m:[],note:""},pomo:0};});
    rvFilled=false;render();`);
  e.doc._ids.r1.value='3';
  e.R('render();render();');
  eq(e.doc._ids.r1.value,'3','又被填回去了');
});
T('这一段的文案不越界、不责备',()=>{
  const e=boot();
  e.R(`const ks=${thisWeek(1)};D.days[ks[0]]={min:{},plus:{},meds:{},medSkip:{},
    set:[],pset:[],feel:{b:[],m:[],note:""},pomo:1};renderRvSum();`);
  const h=e.doc._ids.rvSum._html;
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>
    ok(h.indexOf(w)<0,'出现禁用词: '+w));
  ok(h.indexOf('它答不了')>=0,'没有讲清工具的边界');
});

console.log('【2 · 换行与排版】');
T('补记那一行:说明独占一行',()=>{
  ok(/\.fixrow \.flab\{width:100%/.test(html),'flab 没有独占一行');
});
T('补记那一行:两个按钮不换行、不被挤走',()=>{
  const seg=html.slice(html.indexOf('.fixrow button{'));
  ok(seg.slice(0,200).indexOf('white-space:nowrap')>=0,'按钮会折行');
  ok(seg.slice(0,200).indexOf('flex:none')>=0,'按钮会被压缩');
});
T('番茄时长:标签独占一行,四档等分',()=>{
  ok(/\.tlab\{width:100%/.test(html),'标签没独占一行');
  ok(html.indexOf('.trow .chip{flex:1 1 auto')>=0,'四档没有等分');
});
T('计时器拆成上下两块,不靠 wrap 决定',()=>{
  ok(html.indexOf('<div class="tline">')>=0,'没有时钟行');
  ok(html.indexOf('<div class="tbtns">')>=0,'没有按钮行');
  ok(html.indexOf('.timer{background:#fff;border:1px solid var(--line);padding:13px 15px}')>=0,
    'timer 还在用 flex-wrap');
});
T('计时器按钮仍然能用',()=>{
  const e=boot();e.R('render();');
  ok(e.doc._ids.tStart,'开始按钮没了');
  ok(e.doc._ids.tReset,'归零按钮没了');
  ok(e.doc._ids.tCnt,'计数没了');
});
T('考研节点条分成两行',()=>{
  const e=boot();e.R('render();');
  const h=e.doc._ids.kyToggle._html;
  ok(h.indexOf('class="k1"')>=0,'没有第一行');
  ok(h.indexOf('class="k2"')>=0,'没有第二行');
  ok(/\.kyline \.k1,\.kyline \.k2\{display:block/.test(html),'两行没有各自成块');
});
T('节点条的展开字样独立定位,不参与断行',()=>{
  ok(/\.kyline \.k3\{position:absolute/.test(html),'展开字样还在文字流里');
  const e=boot();e.R('render();');
  ok(e.doc._ids.kyToggle._html.indexOf('class="k3"')>=0,'没有展开标记');
});
T('节点条内容仍然正确',()=>{
  const e=boot();e.R('render();');
  const t=e.doc._ids.kyToggle.textContent;
  ok(/距初试 \d+ 天/.test(t),'没有距初试: '+t);
  ok(t.indexOf('下一个:')>=0,'没有下一个节点');
});
T('糖冰五档用栅格锁一行',()=>{
  ok(/\.dtlbox \.dz\.g5\{display:grid/.test(html),'没有栅格');
  ok(html.indexOf('grid-template-columns:19px repeat(5,1fr)')>=0,'不是五等分');
  const e=boot();e.R('day(TODAY).drink={tea:1};drinkOpen="tea";render();');
  const panel=e.doc._ids.drinkBox._children.find(c=>c.classList&&c.classList.contains('dtlbox'));
  const g5=panel._children.filter(c=>c.classList&&c.classList.contains('g5'));
  eq(g5.length,2,'糖冰两排没有都用栅格');
});
T('选杯那一排不用栅格(杯数不固定)',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:3};drinkOpen="tea";render();');
  const panel=e.doc._ids.drinkBox._children.find(c=>c.classList&&c.classList.contains('dtlbox'));
  const rows=panel._children.filter(c=>c.classList&&c.classList.contains('dz'));
  eq(rows[0].classList.contains('g5'),false,'选杯行不该用五等分');
});
T('参考值那一行不折行',()=>{
  ok(/\.drinkref\{[^}]*flex-wrap:nowrap/.test(html.replace(/\n/g,'')),'参考值行会折');
});
T('补剂编辑行:输入框伸缩,按钮不掉队',()=>{
  ok(html.indexOf('.addrow2 .whenin{flex:1 1 auto')>=0,'输入框不伸缩');
  ok(html.indexOf('.addrow2 button{flex:none;white-space:nowrap}')>=0,'按钮会被挤走');
});

console.log('【3 · 夜里自动切】');
T('默认不开,不动原有行为',()=>{
  const e=boot(14);
  eq(!!e.R('D.autoDark'),false,'默认就开着');
  e.R('applyPrefs();');
  eq(!!e.R('D.dark'),false,'默认状态被改了');
});
T('白天开启:切到日间',()=>{
  const e=boot(14);
  e.R('D.dark=1;D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(e.R('D.dark'),0,'白天没切回日间');
});
T('夜里开启:切到夜间',()=>{
  const e=boot(23);
  e.R('D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(e.R('D.dark'),1,'23 点没切夜间');
});
T('凌晨算夜里,早六点算白天',()=>{
  const a=boot(2);a.R('D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(a.R('D.dark'),1,'凌晨 2 点该是夜间');
  const b=boot(6);b.R('D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(b.R('D.dark'),0,'早 6 点该是日间');
  const c=boot(22);c.R('D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(c.R('D.dark'),0,'22 点还不该切');
});
T('手动切过之后,同一时段内不会被改回去',()=>{
  const e=boot(23);
  e.R('D.autoDark=1;D.autoDarkMark=null;applyPrefs();');
  eq(e.R('D.dark'),1);
  e.doc._ids.btnDark.onclick();          // 半夜手动切回日间
  eq(e.R('D.dark'),false,'手动切没生效');
  e.R('applyPrefs();applyPrefs();');
  eq(!!e.R('D.dark'),false,'被自动改回去了');
});
T('关掉开关后完全不干预',()=>{
  const e=boot(23);
  e.R('D.autoDark=0;D.dark=0;applyPrefs();applyPrefs();');
  eq(!!e.R('D.dark'),false,'关掉了还在自动切');
});
T('开关按钮能开也能关',()=>{
  const e=boot(14);
  e.doc._ids.btnAutoDark.onclick();
  eq(e.R('D.autoDark'),1,'点不开');
  ok(e.doc._ids.btnAutoDark.className.indexOf('on')>=0,'按钮没高亮');
  e.doc._ids.btnAutoDark.onclick();
  eq(e.R('D.autoDark'),0,'点不关');
});
T('用真实时钟判断,不受学习日 4 点分界影响',()=>{
  const e=boot(2);
  eq(e.R('nightNow()'),1,'凌晨 2 点该算夜里');
  ok(js.indexOf('const h=new Date().getHours();')>=0,'没有用真实时钟');
});
T('页面开着不动也会到点自己切',()=>{
  ok(/setInterval\(\(\)=>\{[\s\S]{0,180}nightNow\(\)/.test(js),'没有定时复查');
});
T('导入会把开关带过来',()=>{
  // v19 起导入先写暂存区 T
  ok(js.indexOf('if(inc.autoDark!==undefined)T.autoDark=inc.autoDark;')>=0,'没迁移');
});

console.log('【4 · 不回归】');
T('四栏一起画不抛错',()=>{
  const e=boot();
  e.R(`day(TODAY).drink={tea:2};setDtl(TODAY,"tea",0,"s","半糖");
    day(TODAY).feel={b:["手脚冰凉"],m:["伤春悲秋"],note:""};
    D.autoDark=1;
    render();renderHealth();renderTimeStat();renderRvSum();renderExpWarn();kyOpen=true;renderKy();`);
  ok(e.doc._ids.rvSum._html.length>20);
  ok(e.doc._ids.cheerText._text.length>0);
});
T('版本号格式正确且只有一处',()=>{
  const m=html.match(/id="verTag">(v\d+ · \d{2}\/\d{2})</);
  ok(m,'顶部没有合法的版本号');
  eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
