const fs=require('fs'),vm=require('vm'),{build}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};

function boot(fakeIso){
  const doc=build('app.html');const store={};
  const st={off:fakeIso?(new Date(fakeIso).getTime()-Date.now()):0};
  const DateC=function(...a){return a.length?new Date(...a):new Date(Date.now()+st.off);};
  DateC.now=()=>Date.now()+st.off;DateC.prototype=Date.prototype;
  const g={document:doc,localStorage:{getItem:k=>store[k]===undefined?null:store[k],
      setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:()=>1,clearInterval:()=>{},requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date:DateC,Math,JSON,String,Number,Object,Array,RegExp,Error,
    isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,
    Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  vm.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();day(TODAY);',ctx);
  return {doc,ctx,st,R:c=>vm.runInContext(c,ctx)};
}
const mkDays=(n,fn)=>`(function(){const mk=i=>{const t=studyNow();t.setDate(t.getDate()-i);return dkey(t);};
  for(let i=1;i<=${n};i++){const k=mk(i);D.days[k]={min:{},plus:{},std:{},meds:{},medSkip:{},medNote:{},
    drink:{},dtl:{},set:[],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};(${fn})(D.days[k],i,k);}})()`;

console.log('【1 · 轮换指针】');
T('默认从古代汉语开始',()=>{
  const e=boot();
  eq(e.R('D.rotGX'),'gh');
});
T('今天最低线里没有现代汉语,也没有古今两门同时出现',()=>{
  const e=boot();
  const names=JSON.parse(e.R('JSON.stringify(minAll().map(x=>x.n))'));
  eq(names.filter(n=>n==='古代汉语'||n==='现代汉语').length,1,'古今两门同时出现或都不出现了');
});
T('没被选中的那门,整节课出现在标准线',()=>{
  const e=boot();
  const stdNames=JSON.parse(e.R('JSON.stringify(stdToday().map(x=>x.n))'));
  ok(stdNames.indexOf('现代汉语')>=0,'现代汉语没有被挪到标准线');
  ok(stdNames.indexOf('古代汉语')<0,'古代汉语不该同时出现在标准线');
});
T('同一个 key 不会在 min 和 std 里同时出现',()=>{
  const e=boot();
  const mk=JSON.parse(e.R('JSON.stringify(minAll().map(x=>x.k))'));
  const sk=JSON.parse(e.R('JSON.stringify(stdToday().map(x=>x.k))'));
  eq(mk.filter(k=>sk.indexOf(k)>=0).length,0,'min 和 std 撞 key 了');
});
T('日期真正翻篇才拨一格,同一天反复渲染不会拨动',()=>{
  const e=boot();
  const a=e.R('D.rotGX');
  e.R('refreshDay();refreshDay();refreshDay();');
  eq(e.R('D.rotGX'),a,'同一天内被拨动了');
});
T('翻到第二天,指针拨一格',()=>{
  const e=boot('2026-08-16T22:00:00');
  const a=e.R('D.rotGX');
  e.st.off+=7*3600*1000;    // 推进到第二天凌晨(过了 04:00 分界)
  e.R('refreshDay();');
  const b=e.R('D.rotGX');
  ok(a!==b,'跨天了指针没动');
});
T('断更三天回来,只拨一格,不会因为断了几天而多拨',()=>{
  const e=boot('2026-08-16T22:00:00');
  const a=e.R('D.rotGX');
  e.st.off+=3*24*3600*1000;   // 直接推进三天,中途从不调用 refreshDay
  e.R('refreshDay();');       // 只在“今天”这一次真正 render 的时候拨
  const b=e.R('D.rotGX');
  ok(a!==b,'完全没拨动');
  eq(e.R('D.rotGXDate'),e.R('TODAY'),'翻页日期没有对齐到今天');
  // 两个值只能是 gh/xh 之一,断三天不会把它拨出这个集合,也不会拨两格变回原值再拨一格
  ok(['gh','xh'].indexOf(b)>=0);
});
T('轮换指针老存档没有这两个字段时,load 会给一个干净起点',()=>{
  ok(js.indexOf('if(!D.rotGX)D.rotGX="gh";')>=0,'load 没有兜底 rotGX');
  ok(js.indexOf('if(!D.rotGXDate)D.rotGXDate=TODAY;')>=0,'load 没有兜底 rotGXDate');
});

console.log('【2 · 用户自定义最低目标清单时,轮换让路】');
T('存在 D.minList 时,轮换不介入,minAll 用回用户的清单',()=>{
  const e=boot();
  e.R('D.minList=[{k:"custom1",n:"我自己加的",s:"其他",mins:30}];');
  const names=JSON.parse(e.R('JSON.stringify(minAll().map(x=>x.n))'));
  eq(names.join(','),'我自己加的','自定义清单被轮换覆盖了');
});
T('自定义清单时,标准线不再包含轮换项,但默写复盘还在',()=>{
  const e=boot();
  e.R('D.minList=[{k:"custom1",n:"我自己加的",s:"其他",mins:30}];');
  const stdNames=JSON.parse(e.R('JSON.stringify(stdToday().map(x=>x.n))'));
  eq(stdNames.indexOf('古代汉语'),-1);
  eq(stdNames.indexOf('现代汉语'),-1);
  ok(stdNames.indexOf('默写复盘')>=0,'默写复盘不该被自定义清单影响');
});
T('gxRotateActive() 精确反映这个开关',()=>{
  const e=boot();
  eq(e.R('gxRotateActive()'),true);
  e.R('D.minList=[{k:"x",n:"x",s:"其他",mins:10}];');
  eq(e.R('gxRotateActive()'),false);
});

console.log('【3 · 标准线渲染与勾选】');
T('渲染出标准线区块,数量和内容正确',()=>{
  const e=boot();e.R('render();');
  const rows=e.doc._ids.stdList._children;
  eq(rows.length,2,'标准线条数不对');
  const txt=rows.map(r=>r._html).join('|');
  ok(txt.indexOf('默写复盘')>=0);
  ok(txt.indexOf('现代汉语')>=0);
});
T('点一下能勾上,存进 d.std',()=>{
  const e=boot();e.R('render();');
  e.doc._ids.stdList._children[0].onclick({target:{classList:{contains:()=>false}}});
  ok(e.R('!!D.days[TODAY].std.p_dic'),'没有存进 std');
});
T('再点一下能取消,且不留 false 残值',()=>{
  const e=boot();
  e.R('day(TODAY).std={p_dic:true};render();');
  e.doc._ids.stdList._children[0].onclick({target:{classList:{contains:()=>false}}});
  eq(e.R('("p_dic" in D.days[TODAY].std)'),false,'取消后留了残值');
});
T('render 会写 d.sset 快照,和 min 的 d.set 同一套模式',()=>{
  const e=boot();e.R('render();');
  const sset=JSON.parse(e.R('JSON.stringify(D.days[TODAY].sset)'));
  eq(sset.length,2);
  ok(sset.some(x=>x.k==='p_dic'));
});
T('标准线的分钟输入框带 class="mi",会被 bindMins() 同一套逻辑处理',()=>{
  // 桩的 querySelectorAll 恒返回 [](见 stub.js),bindMins() 这条路径
  // 在整个项目里从来没有被端到端测试真实覆盖过 —— 不只是标准线,
  // 连最低目标原有的分钟输入也是同样的桩限制,不是这次改动引入的新盲区。
  // 这里只做静态确认:标准线生成的 input 带 class="mi" 和 data-k,
  // 在真机上会被 bindMins() 的 document.querySelectorAll(".mi") 找到并处理。
  const e=boot();
  e.R('day(TODAY).std={p_dic:true};render();');
  const html=e.doc._ids.stdList._children[0]._html;
  ok(/class="mi"/.test(html),'标准线的分钟输入没有 class="mi",不会被 bindMins() 处理');
  ok(/data-k="p_dic"/.test(html),'标准线的分钟输入没有 data-k,bindMins() 拿不到是哪一项');
  ok(js.indexOf('function bindMins()')>=0);
});

console.log('【4 · 断档恢复文案(justRecovering)】');
T('前两天都只完成最低线时,文案出现',()=>{
  const e=boot();
  e.R(`const y=(function(){const t=studyNow();t.setDate(t.getDate()-1);return dkey(t);})();
    const y2=(function(){const t=studyNow();t.setDate(t.getDate()-2);return dkey(t);})();
    D.days[y]={min:{word:1},plus:{},std:{},set:[{k:"word",n:"x",s:"英语",mins:20}],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[y2]={min:{},plus:{},std:{},set:[],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};`);
  eq(e.R('justRecovering()'),true,'该出现却没出现');
});
T('前两天里有一天做到了标准线以上,文案不出现',()=>{
  const e=boot();
  e.R(`const y=(function(){const t=studyNow();t.setDate(t.getDate()-1);return dkey(t);})();
    const y2=(function(){const t=studyNow();t.setDate(t.getDate()-2);return dkey(t);})();
    D.days[y]={min:{word:1},std:{p_dic:1},plus:{},set:[{k:"word",n:"x",s:"英语",mins:20}],pset:[],sset:[{k:"p_dic",n:"y",s:"其他",mins:15}],feel:{b:[],m:[],note:""},pomo:0};
    D.days[y2]={min:{},plus:{},std:{},set:[],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};`);
  eq(e.R('justRecovering()'),false,'不该出现却出现了');
});
T('只有一天数据(比如刚开始用),不判定为需要恢复',()=>{
  const e=boot();
  eq(e.R('justRecovering()'),false,'数据不够也判定了');
});
T('渲染时会把这个状态同步到隐藏属性上',()=>{
  const e=boot();
  e.R(`const y=(function(){const t=studyNow();t.setDate(t.getDate()-1);return dkey(t);})();
    const y2=(function(){const t=studyNow();t.setDate(t.getDate()-2);return dkey(t);})();
    D.days[y]={min:{},plus:{},std:{},set:[],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};
    D.days[y2]={min:{},plus:{},std:{},set:[],pset:[],sset:[],feel:{b:[],m:[],note:""},pomo:0};
    render();`);
  eq(e.doc._ids.stdRecoverTip.hidden,false,'该显示却隐藏着');
});
T('这句文案不追赶、不惩罚、不提"欠"字',()=>{
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费','欠','补上','该完成'].forEach(w=>{
    ok(js.indexOf(w+'”')<0 && html.indexOf('这两天量少也没关系')>=0 &&
      html.slice(html.indexOf('这两天量少'),html.indexOf('这两天量少')+60).indexOf(w)<0,
      '恢复文案里出现了: '+w);
  });
});
T('这个判定不做成状态机,不持久化任何字段,纯滑动窗口',()=>{
  // 断言:justRecovering 不读写任何 D.xxx 持久字段,只读 backKeys/stateOf
  const seg=js.slice(js.indexOf('function justRecovering'),js.indexOf('function justRecovering')+400);
  ok(!/D\.\w+\s*=/.test(seg),'justRecovering 里写了持久化字段,不该有状态机');
});

console.log('【5 · 三处统计接入标准线,不能漏记】');
T('今日时长把标准线算进去',()=>{
  const e=boot();
  e.R('day(TODAY).std={p_dic:1};day(TODAY).sset=[{k:"p_dic",n:"y",s:"其他",mins:15}];');
  eq(e.R('todayMins(TODAY)'),15,'标准线的时长没算进今日时长');
});
T('各科上次记录:只做了标准线那天,不会被当成"没碰过"',()=>{
  const e=boot();
  e.R(mkDays(3,`(d,i)=>{if(i===2){d.std={p_dic:1};d.sset=[{k:"p_dic",n:"默写复盘",s:"其他",mins:15}];}}`));
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  ok(r['其他'],'标准线做的科目完全没被记录');
  eq(r['其他'].gap,2);
});
T('空壳日判定:只做了标准线的一天不算空壳',()=>{
  const e=boot();
  e.R('day(TODAY).std={p_dic:1};day(TODAY).sset=[{k:"p_dic",n:"y",s:"其他",mins:15}];');
  e.R(mkDays(1,`(d)=>{}`));   // 保证本周有别的日子,weekFacts 能跑
  eq(e.R('weekFacts().days')>=1,true,'只做标准线的一天被当成空壳了');
});
T('按科目统计:标准线的完成量计入排行',()=>{
  // render() 会用当天真实的 stdToday() 重算 sset,手动塞的假 sset 会被覆盖 ——
  // 不用手动造 sset,让 render() 自己算就行。另外 statList 是逐行 appendChild
  // 出来的,内容不在 _html 里(桩的已知坑),要读 _children。
  const e=boot();
  e.R('day(TODAY).std={p_dic:true};render();');
  const rows=e.doc._ids.statList._children;
  ok(rows.some(r=>(r._html||'').indexOf('默写复盘')>=0),'标准线完成的项目没有进入统计排行');
});

console.log('【6 · 迁移:load / import 两处对称】');
T('load 会给老存档补 std/sset 字段',()=>{
  ok(js.indexOf('if(!dy.std||typeof dy.std!=="object")dy.std={};')>=0,'load 缺 std 兜底');
  ok(js.indexOf('if(!Array.isArray(dy.sset))dy.sset=[];')>=0,'load 缺 sset 兜底');
});
T('import 也有同样的兜底,和 load 对称',()=>{
  eq(js.split('if(!dy.std||typeof dy.std!=="object")').length-1,2,'std 兜底不是两处都有');
  eq(js.split('if(!Array.isArray(dy.sset))').length-1,2,'sset 兜底不是两处都有');
});
T('import 会把轮换指针带过来,不会重置成默认值',()=>{
  ok(js.indexOf('if(inc.rotGX)T.rotGX=inc.rotGX;')>=0,'导入没带 rotGX');
  ok(js.indexOf('if(inc.rotGXDate)T.rotGXDate=inc.rotGXDate;')>=0,'导入没带 rotGXDate');
});
T('导入路径里这两行仍然写在暂存区 T 上,不是直接写 D',()=>{
  const i=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(i,js.indexOf('D=T;',i));
  ok(/T\.rotGX=inc\.rotGX/.test(seg),'没有写在 T 上');
  ok(!/(^|[^.\w])D\.rotGX\s*=/.test(seg),'违反两阶段提交,直接写了 D.rotGX');
});

console.log('【7 · 达标口径不受三档结构影响(已定默认值)】');
T('只完成标准线,不完成最低线,不算达标',()=>{
  const e=boot();
  e.R('day(TODAY).std={p_dic:1,gh:1};');   // 标准线全勾了
  eq(e.R('stateOf(TODAY)'),'none','光做标准线不该被算成达标');
});
T('最低线全勾,不管标准线做没做,达标判定不变',()=>{
  const e=boot();
  e.R('const d=day(TODAY);minAll().forEach(x=>{d.min[x.k]=1;});');
  eq(e.R('stateOf(TODAY)'),'full','达标判定被标准线影响了');
});

console.log('【8 · 不回归】');
T('四栏一起画不抛错',()=>{
  const e=boot();e.R('render();');
  e.R(`day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");
    day(TODAY).std={p_dic:1};
    render();renderHealth();renderTimeStat();renderRvSum();renderExpWarn();renderBroke();kyOpen=true;renderKy();`);
  ok(e.doc._ids.cheerText._text.length>0);
});
T('版本号格式正确且只有一处',()=>{
  // v21.1 起版本号可以带小版本号(v21.1),正则放开一位小数位
  const m=html.match(/id="verTag">(v\d+(?:\.\d+)? · \d{2}\/\d{2})</);
  ok(m,'顶部没有合法的版本号');
  eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
