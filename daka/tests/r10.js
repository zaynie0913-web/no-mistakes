const fs=require('fs'),vm=require('vm'),{build,FileReaderStub}=require('./stub.js');
const html=fs.readFileSync('app.html','utf8');
const js=/<script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
let pass=0,fail=0;
const T=(n,f)=>{try{f();pass++;}catch(e){fail++;console.log('  ✗ '+n+' :: '+e.message);}};
const TA=(n,f)=>queue.push([n,f]);
const queue=[];
const ok=(c,m)=>{if(!c)throw new Error(m||'断言失败');};
const eq=(a,b,m)=>{if(a!==b)throw new Error((m||'')+' 期望 '+JSON.stringify(b)+' 实得 '+JSON.stringify(a));};
const tick=()=>new Promise(r=>setImmediate(r));

function boot(seedRaw,fakeIso){
  const doc=build('app.html');const store={};
  if(seedRaw!==undefined)store['bnu-tracker-v1']=seedRaw;
  const st={off:fakeIso?(new Date(fakeIso).getTime()-Date.now()):0};
  const DateC=function(...a){return a.length?new Date(...a):new Date(Date.now()+st.off);};
  DateC.now=()=>Date.now()+st.off;DateC.prototype=Date.prototype;
  const g={document:doc,localStorage:{getItem:k=>store[k]===undefined?null:store[k],
      setItem:(k,v)=>{store[k]=v;},removeItem:k=>{delete store[k];}},
    alert:m=>{g.__alert=m;},confirm:()=>{g.__confirmed=true;return true;},prompt:()=>null,
    setTimeout:()=>0,clearTimeout:()=>{},setInterval:()=>1,clearInterval:()=>{},
    requestAnimationFrame:()=>0,navigator:{userAgent:'node'},
    console:{log(){},warn(){},error(){}},Date:DateC,Math,JSON,String,Number,Object,Array,RegExp,Error,
    isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,
    Blob:function(a){this.__a=a;},URL:{createObjectURL:()=>'blob:x',revokeObjectURL(){}},Promise,
    FileReader:FileReaderStub};
  g.window=g;g.globalThis=g;g.self=g;
  const ctx=vm.createContext(g);vm.runInContext(js,ctx,{filename:'app.js'});
  return {doc,ctx,g,store,st,R:c=>vm.runInContext(c,ctx)};
}
// 走真实的 onchange 入口,不是直接调内部函数
const doImport = (e,text)=>{
  e.g.__f={__text:text};
  e.R('$("#impFile").onchange({target:{files:[__f],value:""}});');
};

(async()=>{

console.log('【1 · 导入改为两阶段提交】');
{
  await (async()=>{
    const e=boot();await tick();
    e.R('D.customs=["原有"];D.minList=[{k:"keep",n:"本机项",s:"英语",mins:20}];');
    const before=e.R('JSON.stringify(D)');
    doImport(e,'{"days":{ 这不是 json');
    await tick();
    T('文件根本不是 json 时,D 一个字节都没动',()=>{
      eq(e.R('JSON.stringify(D)'),before,'D 被改了');
      ok(e.doc._ids.bMsg._text.indexOf('文件读不了')>=0,'没有给出提示');
    });
  })();

  await (async()=>{
    const e=boot();await tick();
    e.R('D.customs=["原有"];');
    const before=e.R('JSON.stringify(D)');
    doImport(e,'"我是一个字符串不是对象"');
    await tick();
    T('顶层不是对象时,D 也不动',()=>{
      eq(e.R('JSON.stringify(D)'),before,'D 被改了');
    });
  })();

  await (async()=>{
    const e=boot();await tick();
    doImport(e,JSON.stringify({days:{"2026-08-11":null,"2026-08-12":{min:{},plus:{}}},
      customs:["来自导入"],teaRef:3}));
    await tick();
    T('某天是 null:剔掉它,其余照常导入,不再半途崩溃',()=>{
      eq(e.R('Object.keys(D.days).filter(k=>D.days[k]===null).length'),0,'null 留在数据里了');
      ok(e.R('!!D.days["2026-08-12"]'),'好的那天没导进来');
      eq(e.R('JSON.stringify(D.customs)'),'["来自导入"]','偏好没跟着过来');
      eq(e.R('D.teaRef'),3,'teaRef 没过来');
      ok(e.doc._ids.bMsg._text.indexOf('跳过读不懂的 1 天')>=0,
        '没如实报告跳过数: '+e.doc._ids.bMsg._text);
    });
  })();

  await (async()=>{
    const e=boot();await tick();
    doImport(e,JSON.stringify({days:{"2026-08-12":{min:{},plus:{},set:[],pset:[],
      feel:{b:[],m:[],note:""},pomo:3}},customs:["好的"],teaRef:3,kyFix:{exam:"2026-12-26"}}));
    await tick();
    T('正常文件仍然完整导入(不能只顾防守)',()=>{
      eq(e.R('D.days["2026-08-12"].pomo'),3,'天数据不对');
      eq(e.R('D.teaRef'),3);
      eq(e.R('D.kyFix.exam'),'2026-12-26');
      ok(e.doc._ids.bMsg._text.indexOf('导入完成')>=0,'没报成功');
    });
  })();

  await (async()=>{
    const e=boot();await tick();
    doImport(e,JSON.stringify({days:{"2026-08-12":{min:"不是对象",plus:{},pomo:1}}}));
    await tick();
    T('天记录里的子字段类型不对:就地补默认值,不整个丢掉',()=>{
      eq(e.R('typeof D.days["2026-08-12"].min'),'object','min 没被兜住');
      eq(e.R('D.days["2026-08-12"].pomo'),1,'把好数据也丢了');
      ok(e.doc._ids.bMsg._text.indexOf('补全旧字段')>=0,'没报补全数');
    });
  })();

  T('导入路径里的赋值全部走暂存区 T,不直接写 D',()=>{
    const i=js.indexOf('$("#impFile").onchange');
    const seg=js.slice(i,js.indexOf('rd.readAsText(f);',i));
    const body=seg.slice(seg.indexOf('const T=JSON.parse'),seg.indexOf('D=T;'));
    const bad=body.split('\n').filter(l=>/(^|[^.\w])D\.\w+\s*=/.test(l));
    eq(bad.length,0,'仍有直接写 D 的行:\n'+bad.join('\n'));
  });
  T('只有校验全部走完才整体换过去',()=>{
    const i=js.indexOf('$("#impFile").onchange');
    const seg=js.slice(i,js.indexOf('rd.readAsText(f);',i));
    ok(seg.indexOf('D=T;')>=0,'没有整体替换');
    ok(seg.indexOf('D=T;')>seg.indexOf('dropped++'),'替换发生在校验之前');
  });
  T('导入的类型防护和 load 的那套一致',()=>{
    ok(js.split('if(!dy||typeof dy!=="object")').length-1>=2,
      'load 和 import 没有都做「这天是不是对象」的判断');
  });
}

console.log('【2 · 跨周不刷新】');
{
  const e=boot(undefined,'2026-08-16T22:00:00');
  await tick();
  const tw0=e.R('TW');
  T('周日晚上的 TW 正确',()=>{ eq(tw0,e.R('wkey(studyNow())')); });
  e.st.off+=7*3600*1000;                       // 同一会话推进到周一 05:00
  e.R('refreshDay();');
  T('同一会话跨过周一 4 点后,TW 会跟着走',()=>{
    eq(e.R('TW'),e.R('wkey(studyNow())'),'TW 没跟上');
    ok(e.R('TW')!==tw0,'TW 完全没变');
  });
  T('TODAY 也同时翻篇',()=>{ eq(e.R('TODAY'),e.R('dkey(studyNow())')); });
  T('新一周的复盘存进新一周的键',()=>{
    e.R('D.reviews={};D.reviews[TW]=["新一周","","",""];');
    eq(e.R('JSON.stringify(Object.keys(D.reviews))'),JSON.stringify([e.R('wkey(studyNow())')]),
      '存错周了');
  });
  T('跨周会重置复盘回填标记,不把上周的答案留在框里',()=>{
    ok(js.indexOf('if(w!==TW){TW=w;rvFilled=false;}')>=0,'跨周没有重置 rvFilled');
  });
  T('TW 声明成 let,不再是启动时算一次的常量',()=>{
    ok(/(^|\n)let TW=wkey/.test(js),'TW 还是 const');
  });
  T('同一周内反复 refreshDay 不会瞎改 TW',()=>{
    const e2=boot();
    const a=e2.R('TW');
    e2.R('refreshDay();refreshDay();refreshDay();');
    eq(e2.R('TW'),a,'同一周内 TW 被改动了');
  });
}

console.log('【3 · 存档损坏时不许静默覆盖】');
{
  await (async()=>{
    const e=boot('{"days":{"2026-08-01":{"min":{},"pl');
    await e.R('load()');
    T('解析失败会被捕获,而不是吞掉',()=>{
      ok(e.R('LOAD_BROKEN!==null'),'没有捕获');
      eq(e.R('LOAD_BROKEN.length'),35,'原文长度不对');
    });
    const before=e.store['bnu-tracker-v1'];
    await e.R('save()');
    T('冻结写入:save() 不会覆盖掉原文',()=>{
      eq(e.store['bnu-tracker-v1'],before,'原文被覆盖了');
    });
    e.R('render();');
    T('页面上给出明确警示,不是绿色「已保存」',()=>{
      const w=e.doc._ids.brokeWarn;
      eq(w.hidden,false,'警示条没显示');
      const t=String(w._html||'').replace(/<[^>]*>/g,'');
      ok(t.indexOf('读不出来')>=0,'没说清出了什么事');
      ok(t.indexOf('那不是你的记录')>=0,'没有点破「空白不等于新用户」');
      ok(t.indexOf('自动保存已经暂停')>=0,'没告知已冻结');
    });
    T('给了三个出口:先导出原文 / 去导入备份 / 放弃重来',()=>{
      const row=e.doc._ids.brokeWarn._children[0];
      const names=row._children.map(b=>b._text);
      eq(names.length,3,'出口数不对: '+names.join(','));
      ok(names[0].indexOf('导出')>=0,'第一个应该是导出原文');
      ok(names.some(x=>x.indexOf('导入')>=0),'缺少导入入口');
      ok(names.some(x=>x.indexOf('重新开始')>=0),'缺少放弃入口');
    });
    T('「放弃重来」要二次确认,确认后才解冻',()=>{
      const row=e.doc._ids.brokeWarn._children[0];
      row._children.find(b=>b._text.indexOf('重新开始')>=0).onclick();
      ok(e.g.__confirmed,'没有二次确认');
      ok(e.R('LOAD_BROKEN===null'),'没有解冻');
    });
  })();

  await (async()=>{
    const e=boot('{"days":{"2026-08-01":{"min":{},"pl');
    await e.R('load()');
    const before=e.store['bnu-tracker-v1'];
    doImport(e,JSON.stringify({days:{"2026-08-02":{min:{},plus:{},pomo:2}}}));
    await tick();
    T('拿备份导入成功,即视为修好,自动解冻并写入',()=>{
      ok(e.R('LOAD_BROKEN===null'),'导入成功却没解冻');
      eq(e.R('D.days["2026-08-02"].pomo'),2,'备份没导进来');
      ok(e.store['bnu-tracker-v1']!==before,'解冻后仍然写不进去');
    });
  })();

  await (async()=>{
    const e=boot();                       // 真正的新用户:localStorage 里什么都没有
    await e.R('load()');
    e.R('render();');
    T('真正的新用户不会看到这条警示',()=>{
      ok(e.R('LOAD_BROKEN===null'),'新用户被误判为损坏');
      eq(e.doc._ids.brokeWarn.hidden,true,'新用户看到了警示条');
    });
    const n=Object.keys(e.store).length;
    await e.R('save()');
    T('新用户照常能保存',()=>{ ok(Object.keys(e.store).length>=n,'新用户存不进去'); });
  })();

  await (async()=>{
    const e=boot(JSON.stringify({days:{},reviews:{},dictation:[],customs:["好的存档"]}));
    await e.R('load()');
    T('合法存档不受影响',()=>{
      ok(e.R('LOAD_BROKEN===null'),'合法存档被误判');
      eq(e.R('JSON.stringify(D.customs)'),'["好的存档"]','存档没读进来');
    });
  })();

  T('警示文案不吓唬人也不含糊,没有禁用词',()=>{
    const i=js.indexOf('function renderBroke');
    const seg=js.slice(i,js.indexOf('function renderExpWarn'));
    ['必须','应该','赶紧','别偷懒','不许','否则','浪费'].forEach(w=>
      ok(seg.indexOf(w)<0,'出现禁用词: '+w));
    const bad=seg.match(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/gu);
    eq(bad?bad.join(''):'','','出现 emoji');
  });
}

console.log('【4 · 不回归】');
{
  const e=boot();await tick();
  T('四栏一起画不抛错',()=>{
    e.R(`day(TODAY).drink={tea:1};setDtl(TODAY,"tea",0,"s","半糖");
      day(TODAY).feel={b:["手脚冰凉"],m:["伤春悲秋"],note:""};
      render();renderHealth();renderTimeStat();renderRvSum();renderExpWarn();renderBroke();`);
    ok(e.doc._ids.cheerText._text.length>0);
  });
  T('版本号格式正确且只有一处',()=>{
    const m=html.match(/id="verTag">(v\d+ · \d{2}\/\d{2})</);
    ok(m,'顶部没有合法的版本号');
    eq(html.split(m[1]).length-1,1,'版本号出现了不止一处');
  });
}

console.log('\n通过 '+pass+' / 失败 '+fail);
process.exit(fail?1:0);
})();
