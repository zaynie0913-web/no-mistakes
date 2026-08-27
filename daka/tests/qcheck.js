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
  vm.runInContext('D={days:{},reviews:{},dictation:[],customs:[]};',ctx);
  return {doc,R:c=>vm.runInContext(c,ctx)};
}
const L=(e,t)=>JSON.parse(e.R('JSON.stringify(quoteLines('+JSON.stringify(t)+'))'));

console.log('【分行规则】');
const e=boot();
T('对仗二句分两行',()=>eq(L(e,'捐躯赴国难,视死忽如归').join('|'),'捐躯赴国难,|视死忽如归'));
T('四分句对仗分四行',()=>eq(L(e,'知人者智,自知者明;胜人者有力,自胜者强').length,4));
T('无标点短句保持单段',()=>eq(L(e,'有志者事竟成').length,1));
T('长短悬殊的散文句不拆',()=>eq(L(e,'关键是你的目光而不是你的所见,抛弃优越感吧,那是思想的一大包袱').length,1));
T('超长引文不拆',()=>eq(L(e,'一个诗人应当把所有的东西,甚至包括不幸,视为对他的馈赠。不幸、挫折、耻辱、失败,这都是我们的工具').length,1));
T('句号也作为断点',()=>eq(L(e,'剪不断,理还乱,是离愁。别是一般滋味在心头').length,2));
T('问号也作为断点',()=>eq(L(e,'问渠那得清如许?为有源头活水来').length,2));
T('碎行被并进上一行',()=>{const r=L(e,'争渡,争渡,惊起一滩鸥鹭');eq(r.length,2);eq(r[0],'争渡,争渡,');});
T('原文一个字都没改',()=>{
  const src=['捐躯赴国难,视死忽如归','知人者智,自知者明;胜人者有力,自胜者强','剪不断,理还乱,是离愁。别是一般滋味在心头','争渡,争渡,惊起一滩鸥鹭'];
  src.forEach(t=>eq(L(e,t).join(''),t,'拼回去和原文不一致: '+t));
});
T('全库拼回去都等于原文',()=>{
  const bad=e.R(`(function(){const all=Q.concat(MYQ_SEED);let b=[];
    all.forEach(x=>{if(quoteLines(x[0]).join('')!==x[0])b.push(x[0]);});return JSON.stringify(b);})()`);
  eq(bad,'[]','有句子被改动了: '+bad);
});

console.log('【渲染与层级】');
T('分行时输出多个 .ln',()=>{
  const e2=boot();
  e2.R('paintQuote("捐躯赴国难,视死忽如归","曹植《白马篇》",0);');
  const h=e2.doc._ids.dqText._html;
  eq((h.match(/class="ln"/g)||[]).length,2);
});
T('单段时不包 .ln',()=>{
  const e2=boot();
  e2.R('paintQuote("有志者事竟成","《后汉书》",0);');
  ok(e2.doc._ids.dqText._html.indexOf('class="ln"')<0);
});
T('textContent 读到的仍是完整原文',()=>{
  const e2=boot();
  e2.R('paintQuote("捐躯赴国难,视死忽如归","曹植《白马篇》",0);');
  eq(e2.doc._ids.dqText.textContent,'捐躯赴国难,视死忽如归');
});
T('HTML 注入被转义',()=>{
  const e2=boot();
  e2.R('paintQuote("<img src=x onerror=1>,<b>y</b>是坏东西","测试",0);');
  eq(e2.doc._ids.dqText._html.indexOf('<img'),-1);
  eq(e2.doc._ids.dqFrom._html.indexOf('<script'),-1);
});
T('歪歪严选是独立的轻标签,不再拼「·」',()=>{
  const e2=boot();
  e2.R('paintQuote("测试句","某人",1);');
  const h=e2.doc._ids.dqFrom._html;
  ok(h.indexOf('class="yy"')>=0,'没有独立标签');
  ok(h.indexOf('歪歪严选')>=0);
  ok(h.indexOf('· 歪歪严选')<0,'还在用旧的拼接写法');
});
T('内置句不显示歪歪严选',()=>{
  const e2=boot();
  e2.R('paintQuote("测试句","李白《将进酒》",0);');
  ok(e2.doc._ids.dqFrom._html.indexOf('歪歪严选')<0);
});
T('newQuote 走的是同一个绘制入口',()=>{
  const e2=boot();
  e2.R('D.qSeen=[];newQuote();');
  ok(e2.doc._ids.dqText._html.length>0,'没画出来');
  ok(e2.doc._ids.dqFrom._html.length>0);
});
T('加一句后立刻显示且带歪歪严选',()=>{
  const e2=boot();
  e2.doc._ids.myqText.value='人生忽如寄,寿无金石固';
  e2.doc._ids.myqFrom.value='古诗十九首';
  e2.R('$("#myqAdd").onclick();');
  eq(e2.doc._ids.dqText.textContent,'人生忽如寄,寿无金石固');
  ok(e2.doc._ids.dqFrom._html.indexOf('class="yy"')>=0);
  eq((e2.doc._ids.dqText._html.match(/class="ln"/g)||[]).length,2,'自己加的句子也该分行');
});

console.log('【样式与点击区】');
T('句子是最大字号',()=>{
  const dt=/\.daily \.dt\{[^}]*font-size:(\d+)px/.exec(html);
  const da=/\.daily \.da\{[^}]*font-size:([\d.]+)px/.exec(html);
  const bt=/\.daily \.dops button\{[^}]*font-size:([\d.]+)px/.exec(html);
  ok(dt&&da&&bt,'样式没解析到');
  ok(+dt[1]>=16,'句子字号偏小: '+dt[1]);
  ok(+dt[1]<=18,'句子字号过大,又变回标题了: '+dt[1]);
  ok(+dt[1]>parseFloat(da[1])+5,'句子与出处层级不够');
  ok(+dt[1]-parseFloat(bt[1])>=6,'句子与操作文字层级不够');
});
T('操作文字视觉轻:无边框、灰色',()=>{
  const seg=/\.daily \.dops button\{([^}]*)\}/.exec(html)[1];
  ok(/border:none/.test(seg),'还有边框');
  ok(/color:var\(--mo-3\)/.test(seg),'颜色不够轻');
});
T('操作点击区没有跟着变小',()=>{
  const seg=/\.daily \.dops button\{([^}]*)\}/.exec(html)[1];
  const m=/padding:(\d+)px/.exec(seg);
  ok(m&&+m[1]>=12,'上下 padding 太小,点击区不够: '+(m&&m[1]));
  const fs=parseFloat(/font-size:([\d.]+)px/.exec(seg)[1]);
  const h=fs*1.4+ (+m[1])*2;
  ok(h>=40,'点击区高度约 '+Math.round(h)+'px,不足 40px');
});
T('歪歪严选比出处更轻',()=>{
  const yy=/\.daily \.da \.yy\{([^}]*)\}/.exec(html)[1];
  ok(/opacity:0?\.[0-6]/.test(yy),'没有降低权重');
  ok(/font-size:10\.5px/.test(yy),'字号没比出处小');
  ok(!/background/.test(yy)&&!/border/.test(yy),'做成徽章了');
});
T('行距有呼吸感',()=>{
  const dt=/\.daily \.dt\{[^}]*line-height:([\d.]+)/.exec(html);
  ok(dt&&parseFloat(dt[1])>=1.9,'行距偏紧: '+(dt&&dt[1]));
});
T('长句有宽度上限',()=>{
  ok(/\.daily \.dt\{[^}]*max-width:\d+em/.test(html),'没有限制文本宽度');
});
T('没有加装饰(渐变/阴影/花体)',()=>{
  const seg=html.slice(html.indexOf('.daily{'),html.indexOf('.qhide{'));
  ok(!/gradient/.test(seg),'加了渐变');
  ok(!/box-shadow/.test(seg),'加了阴影');
  ok(!/cursive|fantasy/.test(seg),'用了花体');
});

console.log('【没动别的东西】');
T('随机算法未改',()=>ok(/先随机挑一类,再在类里挑一句/.test(js)&&/const QMEM=60/.test(js)));
T('hideQ 逻辑未改',()=>ok(/const hid=new Set\(D\.hideQ\|\|\[\]\)/.test(js)));
T('删除确认未改',()=>ok(/以后不再显示这一句/.test(js)));
T('导入逻辑未动 myQ/hideQ',()=>{
  const st=js.indexOf('$("#impFile").onchange');
  const seg=js.slice(st,js.indexOf('}catch(err){',st));
  ok(seg.indexOf('inc.myQ')>=0&&seg.indexOf('inc.hideQ')>=0);
});
T('分类随机未改',()=>ok(/function qCat\(from,mine\)/.test(js)));

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
