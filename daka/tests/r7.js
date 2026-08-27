const fs=require('fs'),vm=require('vm'),{build}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};
function boot(){
  const doc=build('app.html');const store={};
  const g={document:doc,localStorage:{getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>true,prompt:()=>null,setTimeout:()=>0,clearTimeout:()=>{},
    setInterval:()=>1,clearInterval:()=>{},requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date,Math,JSON,String,Number,Object,Array,RegExp,Error,isNaN,parseInt,parseFloat,
    encodeURIComponent,decodeURIComponent,Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},Promise};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  vm.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};refreshDay();day(TODAY);',ctx);
  return {doc,ctx,R:c=>vm.runInContext(c,ctx)};
}
// 造历史日:走 mk(i) 拿学习日键,不直接 new Date
const mkDays=(n,fn)=>`(function(){const mk=i=>{const t=studyNow();t.setDate(t.getDate()-i);return dkey(t);};
  for(let i=1;i<=${n};i++){const k=mk(i);D.days[k]={min:{},plus:{},meds:{},medSkip:{},medNote:{},drink:{},
    set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};(${fn})(D.days[k],i,k);}})()`;

console.log('【1 · 感受统计按天去重】');
T('同一天全天+中午各勾一次,只算一天',()=>{
  const e=boot();
  e.R(mkDays(1,`(d)=>{d.feel={b:["手脚冰凉"],m:[],note:"",slots:{noon:{b:["手脚冰凉"],m:[]}}};}`));
  const n=e.R('feelDayTags(D.days[Object.keys(D.days).filter(k=>k!==TODAY)[0]]).length');
  eq(n,1,'去重后条数不对');
});
T('去重不丢时段信息',()=>{
  const e=boot();
  e.R(mkDays(1,`(d)=>{d.feel={b:["手脚冰凉"],m:[],note:"",slots:{noon:{b:["手脚冰凉"],m:[]},pm:{b:["手脚冰凉"],m:[]}}};}`));
  const sl=e.R('JSON.stringify(feelDayTags(D.days[Object.keys(D.days).filter(k=>k!==TODAY)[0]])[0].slots)');
  eq(sl,'["all","noon","pm"]','时段合并结果不对');
});
T('健康回顾里显示的是天数不是次数',()=>{
  const e=boot();
  e.R(mkDays(3,`(d)=>{d.feel={b:["手脚冰凉"],m:[],note:"",slots:{noon:{b:["手脚冰凉"],m:[]}}};}`));
  e.R('renderHealth();');
  const h=e.doc._ids.healthStat._html;
  const m=/手脚冰凉[\s\S]{0,220}?<span class="sc">([^<]+)</.exec(h);
  ok(m,'没找到该行');
  eq(m[1],'3 天','双记就会变成 6');
});
T('不同标签不会被合并',()=>{
  const e=boot();
  e.R(mkDays(1,`(d)=>{d.feel={b:["头痛","手脚冰凉"],m:[],note:""};}`));
  const n=e.R('feelDayTags(D.days[Object.keys(D.days).filter(k=>k!==TODAY)[0]]).length');
  eq(n,2);
});
T('feelDetail 概览按天',()=>{
  const e=boot();
  e.R(mkDays(2,`(d)=>{d.feel={b:["脸发红发烫"],m:[],note:"",slots:{pm:{b:["脸发红发烫"],m:[]}}};}`));
  e.R('feelDetail("脸发红发烫",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('出现在 2 天')>=0,'概览仍在按次数算');
});
T('feelDetail 日期行把同一天的时段并成一行',()=>{
  const e=boot();
  e.R(mkDays(1,`(d)=>{d.feel={b:["手脚冰凉"],m:[],note:"",slots:{pm:{b:["手脚冰凉"],m:[]}}};}`));
  e.R('feelDetail("手脚冰凉",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  const seg=b.slice(b.indexOf('具体日期'));
  eq((seg.match(/周[日一二三四五六]/g)||[]).length,1,'同一天出现了多行');
  ok(seg.indexOf('晚')>=0,'时段没带出来');
});
T('经期内外对照也按天',()=>{
  const e=boot();
  e.R(mkDays(4,`(d)=>{d.cycle=["mid"];d.feel={b:["疲劳"],m:[],note:"",slots:{noon:{b:["疲劳"],m:[]}}};}`));
  e.R('renderHealth();');
  const h=e.doc._ids.healthStat._html;
  ok(h.indexOf('经期内 4 天')>=0,'经期对照仍在双记: '+(/疲劳:[^　<]*/.exec(h)||[''])[0]);
});

console.log('【2 · 早晚补身体组】');
T('早上能记身体类标签',()=>{
  const e=boot();
  const g=e.R('JSON.stringify(FEEL_SETS.am.map(x=>x.g))');
  ok(g.indexOf('身体')>=0,'am 没有身体组');
});
T('晚上能记身体类标签',()=>{
  const e=boot();
  const g=e.R('JSON.stringify(FEEL_SETS.pm.map(x=>x.g))');
  ok(g.indexOf('身体')>=0,'pm 没有身体组');
});
T('手脚冰凉在晚上可选',()=>{
  const e=boot();
  ok(e.R('FEEL_SETS.pm.some(x=>x.t.indexOf("手脚冰凉")>=0)'),'晚上仍记不了手脚冰凉');
});
T('脸发红发烫早晚都可选',()=>{
  const e=boot();
  ok(e.R('FEEL_SETS.am.some(x=>x.t.indexOf("脸发红发烫")>=0)'),'早上缺');
  ok(e.R('FEEL_SETS.pm.some(x=>x.t.indexOf("脸发红发烫")>=0)'),'晚上缺');
});
// 桩的 appendChild 内容不在 innerHTML 里,要递归子节点(见 tests/README 第 5 条)
const deepText=n=>{
  if(!n)return '';
  let t=(n._text||'')+' '+(n._html||'');
  (n._children||[]).forEach(c=>{t+=' '+deepText(c);});
  return t;
};
T('切到晚上时段能渲染出身体组按钮',()=>{
  const e=boot();
  e.R('feelSlot="pm";render();');
  const host=e.doc._ids.feelGroups;
  ok(host._children.length>=4,'晚间分组数不对: '+host._children.length);
  const txt=deepText(host);
  ok(txt.indexOf('手脚冰凉')>=0,'晚上渲染不出手脚冰凉');
});
T('新加的标签没有和原组重名冲突',()=>{
  const e=boot();
  const dup=e.R(`(function(){let n=0;["am","pm"].forEach(s=>{const seen={};
    FEEL_SETS[s].forEach(g=>g.t.forEach(t=>{if(seen[t])n++;seen[t]=1;}));});return n;})()`);
  eq(dup,0,'同一时段内出现了重复标签');
});

console.log('【3 · 补剂观察记录三态通用】');
T('吃了的日子也能写观察',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.meds={m_vd:Date.now()};medOpen="m_vd";render();');
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  ok(mm,'菜单没展开');
  ok(mm._children.some(c=>c.classList&&c.classList.contains('rnote')),'「已吃」下没有观察输入框');
});
T('已吃状态写的观察存得住',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.meds={m_vd:Date.now()};medOpen="m_vd";render();');
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  const inp=mm._children.find(c=>c.classList&&c.classList.contains('rnote'));
  inp.value='有点反胃';inp.onchange();
  eq(e.R('D.days[TODAY].medNote.m_vd'),'有点反胃','没写进 medNote');
});
T('吃了的行上会显示「有观察」',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.meds={m_vd:Date.now()};d.medNote={m_vd:"腥味打嗝"};render();');
  const r=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('med'));
  ok(r._html.indexOf('有观察')>=0,'已吃状态没有观察标记');
});
T('老数据的 medSkip.t 能被读出来',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"忘了",t:"旧观察"}};');
  eq(e.R('mNote(D.days[TODAY],"m_vd")'),'旧观察','老字段读不到');
});
T('改成「吃了」之后观察不丢',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"忘了",t:""}};medOpen="m_vd";render();');
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  mm._children.find(c=>c.classList&&c.classList.contains('rnote')).value='停一天看看';
  mm._children.find(c=>c.classList&&c.classList.contains('rnote')).onchange();
  e.R('const d2=day(TODAY);delete d2.medSkip.m_vd;d2.meds.m_vd=Date.now();render();');
  eq(e.R('mNote(D.days[TODAY],"m_vd")'),'停一天看看','换状态把观察冲掉了');
});
T('清空观察会把字段删掉,不留空串',()=>{
  const e=boot();
  e.R('setNote(TODAY,"m_vd","abc");setNote(TODAY,"m_vd","");');
  eq(e.R('("m_vd" in D.days[TODAY].medNote)'),false,'留下了空壳');
});
T('明细的观察段把三种状态都收进来',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i)=>{d.meds={m_vd:1};if(i===2){d.medNote={m_vd:"吃完有点胀"};}
    if(i===4){delete d.meds.m_vd;d.medSkip={m_vd:{r:"有意没吃",t:""}};d.medNote={m_vd:"停一天"};}}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('观察记录 · 共 2 条')>=0,'条数不对');
  ok(b.indexOf('吃完有点胀')>=0,'吃了那条没进来');
  ok(b.indexOf('停一天')>=0,'没吃那条没进来');
});
T('观察段标出当天是吃了还是没吃',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i)=>{d.meds={m_vd:1};if(i===2)d.medNote={m_vd:"胀"};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  ok(e.doc._ids.detBody._html.indexOf('· 吃了')>=0,'没标状态');
});

console.log('【4 · 过去的日子可以补记】');
function detEnv(){
  const e=boot();
  e.R(mkDays(9,`(d,i)=>{if(i%3)d.meds={m_vd:1};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  return e;
}
T('没有记录的日子渲染成可点的行',()=>{
  const e=detEnv();
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('没有记录的日子 · 点一条可以补记')>=0,'标题没改');
  ok(/data-fix="1"/.test(b),'没有可补记的行');
});
T('记了「没吃」的日子也可以点开改',()=>{
  const e=boot();
  e.R(mkDays(9,`(d,i)=>{if(i%3)d.meds={m_vd:1};else d.medSkip={m_vd:{r:"忘了",t:""}};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('记了「没吃」的日子 · 点一条可以改')>=0,'标题没改');
});
T('补记面板能写进原因',()=>{
  const e=detEnv();
  const b=e.doc._ids.detBody._html;
  const m=/data-day="([\d-]+)" data-mk="m_vd" data-fix="1"/.exec(b);
  ok(m,'找不到补记行');
  const dk=m[1];
  const row={__open:false,__box:null,getAttribute:k=>k==='data-day'?dk:(k==='data-mk'?'m_vd':(k==='data-fix'?'1':null)),parentNode:null};
  e.R(`__row=${JSON.stringify({d:dk})}`);
  e.ctx.__testRow=row;
  e.R('openFixEdit(__testRow);');
  const box=row.__box;
  ok(box,'面板没建出来');
  const rz=box._children.find(c=>c.classList&&c.classList.contains('rz'));
  rz._children.find(c=>c._text==='忘了').onclick({stopPropagation(){}});
  eq(e.R(`D.days["${dk}"].medSkip.m_vd.r`),'忘了','原因没写进去');
});
T('补记面板能写进观察',()=>{
  const e=detEnv();
  const dk=/data-day="([\d-]+)" data-mk="m_vd" data-fix="1"/.exec(e.doc._ids.detBody._html)[1];
  const row={__open:false,__box:null,getAttribute:k=>k==='data-day'?dk:(k==='data-mk'?'m_vd':(k==='data-fix'?'1':null)),parentNode:null};
  e.ctx.__testRow=row;e.R('openFixEdit(__testRow);');
  const inp=row.__box._children.find(c=>c.classList&&c.classList.contains('rnote'));
  inp.value='那天忘带出门';inp.onchange();
  eq(e.R(`mNote(D.days["${dk}"],"m_vd")`),'那天忘带出门','观察没存住');
});
T('补记面板的「其实吃了」写成无时刻记录',()=>{
  const e=detEnv();
  const dk=/data-day="([\d-]+)" data-mk="m_vd" data-fix="1"/.exec(e.doc._ids.detBody._html)[1];
  const row={__open:false,__box:null,getAttribute:k=>k==='data-day'?dk:(k==='data-mk'?'m_vd':(k==='data-fix'?'1':null)),parentNode:null};
  e.ctx.__testRow=row;e.R('openFixEdit(__testRow);');
  row.__box._children.find(c=>c.tagName==='BUTTON'&&c._text==='其实吃了').onclick({stopPropagation(){}});
  eq(e.R(`D.days["${dk}"].meds.m_vd`),1,'没记成「吃过但无时刻」');
});
T('补记不会污染别的日子',()=>{
  const e=detEnv();
  const dk=/data-day="([\d-]+)" data-mk="m_vd" data-fix="1"/.exec(e.doc._ids.detBody._html)[1];
  const before=e.R('Object.keys(D.days).filter(k=>D.days[k].meds&&D.days[k].meds.m_vd).length');
  const row={__open:false,__box:null,getAttribute:k=>k==='data-day'?dk:(k==='data-mk'?'m_vd':(k==='data-fix'?'1':null)),parentNode:null};
  e.ctx.__testRow=row;e.R('openFixEdit(__testRow);');
  row.__box._children.find(c=>c.tagName==='BUTTON'&&c._text==='其实吃了').onclick({stopPropagation(){}});
  eq(e.R('Object.keys(D.days).filter(k=>D.days[k].meds&&D.days[k].meds.m_vd).length'),before+1,'影响了别的天');
});

console.log('【5 · 饮品记录】');
T('两项饮品都渲染出来',()=>{
  const e=boot();e.R('render();');
  const rows=e.doc._ids.drinkBox._children;
  eq(rows.length,2,'行数不对');
  ok(rows[0]._html.indexOf('奶茶')>=0);
});
T('记一杯会加一',()=>{
  const e=boot();e.R('render();');
  const row=e.doc._ids.drinkBox._children[0];
  row._children.find(c=>c._text==='记一杯').onclick();
  eq(e.R('D.days[TODAY].drink.tea'),1,'没加上');
});
T('减到零会把字段删掉',()=>{
  const e=boot();
  e.R('day(TODAY).drink={tea:1};render();');
  const row=e.doc._ids.drinkBox._children[0];
  row._children.find(c=>c._text==='−').onclick();
  eq(e.R('("tea" in D.days[TODAY].drink)'),false,'留下了 0');
});
T('零杯时减号是禁用的',()=>{
  const e=boot();e.R('render();');
  const row=e.doc._ids.drinkBox._children[0];
  eq(row._children.find(c=>c._text==='−').disabled,true,'零杯还能减');
});
T('本周合计只算本周',()=>{
  const e=boot();
  e.R(`day(TODAY).drink={tea:2};
    (function(){const t=studyNow();t.setDate(t.getDate()-30);D.days[dkey(t)]={min:{},plus:{},meds:{},
      medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0,drink:{tea:9}};})();`);
  eq(e.R('weekDrink().tea'),2,'把陈年记录算进来了');
});
T('参考值默认两杯,可改并存下来',()=>{
  const e=boot();e.R('render();');
  eq(e.R('teaRef()'),2);
  e.doc._ids.teaRef.value='3';e.doc._ids.teaRef.onchange();
  eq(e.R('D.teaRef'),3,'没存住');
});
T('参考值填了乱数会退回默认',()=>{
  const e=boot();e.R('D.teaRef=-5;');
  eq(e.R('teaRef()'),2,'负数没被挡住');
});
T('健康回顾里出现饮品段',()=>{
  const e=boot();
  e.R(mkDays(5,`(d,i)=>{d.drink={tea:1};}`));
  e.R('renderHealth();');
  const h=e.doc._ids.healthStat._html;
  ok(h.indexOf('饮品 · 最近 30 天')>=0,'没有饮品段');
  ok(h.indexOf('5 杯')>=0,'杯数不对');
  ok(h.indexOf('data-drink=')>=0,'行不可点');
});
T('一杯没喝时不显示饮品段',()=>{
  const e=boot();
  e.R(mkDays(5,`(d)=>{}`));
  e.R('renderHealth();');
  ok(e.doc._ids.healthStat._html.indexOf('饮品 · 最近 30 天')<0,'空的也显示了');
});
T('饮品明细能打开并按周分组',()=>{
  const e=boot();
  e.R(mkDays(5,`(d)=>{d.drink={tea:1};}`));
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('每周几杯')>=0,'没有周分组');
  ok(b.indexOf('共 5 杯')>=0,'总数不对');
});
T('饮品文案不带评判用词',()=>{
  const seg=js.slice(js.indexOf('function drinkDetail'));
  const body=seg.slice(0,seg.indexOf('\n}'));
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>{
    ok(body.indexOf(w)<0,'出现禁用词: '+w);
  });
});
T('饮品区没有 emoji',()=>{
  const seg=js.slice(js.indexOf('const DRINKS='),js.indexOf('const SUBJECTS_BUILTIN'))
    +js.slice(js.indexOf('function drinkDetail'),js.indexOf('const MOOD_LOW'));
  const bad=seg.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu);
  eq(bad?bad.join(''):'','','出现 emoji: '+(bad||[]).join(''));
});

console.log('【6 · 考研时间节点】');
T('收起时只有一行,列表是隐藏的',()=>{
  const e=boot();e.R('render();');
  eq(e.doc._ids.kyList.hidden,true,'默认就展开了');
  ok(e.doc._ids.kyToggle.textContent.length>0,'条上没有文字');
});
T('条上显示距初试天数',()=>{
  const e=boot();e.R('render();');
  ok(/距初试 \d+ 天/.test(e.doc._ids.kyToggle.textContent),'没算天数: '+e.doc._ids.kyToggle.textContent);
});
T('点一下展开,再点收起',()=>{
  const e=boot();e.R('render();');
  e.R('kyOpen=true;renderKy();');
  eq(e.doc._ids.kyList.hidden,false,'没展开');
  ok(e.doc._ids.kyList._children.length>=10,'条目太少');
  e.R('kyOpen=false;renderKy();');
  eq(e.doc._ids.kyList.hidden,true,'没收起');
});
T('节点按日期排序',()=>{
  const e=boot();
  const ds=JSON.parse(e.R('JSON.stringify(kyAll().map(x=>x.d))'));
  eq(JSON.stringify(ds),JSON.stringify(ds.slice().sort()),'没有按日期排');
});
T('改过的日期会盖掉预填值并参与排序',()=>{
  const e=boot();
  e.R('D.kyFix={exam:"2026-12-26"};');
  eq(e.R('kyAll().find(x=>x.k==="exam").d'),'2026-12-26','没生效');
});
T('乱七八糟的自定义日期不会生效',()=>{
  const e=boot();
  e.R('D.kyFix={exam:"明天"};');
  eq(e.R('kyAll().find(x=>x.k==="exam").d'),'2026-12-19','脏数据被采用了');
});
T('展开后每条都能改日期',()=>{
  const e=boot();e.R('render();kyOpen=true;renderKy();');
  const row=e.doc._ids.kyList._children[0];
  const inp=row._children.find(c=>c.type==='date');
  ok(inp,'没有日期输入框');
  inp.value='2026-09-20';inp.onchange();
  ok(e.R('JSON.stringify(D.kyFix)').indexOf('2026-09-20')>=0,'没存住');
});
T('已经过去的节点标成已过',()=>{
  const e=boot();
  e.R('D.kyFix={outline:"2020-01-01"};render();kyOpen=true;renderKy();');
  const h=JSON.stringify(e.doc._ids.kyList._children.map(c=>c._html||''));
  ok(h.indexOf('已过')>=0,'没有已过标记');
});
T('节点区文案写明以官方为准',()=>{
  const e=boot();e.R('render();kyOpen=true;renderKy();');
  const txt=e.doc._ids.kyList._children.map(c=>c._text||'').join('');
  ok(txt.indexOf('研招网')>=0,'没有免责说明');
});
T('节点区没有禁用词和 emoji',()=>{
  // 锚点必须用不会被后续改动删掉的东西 —— 上一版用一句注释文字当第二段的
  // 结束锚点,后来那句注释被移走,indexOf 找不到就返回 -1,而 slice(a,-1)
  // 在 JS 里不报错、只是把「从考研节点一路切到文件末尾」都当成了目标区间,
  // 这个测试因此悄悄失效了一段时间,直到这次撞上「必须」两个字才暴露。
  // 换成函数体本身的起止(renderKy 从声明到它自己最后一行 p.textContent
  // 那句一定存在的产品文案），不依赖任何注释文字。
  ok(js.indexOf('function renderKy')>=0,'renderKy 函数不见了,基线已经变了,需要重新核实这条测试');
  const seg=js.slice(js.indexOf('const KY_NODES='),js.indexOf('const KEY='))
    +js.slice(js.indexOf('function renderKy'),
      js.indexOf('研招网公告为准')+20);
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>{
    ok(seg.indexOf(w)<0,'出现禁用词: '+w);
  });
  const bad=seg.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu);
  eq(bad?bad.join(''):'','','出现 emoji');
});

console.log('【7 · 存档与迁移】');
T('老存档没有 medNote 也能装载',()=>{
  const e=boot();
  e.R(`D.days["2026-08-01"]={min:{},plus:{},meds:{m_vd:1},medSkip:{m_mg:{r:"忘了",t:"旧的观察"}},
    set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};`);
  e.R(`Object.keys(D.days).forEach(k=>{const dy=D.days[k];
    if(!dy.medNote||typeof dy.medNote!=="object")dy.medNote={};
    Object.keys(dy.medSkip||{}).forEach(mk=>{const o=dy.medSkip[mk];
      if(o&&o.t&&!dy.medNote[mk])dy.medNote[mk]=o.t;});
    if(!dy.drink||typeof dy.drink!=="object")dy.drink={};});`);
  eq(e.R('D.days["2026-08-01"].medNote.m_mg'),'旧的观察','老观察没迁过来');
  eq(e.R('JSON.stringify(D.days["2026-08-01"].drink)'),'{}','drink 没兜底');
});
T('load 里带了 medNote / drink 兜底',()=>{
  ok(/if\(!dy\.medNote\|\|typeof dy\.medNote!=="object"\)dy\.medNote=\{\};/.test(js),'load 缺兜底');
  ok(js.indexOf('if(!dy.drink||typeof dy.drink!=="object")dy.drink={};')>=0,'drink 缺兜底');
});
T('导入会把 kyFix 和 teaRef 带过来',()=>{
  // v19 起导入先写暂存区 T,校验通过才整体赋给 D
  ok(js.indexOf('if(inc.teaRef!==undefined)T.teaRef=inc.teaRef;')>=0,'teaRef 没迁移');
  ok(js.indexOf('inc.kyFix&&typeof inc.kyFix==="object"')>=0,'kyFix 没迁移');
});
T('导出是整个 D,新字段自动带上',()=>{
  const e=boot();
  e.R('D.teaRef=3;D.kyFix={exam:"2026-12-26"};day(TODAY).drink={tea:1};');
  const dump=e.R('JSON.stringify(D)');
  ok(dump.indexOf('teaRef')>=0&&dump.indexOf('kyFix')>=0&&dump.indexOf('drink')>=0,'字段没进快照');
});
T('版本号格式正确且只有一处',()=>{
  // 不写死版本号,免得每次发版都要回来改这条
  const m=html.match(/id="verTag">(v\d+ · \d{2}\/\d{2})</);
  ok(m,'顶部没有合法的版本号');
  eq(html.split(m[1]).length-1,1,'版本号字符串出现了不止一处');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
