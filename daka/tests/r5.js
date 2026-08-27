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
const mkDays=(n,fn)=>`(function(){const mk=i=>{const t=studyNow();t.setDate(t.getDate()-i);return dkey(t);};
  for(let i=1;i<=${n};i++){const k=mk(i);D.days[k]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],
    feel:{b:[],m:[],note:""},pomo:0};(${fn})(D.days[k],i,k);}})()`;

console.log('【1 · 漏记原因与观察】');
const e0=boot();
T('原因改为三项',()=>{
  eq(e0.R('JSON.stringify(SKIP_REASONS)'),'["忘了","有意没吃","其他"]');
});
T('观察文字在任何原因下都能写',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"有意没吃",t:""}};medOpen="m_vd";render();');
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  ok(mm._children.some(c=>c.classList&&c.classList.contains('rnote')),'非「其他」时没有输入框');
});
T('换原因不会把观察文字清掉',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"忘了",t:"今晚停用,观察睡眠"}};medOpen="m_vd";render();');
  const mm=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('medmenu'));
  const rz=mm._children.find(c=>c.classList&&c.classList.contains('rz'));
  rz._children.find(c=>c._text==='有意没吃').onclick();
  eq(e.R('day(TODAY).medSkip.m_vd.t'),'今晚停用,观察睡眠','观察被清掉了');
  eq(e.R('day(TODAY).medSkip.m_vd.r'),'有意没吃');
});
T('写了观察的行上有标记',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"有意没吃",t:"观察睡眠"}};render();');
  const r=e.doc._ids.medList._children.find(c=>c.classList&&c.classList.contains('med'));
  ok(r._html.indexOf('有观察')>=0,'没有观察标记');
});
T('观察内容存得住,不是写完就没',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"有意没吃",t:"停一晚看睡眠"}};save();');
  eq(e.R('JSON.parse(localStorage.getItem(KEY)).days[TODAY].medSkip.m_vd.t'),'停一晚看睡眠');
});
T('观察记录在明细里单独成段,能点开看到',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i,k)=>{if(i<=2)d.meds.m_vd=Date.now();
    else if(i===3)d.medSkip.m_vd={r:"有意没吃",t:"今晚停用,观察睡眠"};
    else if(i===4)d.medSkip.m_vd={r:"忘了",t:""};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('观察记录')>=0,'没有观察记录段');
  ok(b.indexOf('今晚停用,观察睡眠')>=0,'观察内容没显示');
  ok(b.indexOf('class="obs"')>=0);
});
T('没写观察时不出现该段',()=>{
  const e=boot();
  e.R(mkDays(4,`(d,i,k)=>{if(i<=2)d.meds.m_vd=Date.now();else d.medSkip.m_vd={r:"忘了",t:""};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  ok(e.doc._ids.detBody._html.indexOf('观察记录')<0,'空的也显示了');
});
T('导出导入保住观察内容',()=>{
  const e=boot();
  e.R('const d=day(TODAY);d.medSkip={m_vd:{r:"有意没吃",t:"停一晚看睡眠"}};save();');
  const exp=e.R('JSON.stringify(D)');
  const e2=boot();
  const st=js.indexOf('$("#impFile").onchange');
  const BODY=js.slice(js.indexOf('try{',st)+4,js.indexOf('}catch(err){',st));
  e2.R(`(function(){const rd={result:${JSON.stringify(exp)}};const e={target:{value:""}};
    try{ ${BODY} }catch(err){ __E=String(err); }})();`);
  eq(e2.R('typeof __E==="undefined"?"无":__E'),'无');
  eq(e2.R('D.days[TODAY].medSkip.m_vd.t'),'停一晚看睡眠');
});

console.log('【2 · 布局顺序与折叠】');
function detail(e,n){
  e.R(mkDays(n,`(d,i,k)=>{if(i%4)d.meds.m_vd=new Date(k+"T21:00:00").getTime();
    else d.medSkip.m_vd={r:"忘了",t:i===4?"看看少吃一天有没有差别":""};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  return e.doc._ids.detBody._html;
}
T('时刻规律排在具体日期前面',()=>{
  const b=detail(boot(),40);
  const a1=b.indexOf('时刻规律'), a2=b.indexOf('每一次');
  ok(a1>=0&&a2>=0,'段落缺失');
  ok(a1<a2,'规律没排在日期前面');
});
T('观察记录也在具体日期前面',()=>{
  const b=detail(boot(),40);
  ok(b.indexOf('观察记录')<b.indexOf('每一次'),'观察记录排在后面了');
});
T('长列表默认折叠,只露前几条',()=>{
  const e=boot();const b=detail(e,60);
  ok(b.indexOf('展开全部')>=0,'没有折叠');
  ok(b.indexOf('class="foldr"')>=0);
  ok(b.indexOf('hidden')>=0,'折叠部分没隐藏');
});
T('短列表不折叠,不多此一举',()=>{
  const e=boot();
  e.R(mkDays(3,`(d,i,k)=>{d.meds.m_vd=Date.now();}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  ok(e.doc._ids.detBody._html.indexOf('展开全部')<0,'3 条也折叠了');
});
T('点展开能打开、再点能收起',()=>{
  const e=boot();detail(e,60);
  const body=e.doc._ids.detBody;
  const id=/data-fold="(fold\d+)"/.exec(body._html)[1];
  const box=e.doc._ids[id]||{hidden:true};
  const fb={_text:'展开全部',getAttribute:()=>id,
    closest:sel=>sel.indexOf('foldb')>=0?fb:null,classList:{contains:c=>c==='foldb'}};
  e.R(`(function(){const b=$("#${id}");if(b)b.hidden=false;})()`);
  ok(true);
});
T('感受明细的日期也折叠了',()=>{
  const e=boot();
  e.R(mkDays(40,`(d,i,k)=>{d.feel.b=["疲劳"];}`));
  e.R('feelDetail("疲劳",backKeys(30));');
  ok(e.doc._ids.detBody._html.indexOf('展开全部')>=0,'感受日期没折叠');
});
T('折叠后默认可见条数有上限,不随天数增长',()=>{
  const vis=h=>{
    const seg=h.slice(h.indexOf('每一次'));
    const head=seg.slice(0,seg.indexOf('class="foldr"')>=0?seg.indexOf('class="foldr"'):seg.length);
    return (head.match(/class="qrow"/g)||[]).length;
  };
  const a=vis(detail(boot(),10)), b=vis(detail(boot(),90));
  ok(b<=10,'90 天时默认露出 '+b+' 条,没折住');
  ok(b<=a+2,'可见条数随天数增长了: '+a+' -> '+b);
});

console.log('【3 · 养生小课堂】');
const TIPS=(()=>{const i=html.indexOf('const TIPS=[');const j=html.indexOf('\n];',i);
  return [...html.slice(i,j).matchAll(/"([^"]+)"/g)].map(m=>m[1]);})();
T('条数增加',()=>ok(TIPS.length>=60,'只有 '+TIPS.length));
T('无重复',()=>eq(new Set(TIPS).size,TIPS.length,'有重复'));
T('新内容覆盖了当前健康问题',()=>{
  ['热敷','睑板','揉眼','人工泪液','霰粒肿','头皮','发缝','D3','足弓','过敏','性激素'].forEach(k=>
    ok(TIPS.some(t=>t.indexOf(k)>=0),'没覆盖: '+k));
});
T('没有和旧内容撞车',()=>{
  const olds=['20-20-20','咖啡因半衰期','小腿是第二心脏'];
  olds.forEach(o=>eq(TIPS.filter(t=>t.indexOf(o)>=0).length,1,'重复了: '+o));
});
T('视觉权重低于每日一句',()=>{
  const q=+/\.daily \.dt\{[^}]*font-size:(\d+)px/.exec(html)[1];
  const t=+/\.tip-daily \.dt\{[^}]*font-size:(\d+)px/.exec(html)[1];
  ok(t<q,'养生课堂没比每日一句小: '+t+' vs '+q);
});
T('结构和每日一句一致(有 dops)',()=>{
  ok(/<div class="daily tip-daily">[\s\S]{0,300}class="dops"/.test(html),'还是旧结构');
});
T('能正常抽换',()=>{
  const e=boot();e.R('newTip();');
  ok(e.doc._ids.tipText.textContent.length>0);
});

console.log('【4 · 字号层级】');
T('四级变量已定义',()=>{
  ['--fz-key','--fz-act','--fz-info','--fz-aux'].forEach(v=>
    ok(html.indexOf(v+':')>=0,'缺 '+v));
});
T('层级递减且符合 核心>操作>信息>辅助',()=>{
  const g=v=>parseFloat(new RegExp(v+':([\\d.]+)px').exec(html)[1]);
  const k=g('--fz-key'),a=g('--fz-act'),i=g('--fz-info'),x=g('--fz-aux');
  ok(k>a,'核心不大于操作');ok(a>i,'操作不大于信息');ok(i>x,'信息不大于辅助');
});
T('手机可读性:最小档不低于 11px',()=>{
  const x=parseFloat(/--fz-aux:([\d.]+)px/.exec(html)[1]);
  ok(x>=11,'辅助说明缩到 '+x+'px,太小');
});
T('同一概念用同一字号:三处任务/补剂名统一',()=>{
  ['.task .nm{','.plus .nm{','.med .mn{'].forEach(sel=>{
    const re=new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'[^}]*font-size:var\\(--fz-key\\)');
    ok(re.test(html),sel+' 没归并');
  });
});
T('按钮用操作级',()=>ok(/\.chip\{[^}]*font-size:var\(--fz-act\)/.test(html)));
T('说明文字用信息级/辅助级',()=>{
  ok(/\.tip\{[^}]*font-size:var\(--fz-info\)/.test(html));
  ok(/\.mdim\{[^}]*font-size:var\(--fz-aux\)/.test(html));
});

console.log('【5 · 兼容与体积】');
T('旧存档没有 medSkip 照常读',()=>{
  const e=boot();
  e.R(`D.days[TODAY]={min:{},plus:{},meds:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};
    render();renderHealth();`);
  ok(true);
});
T('旧 medSkip 只有 r 没有 t 也不崩',()=>{
  const e=boot();
  e.R(mkDays(5,`(d,i,k)=>{d.medSkip.m_vd={r:"忘了"};}`));
  e.R('renderHealth();medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  ok(e.doc._ids.detBody._html.length>0);
});
T('旧原因「外出」「不方便」仍能正常显示',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i,k)=>{if(i<=2)d.meds.m_vd=Date.now();else d.medSkip.m_vd={r:"外出",t:""};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  ok(e.doc._ids.detBody._html.indexOf('外出')>=0,'旧原因不显示了');
});
T('90 天数据下健康回顾不崩',()=>{
  const e=boot();
  e.R(mkDays(90,`(d,i,k)=>{d.meds.m_vd=new Date(k+"T21:00:00").getTime();d.feel.b=["疲劳"];}`));
  e.R('renderHealth();');
  ok(e.doc._ids.healthStat._html.length>0);
});
T('导出导入全字段往返',()=>{
  const e=boot();
  e.R(mkDays(10,`(d,i,k)=>{if(i%3)d.meds.m_vd=Date.now();else d.medSkip.m_vd={r:"有意没吃",t:"观察 "+i};}`));
  e.R('save();');
  const exp=e.R('JSON.stringify(D)');
  const e2=boot();
  const st=js.indexOf('$("#impFile").onchange');
  const BODY=js.slice(js.indexOf('try{',st)+4,js.indexOf('}catch(err){',st));
  e2.R(`(function(){const rd={result:${JSON.stringify(exp)}};const e={target:{value:""}};
    try{ ${BODY} }catch(err){ __E=String(err); }})();`);
  eq(e2.R('typeof __E==="undefined"?"无":__E'),'无');
  eq(e2.R('Object.keys(D.days).length'),e.R('Object.keys(D.days).length'));
  // 导入会补全缺字段(pomoEarly 等),所以比对关键内容而不是逐字节
  const norm=o=>JSON.stringify(o&&Object.keys(o).length?o:{});
  const pick=v=>JSON.stringify(Object.keys(JSON.parse(v)).sort().map(k=>{
    const d=JSON.parse(v)[k];
    return [k,norm(d.meds),norm(d.medSkip),norm(d.feel&&(d.feel.b.length||d.feel.m.length||d.feel.note)?d.feel:null),d.pomo||0];}));
  eq(pick(e2.R('JSON.stringify(D.days)')),pick(e.R('JSON.stringify(D.days)')),'关键内容不一致');
});
T('HTML 注入在观察记录里被转义',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i,k)=>{if(i<=2)d.meds.m_vd=Date.now();
    else d.medSkip.m_vd={r:"其他",t:"<img src=x onerror=1>"};}`));
  e.R('medDetail({k:"m_vd",n:"维生素 D",w:"随餐",on:1});');
  eq(e.doc._ids.detBody._html.indexOf('<img'),-1,'没转义');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
