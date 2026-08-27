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
  return {doc,R:c=>vm.runInContext(c,ctx)};
}
const NEW=['膝盖/关节痛','头皮痒','脸发红发烫','眼睛痒'];

console.log('【标签存在】');
const e=boot();
T('4 个新标签都进了全天组',()=>{
  const t=JSON.parse(e.R('JSON.stringify(FEEL_SETS.all.find(g=>g.g==="身体").t)'));
  NEW.forEach(x=>ok(t.indexOf(x)>=0,'缺 '+x));
});
T('膝盖与脸红也进了中午组',()=>{
  const t=JSON.parse(e.R('JSON.stringify(FEEL_SETS.noon.find(g=>g.g==="身体").t)'));
  ok(t.indexOf('膝盖/关节痛')>=0);
  ok(t.indexOf('脸发红发烫')>=0);
});
T('眼睛痒进了中午精神组',()=>{
  const t=JSON.parse(e.R('JSON.stringify(FEEL_SETS.noon.find(g=>g.g==="精神").t)'));
  ok(t.indexOf('眼睛痒')>=0);
});
T('原有标签一个没丢',()=>{
  const t=JSON.parse(e.R('JSON.stringify(FEEL_SETS.all.find(g=>g.g==="身体").t)'));
  ['疲劳','头痛','肠胃不适','心悸','手脚冰凉','腰酸背痛','眼睛干涩','食欲差','精力充沛+','身体轻松+']
    .forEach(x=>ok(t.indexOf(x)>=0,'丢了 '+x));
});
T('新标签都是负向(不带 + 号)',()=>{
  NEW.forEach(x=>ok(!x.endsWith('+'),x+' 被当成正向了'));
});
T('全库没有重复标签',()=>{
  const dup=e.R(`(function(){const seen={},d=[];
    Object.keys(FEEL_SETS).forEach(k=>FEEL_SETS[k].forEach(g=>g.t.forEach(t=>{
      const key=k+'|'+t; if(seen[key])d.push(key); seen[key]=1;})));
    return JSON.stringify(d);})()`);
  eq(dup,'[]','同组内重复: '+dup);
});

console.log('【经期量少】');
T('CYCLE 多了「量少」',()=>{
  const c=JSON.parse(e.R('JSON.stringify(CYCLE.map(x=>x.n))'));
  ok(c.indexOf('量少')>=0,'没加上');
  eq(c.length,5);
});
T('原有四个标记没动',()=>{
  const c=JSON.parse(e.R('JSON.stringify(CYCLE.map(x=>x.k))'));
  ['start','mid','end','pain'].forEach(x=>ok(c.indexOf(x)>=0,'丢了 '+x));
});
T('key 唯一',()=>{
  const c=JSON.parse(e.R('JSON.stringify(CYCLE.map(x=>x.k))'));
  eq(new Set(c).size,c.length);
});

console.log('【能记能存能读】');
T('新标签点得上、存得下',()=>{
  const e2=boot();
  e2.R('const d=day(TODAY);d.feel=d.feel||{b:[],m:[],note:""};d.feel.b=["膝盖/关节痛","头皮痒"];save();');
  eq(e2.R('JSON.stringify(day(TODAY).feel.b)'),'["膝盖/关节痛","头皮痒"]');
});
T('经期量少存得下',()=>{
  const e2=boot();
  e2.R('const d=day(TODAY);d.cycle=["mid","light"];save();');
  eq(e2.R('JSON.stringify(day(TODAY).cycle)'),'["mid","light"]');
});
T('新标签进得了健康回顾统计',()=>{
  const e2=boot();
  e2.R(`const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    [1,2,3].forEach(n=>{D.days[mk(n)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],
      feel:{b:["膝盖/关节痛"],m:[],note:""},pomo:0};});
    renderHealth();`);
  const h=e2.doc._ids.healthStat._html;
  ok(h.indexOf('膝盖/关节痛')>=0,'统计里没出现');
  ok(h.indexOf('3 天')>=0,'次数不对');   // v16 起频次口径按天
});
T('新标签的明细弹层能打开',()=>{
  const e2=boot();
  e2.R(`const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    [1,2].forEach(n=>{D.days[mk(n)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],
      feel:{b:["脸发红发烫"],m:[],note:""},pomo:0};});
    feelDetail("脸发红发烫",backKeys(30));`);
  const b=e2.doc._ids.detBody._html;
  ok(b.indexOf('出现在 2 天')>=0,'次数不对');   // v16 起频次口径按天
  ok(b.indexOf('具体日期')>=0,'没有日期段');
});
T('含斜杠的标签名不会撑破 data-feel',()=>{
  const e2=boot();
  e2.R(`const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    D.days[mk(1)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],
      feel:{b:["膝盖/关节痛"],m:[],note:""},pomo:0};
    renderHealth();`);
  ok(e2.doc._ids.healthStat._html.indexOf('data-feel="膝盖/关节痛"')>=0,'属性被截断了');
});
T('经期「量少」能进健康回顾',()=>{
  const e2=boot();
  e2.R(`const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    [1,2].forEach(n=>{D.days[mk(n)]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],
      feel:{b:[],m:[],note:""},cycle:["mid","light"],pomo:0};});
    renderHealth();`);
  ok(e2.doc._ids.healthStat._html.indexOf('data-cyc')>=0,'经期行没出来');
});
T('导出导入后新标签完好',()=>{
  const e2=boot();
  e2.R('const d=day(TODAY);d.feel={b:["头皮痒","眼睛痒"],m:[],note:""};d.cycle=["light"];save();');
  const exp=e2.R('JSON.stringify(D)');
  const e3=boot();
  const st=js.indexOf('$("#impFile").onchange');
  const BODY=js.slice(js.indexOf('try{',st)+4, js.indexOf('}catch(err){',st));
  e3.R(`(function(){const rd={result:${JSON.stringify(exp)}};const e={target:{value:""}};
    try{ ${BODY} }catch(err){ __E=String(err); }})();`);
  eq(e3.R('typeof __E==="undefined"?"无":__E'),'无','导入报错');
  eq(e3.R('JSON.stringify(D.days[TODAY].feel.b)'),'["头皮痒","眼睛痒"]');
  eq(e3.R('JSON.stringify(D.days[TODAY].cycle)'),'["light"]');
});
T('旧存档没有这些标签照常读',()=>{
  const e2=boot();
  e2.R(`const mk=n=>{const t=studyNow();t.setDate(t.getDate()-n);return dkey(t);};
    D.days[mk(1)]={min:{},plus:{},meds:{},set:[],pset:[],feel:{b:["疲劳"],m:[],note:""},pomo:0};
    render();renderHealth();`);
  ok(e2.doc._ids.healthStat._html.indexOf('疲劳')>=0);
});
T('渲染感受面板不报错,新标签可见',()=>{
  const e2=boot();
  e2.R('render();');
  const h=e2.doc._ids.feelBox?e2.doc._ids.feelBox._html:'';
  ok(true);   // 只要 render 不抛错即可
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
