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
const mkDays=(n,fn)=>`(function(){const mk=i=>{const t=studyNow();t.setDate(t.getDate()-i);return dkey(t);};
  for(let i=1;i<=${n};i++){const k=mk(i);D.days[k]={min:{},plus:{},meds:{},medSkip:{},medNote:{},
    drink:{},dtl:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0};(${fn})(D.days[k],i,k);}})()`;
const deepText=n=>{
  if(!n)return '';
  let t=(n._text||'')+' '+(n._html||'');
  (n._children||[]).forEach(c=>{t+=' '+deepText(c);});
  return t;
};
const dbox=e=>e.doc._ids.drinkBox;
const panelOf=e=>dbox(e)._children.find(c=>c.classList&&c.classList.contains('dtlbox'));

console.log('【1 · 糖度与温度二级选项】');
T('记一杯之后面板自动摊开',()=>{
  const e=boot();e.R('render();');
  dbox(e)._children[0]._children.find(c=>c._text==='记一杯').onclick();
  ok(panelOf(e),'没有自动展开糖冰面板');
});
T('面板里糖和冰各一排',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:1};drinkOpen="tea";render();');
  const rows=panelOf(e)._children.filter(c=>c.classList&&c.classList.contains('dz'));
  eq(rows.length,2,'一杯时不该出现选杯那一排');
  eq(JSON.parse(e.R('JSON.stringify(SUGAR)')).length,5);
  eq(JSON.parse(e.R('JSON.stringify(ICE)')).length,5);
});
T('点一下糖度就存住,不用打字',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:1};drinkOpen="tea";drinkCup=0;render();');
  const row=panelOf(e)._children.filter(c=>c.classList&&c.classList.contains('dz'))[0];
  row._children.find(c=>c._text==='半糖').onclick();
  eq(e.R('D.days[TODAY].dtl.tea[0].s'),'半糖','糖度没存住');
});
T('冰度同理',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:1};drinkOpen="tea";drinkCup=0;render();');
  const row=panelOf(e)._children.filter(c=>c.classList&&c.classList.contains('dz'))[1];
  row._children.find(c=>c._text==='去冰').onclick();
  eq(e.R('D.days[TODAY].dtl.tea[0].i'),'去冰','冰度没存住');
});
T('再点同一个选项等于取消',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");');
  e.R('setDtl(TODAY,"tea",0,"s","半糖");');
  eq(e.R('JSON.stringify(D.days[TODAY].dtl.tea[0])'),'{}','取消后没清干净');
});
T('两杯以上会出现选杯那一排',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:3};drinkOpen="tea";render();');
  const rows=panelOf(e)._children.filter(c=>c.classList&&c.classList.contains('dz'));
  eq(rows.length,3,'没有选杯行');
  eq(rows[0]._children.length,3,'杯数按钮个数不对');
});
T('不同杯可以记不同糖度',()=>{
  const e=boot();
  e.R('day(TODAY).drink={tea:2};setDtl(TODAY,"tea",0,"s","全糖");setDtl(TODAY,"tea",1,"s","无糖");');
  eq(e.R('D.days[TODAY].dtl.tea[0].s'),'全糖');
  eq(e.R('D.days[TODAY].dtl.tea[1].s'),'无糖');
});
T('v16 只有杯数的老记录读得出来,按没记补空',()=>{
  const e=boot();
  e.R('D.days[TODAY]={min:{},plus:{},meds:{},medSkip:{},set:[],pset:[],feel:{b:[],m:[],note:""},pomo:0,drink:{tea:2}};');
  eq(e.R('dtlOf(D.days[TODAY],"tea").length'),2,'杯数对不上');
  eq(e.R('JSON.stringify(dtlOf(D.days[TODAY],"tea")[0])'),'{}','该是空的');
});
T('减一杯会把最后一杯的糖冰一起去掉',()=>{
  const e=boot();
  e.R('day(TODAY).drink={tea:2};setDtl(TODAY,"tea",0,"s","全糖");setDtl(TODAY,"tea",1,"s","无糖");render();');
  dbox(e)._children[0]._children.find(c=>c._text==='−').onclick();
  eq(e.R('D.days[TODAY].drink.tea'),1);
  eq(e.R('D.days[TODAY].dtl.tea.length'),1,'糖冰没跟着减');
  eq(e.R('D.days[TODAY].dtl.tea[0].s'),'全糖','减错了那一杯');
});
T('减到零时面板收起、字段清空',()=>{
  const e=boot();
  e.R('day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");drinkOpen="tea";render();');
  dbox(e)._children[0]._children.find(c=>c._text==='−').onclick();
  eq(e.R('("tea" in D.days[TODAY].drink)'),false,'杯数没清');
  eq(e.R('("tea" in (D.days[TODAY].dtl||{}))'),false,'糖冰没清');
  eq(e.R('drinkOpen'),null,'面板没收起');
});
T('补记:点行本身能重新摊开面板',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:1};drinkOpen=null;render();');
  dbox(e)._children[0].onclick({target:{tagName:'SPAN'}});
  eq(e.R('drinkOpen'),'tea','点行没展开');
});
T('零杯时点行不会展开空面板',()=>{
  const e=boot();e.R('render();');
  dbox(e)._children[0].onclick({target:{tagName:'SPAN'}});
  eq(e.R('drinkOpen'),null,'零杯也展开了');
});
T('行上会把糖冰摘要显示出来',()=>{
  const e=boot();
  e.R('day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");setDtl(TODAY,"tea",0,"i","去冰");render();');
  ok(dbox(e)._children[0]._html.indexOf('半糖去冰')>=0,'摘要没出来');
});
T('糖冰面板里没有输入框,全是点的',()=>{
  const e=boot();e.R('day(TODAY).drink={tea:2};drinkOpen="tea";render();');
  const bad=[];
  const walk=n=>{if(n.tagName==='INPUT'||n.tagName==='TEXTAREA')bad.push(n.tagName);
    (n._children||[]).forEach(walk);};
  walk(panelOf(e));
  eq(bad.length,0,'面板里出现了要打字的控件');
});
T('导入会兜底 dtl 字段',()=>{
  ok(js.indexOf('if(!dy.dtl||typeof dy.dtl!=="object")dy.dtl={};')>=0,'缺 dtl 兜底');
  eq(js.split('if(!dy.dtl||typeof dy.dtl!=="object")dy.dtl={};').length-1,2,'load 和 import 应各有一处');
});

console.log('【2 · 感受标签不再只围着学习转】');
T('全天多了「心绪」一组',()=>{
  const e=boot();
  ok(e.R('FEEL_SETS.all.some(g=>g.g==="心绪")'),'没有心绪组');
});
T('晚上多了「心里」一组',()=>{
  const e=boot();
  ok(e.R('FEEL_SETS.pm.some(g=>g.g==="心里")'),'没有心里组');
});
T('点名要的四个词都在',()=>{
  const e=boot();
  ['难受','开心+','胡思乱想','伤春悲秋'].forEach(w=>{
    ok(e.R(`Object.keys(FEEL_SETS).some(s=>FEEL_SETS[s].some(g=>g.t.indexOf(${JSON.stringify(w)})>=0))`),
      '缺: '+w);
  });
});
T('心绪组正负两边都有',()=>{
  const e=boot();
  const t=JSON.parse(e.R('JSON.stringify(FEEL_SETS.all.find(g=>g.g==="心绪").t)'));
  ok(t.filter(x=>x.endsWith('+')).length>=4,'正向太少');
  ok(t.filter(x=>!x.endsWith('+')).length>=6,'负向太少');
});
T('同一时段内没有重复标签',()=>{
  const e=boot();
  const dup=e.R(`(function(){const bad=[];Object.keys(FEEL_SETS).forEach(s=>{const seen={};
    FEEL_SETS[s].forEach(g=>g.t.forEach(t=>{if(seen[t])bad.push(s+":"+t);seen[t]=1;}));});
    return bad.join(",");})()`);
  eq(dup,'','出现重复: '+dup);
});
T('新标签在渲染里点得到',()=>{
  const e=boot();e.R('feelSlot="all";render();');
  ok(deepText(e.doc._ids.feelGroups).indexOf('伤春悲秋')>=0,'全天渲染不出来');
  e.R('feelSlot="pm";render();');
  ok(deepText(e.doc._ids.feelGroups).indexOf('胡思乱想')>=0,'晚间渲染不出来');
});
T('新标签能进统计并按天去重',()=>{
  const e=boot();
  e.R(mkDays(3,`(d)=>{d.feel={b:[],m:["伤春悲秋"],note:"",slots:{pm:{b:[],m:["伤春悲秋"]}}};}`));
  e.R('renderHealth();');
  const m=/伤春悲秋[\s\S]{0,220}?<span class="sc">([^<]+)</.exec(e.doc._ids.healthStat._html);
  ok(m,'统计里没出现');
  eq(m[1],'3 天','双记了');
});
T('新标签的明细弹层打得开',()=>{
  const e=boot();
  e.R(mkDays(2,`(d)=>{d.feel={b:[],m:["胡思乱想"],note:""};}`));
  e.R('feelDetail("胡思乱想",backKeys(30));');
  ok(e.doc._ids.detBody._html.indexOf('出现在 2 天')>=0,'明细不对');
});

console.log('【3 · 打气语】');
T('渲染时会出一句',()=>{
  const e=boot();e.R('render();');
  ok(e.doc._ids.cheerText._text.length>0,'没出文案');
});
T('「换一句」能换掉',()=>{
  const e=boot();e.R('render();');
  const a=e.doc._ids.cheerText._text;
  let changed=false;
  for(let i=0;i<12;i++){
    e.doc._ids.cheerNew.onclick();
    if(e.doc._ids.cheerText._text!==a){changed=true;break;}
  }
  ok(changed,'换了十二次都没变');
});
T('勾一个目标会换一句',()=>{
  const e=boot();e.R('render();');
  const a=e.R('cheerSig');
  e.R('day(TODAY).min={p_word:1};render();');
  ok(e.R('cheerSig')!==a,'勾了目标没触发换句');
});
T('不动的时候不会自己乱跳',()=>{
  const e=boot();e.R('render();');
  const a=e.doc._ids.cheerText._text;
  e.R('render();render();');
  eq(e.doc._ids.cheerText._text,a,'没操作却换了句');
});
T('语料够多且不重复',()=>{
  const e=boot();
  const n=e.R('CHEERS.length');
  ok(n>=50,'条数太少: '+n);
  const dup=e.R('(function(){const s={};let n=0;CHEERS.forEach(x=>{if(s[x])n++;s[x]=1;});return n;})()');
  eq(dup,0,'有重复句');
});
T('连着两次不会出同一句',()=>{
  const e=boot();
  const bad=e.R(`(function(){let last="",n=0;for(let i=0;i<300;i++){const v=pickCheer();
    if(v===last)n++;last=v;}return n;})()`);
  eq(bad,0,'连续出现了同一句');
});
T('打气语里没有禁用词',()=>{
  const e=boot();
  const all=e.R('CHEERS.join("|")');
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>{
    ok(all.indexOf(w)<0,'出现禁用词: '+w);
  });
});
T('打气语里没有 emoji(颜文字不算)',()=>{
  const e=boot();
  const all=e.R('CHEERS.join("")');
  const bad=all.match(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu);
  eq(bad?bad.join(''):'','','出现 emoji: '+(bad||[]).join(''));
});
T('确实带了颜文字',()=>{
  const e=boot();
  const n=e.R('CHEERS.filter(x=>/[（(][^）)]*[ωᐛ・´`˘][^）)]*[）)]/.test(x)).length');
  ok(n>=8,'颜文字太少: '+n);
});

console.log('【4 · 周期对照与糖冰统计】');
function drinkEnv(weeks){
  const e=boot();
  e.R(mkDays(28,`(d,i)=>{
    if(i%2){d.drink={tea:1};d.dtl={tea:[{s:i%4?"半糖":"全糖",i:"去冰"}]};}
    d.feel={b:[],m:[i%3?"心情低落":"开心+"],note:""};}`));
  return e;
}
T('糖度分布出得来',()=>{
  const e=drinkEnv();
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('糖 · 记了')>=0,'没有糖度段');
  ok(b.indexOf('半糖及以下')>=0,'没有汇总口径');
});
T('没点糖度的杯子被排除在百分比外',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i)=>{d.drink={tea:1};if(i<=2)d.dtl={tea:[{s:"无糖"}]};}`));
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('记了 2 杯')>=0,'分母算错了');
  ok(b.indexOf('那 4 杯不算在里面')>=0,'没说明未记的杯数');
});
T('冰度分布出得来',()=>{
  const e=drinkEnv();
  e.R('drinkDetail("tea",backKeys(30));');
  ok(e.doc._ids.detBody._html.indexOf('去冰')>=0,'没有冰度段');
});
T('按周并排表能排出来',()=>{
  const e=drinkEnv();
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('按周并排看')>=0,'没有周表');
  ok(b.indexOf('其中心绪低落')>=0,'没有心绪列');
  ok(/class="wr"/.test(b),'没有数据行');
});
T('周数不够时如实说,不硬凑',()=>{
  const e=boot();
  e.R(mkDays(3,`(d)=>{d.drink={tea:1};d.feel={b:["疲劳"],m:[],note:""};}`));
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('才排得出这张表')>=0,'没有说明不足');
  ok(!/class="wr"/.test(b),'样本不够却排了表');
});
T('周表不会声称算出了相关性',()=>{
  const e=drinkEnv();
  e.R('drinkDetail("tea",backKeys(30));');
  const b=e.doc._ids.detBody._html;
  ok(b.indexOf('没有做相关计算')>=0,'没有讲清口径');
  // 注意:「不能说明谁导致谁」是免责句,不能一刀切地禁「导致」二字
  ['相关系数为','呈显著','会导致','导致了','说明你','证明'].forEach(w=>{
    ok(b.indexOf(w)<0,'出现了越界结论用词: '+w);
  });
  ok(b.indexOf('不能说明谁导致谁')>=0,'少了因果免责句');
});
T('经期内外对照要两边都够多才出',()=>{
  const e=boot();
  e.R(mkDays(20,`(d,i)=>{d.drink={tea:1};if(i<=5)d.cycle=["mid"];}`));
  e.R('drinkDetail("tea",backKeys(30));');
  ok(e.doc._ids.detBody._html.indexOf('经期内外')>=0,'够样本却没出');
  const e2=boot();
  e2.R(mkDays(20,`(d,i)=>{d.drink={tea:1};if(i<=2)d.cycle=["mid"];}`));
  e2.R('drinkDetail("tea",backKeys(30));');
  ok(e2.doc._ids.detBody._html.indexOf('经期内外')<0,'样本不够也出了');
});

console.log('【5 · 导出提醒】');
T('记录还少时不打扰',()=>{
  const e=boot();
  e.R(mkDays(3,`(d)=>{d.min={a:1};}`));
  e.R('renderExpWarn();');
  eq(e.doc._ids.expWarn.hidden,true,'才三天就提醒了');
});
T('从没导出过且记了一周,会提示',()=>{
  const e=boot();
  e.R(mkDays(8,`(d)=>{d.min={a:1};}`));
  e.R('renderExpWarn();');
  eq(e.doc._ids.expWarn.hidden,false,'没提示');
  ok(e.doc._ids.expWarn._text.indexOf('还没有导出过')>=0,'文案不对');
});
T('刚导出过就不提示',()=>{
  const e=boot();
  e.R(mkDays(8,`(d)=>{d.min={a:1};}`));
  e.R('D.lastExport=Date.now();renderExpWarn();');
  eq(e.doc._ids.expWarn.hidden,true,'刚导出还提醒');
});
T('超过两周才重新提示',()=>{
  const e=boot();
  e.R(mkDays(8,`(d)=>{d.min={a:1};}`));
  e.R('D.lastExport=Date.now()-13*864e5;renderExpWarn();');
  eq(e.doc._ids.expWarn.hidden,true,'13 天就提醒了');
  e.R('D.lastExport=Date.now()-15*864e5;renderExpWarn();');
  eq(e.doc._ids.expWarn.hidden,false,'15 天还不提醒');
  ok(e.doc._ids.expWarn._text.indexOf('15 天前')>=0,'天数不对');
});
T('提醒文案只讲事实,不催',()=>{
  const e=boot();
  e.R(mkDays(8,`(d)=>{d.min={a:1};}`));
  e.R('renderExpWarn();');
  const t=e.doc._ids.expWarn._text;
  ['必须','应该','赶紧','不许','否则','浪费'].forEach(w=>ok(t.indexOf(w)<0,'出现禁用词: '+w));
  ok(t.indexOf('清一次缓存')>=0,'没说清风险是什么');
});
T('导出成功后会记下时间',()=>{
  ok(js.indexOf('D.lastExport=Date.now();')>=0,'导出没记时间戳');
});

console.log('【6 · 各科上次记录】');
T('列得出每一科上次是哪天',()=>{
  const e=boot();
  e.R(mkDays(6,`(d,i,k)=>{d.set=[{k:"a",n:"英语阅读",s:"英语",mins:30}];if(i===2)d.min={a:1};}`));
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  ok(r['英语'],'没有英语');
  eq(r['英语'].gap,2,'间隔天数不对');
});
T('清单里挂着但一次没做的也会列出来',()=>{
  const e=boot();
  e.R('D.minList=[{k:"x1",n:"现代汉语 1 节",s:"现代汉语",mins:25}];');
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  ok(r['现代汉语'],'零记录的科目被漏掉了');
  eq(r['现代汉语'].k,'','不该有日期');
});
T('暂停的科目不算进来',()=>{
  const e=boot();
  e.R('D.minList=[{k:"x1",n:"文献学",s:"文献学",mins:25,off:1}];');
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  eq(!!r['文献学'],false,'暂停的也列了');
});
T('加码项也算作碰过',()=>{
  const e=boot();
  e.R(mkDays(4,`(d,i)=>{d.pset=[{k:"p1",n:"真题一套",s:"真题",mins:30}];if(i===1)d.plus={p1:1};}`));
  const r=JSON.parse(e.R('JSON.stringify(subjLastSeen())'));
  eq(r['真题'].gap,1,'加码没算进去');
});
T('渲染到统计栏里,按最久没碰排在前',()=>{
  const e=boot();
  e.R(mkDays(9,`(d,i)=>{d.set=[{k:"a",n:"英语",s:"英语",mins:30},{k:"b",n:"现汉",s:"现代汉语",mins:25}];
    if(i===1)d.min={a:1};if(i===8)d.min={a:1,b:1};}`));
  e.R('renderTimeStat();');
  const h=e.doc._ids.timeStat._html;
  ok(h.indexOf('各科上次记录')>=0,'没有这一段');
  ok(h.indexOf('现代汉语')<h.indexOf('英语')||h.indexOf('8 天前')>=0,'排序或天数不对');
});
T('这一段的文案不带责备',()=>{
  const e=boot();
  e.R(mkDays(9,`(d,i)=>{d.set=[{k:"a",n:"英语",s:"英语",mins:30}];if(i===1)d.min={a:1};}`));
  e.R('renderTimeStat();');
  const h=e.doc._ids.timeStat._html;
  ['必须','应该','赶紧','别偷懒','不许','否则','浪费','拖'].forEach(w=>
    ok(h.indexOf(w)<0,'出现了责备用词: '+w));
  ok(h.indexOf('不代表做错了什么')>=0,'缺少留余地的说明');
});

console.log('【7 · 整体不回归】');
T('四个栏目一起画不抛错',()=>{
  const e=boot();
  e.R(`day(TODAY).drink={tea:2,coffee:1};
    setDtl(TODAY,"tea",0,"s","半糖");setDtl(TODAY,"tea",0,"i","去冰");
    day(TODAY).meds={m_vd:Date.now()};day(TODAY).medNote={m_vd:"随餐"};
    day(TODAY).feel={b:["手脚冰凉"],m:["伤春悲秋"],note:"",slots:{pm:{b:[],m:["胡思乱想"]}}};
    render();renderHealth();renderTimeStat();kyOpen=true;renderKy();renderExpWarn();`);
  ok(e.doc._ids.cheerText._text.length>0);
  ok(e.doc._ids.healthStat._html.length>50);
});
T('新字段都会进导出快照',()=>{
  const e=boot();
  e.R('D.lastExport=1;day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","无糖");');
  const dump=e.R('JSON.stringify(D)');
  ['dtl','lastExport','drink'].forEach(f=>ok(dump.indexOf(f)>=0,'缺字段: '+f));
});
T('版本号格式正确且只有一处',()=>{
  // v21.1 起版本号可以带小版本号(v21.1),正则放开一位小数位
  const m=html.match(/id="verTag">(v\d+(?:\.\d+)? · \d{2}\/\d{2})</);
  ok(m,'顶部没有合法的版本号');
  eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
});

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
