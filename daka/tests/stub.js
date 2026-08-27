// 测试桩 —— 只认真实 HTML 里存在的 id,绝不按需创建
const fs=require('fs');

function makeEl(tag){
  const e={
    tagName:(tag||'div').toUpperCase(),
    _children:[],_html:'',_text:'',
    style:{},classList:null,dataset:{},attrs:{},
    hidden:false,value:'',checked:false,tabIndex:0,disabled:false,
    parentNode:null,
  };
  let cls='';
  Object.defineProperty(e,'className',{
    get:()=>cls,
    set:v=>{cls=String(v);}
  });
  e.classList={
    contains:c=>cls.split(/\s+/).indexOf(c)>=0,
    add:c=>{if(!e.classList.contains(c))cls=(cls+' '+c).trim();},
    remove:c=>{cls=cls.split(/\s+/).filter(x=>x!==c).join(' ');},
    toggle:(c,f)=>{const has=e.classList.contains(c);
      const want=(f===undefined)?!has:!!f;
      if(want)e.classList.add(c);else e.classList.remove(c);}
  };
  Object.defineProperty(e,'innerHTML',{
    get:()=>e._html,
    set:v=>{e._html=String(v);e._children=[];e._text='';}  // 设 innerHTML 会清空子节点与旧文本
  });
  Object.defineProperty(e,'textContent',{
    // 真实 DOM 里 textContent 会把 innerHTML 的标签剥掉返回纯文本。
    // 桩以前只返回 _text,导致「用 innerHTML 渲染的内容读不到」——那是桩的缺陷,不是产品的。
    get:()=>{
      if(e._text)return e._text;
      if(e._html)return e._html.replace(/<[^>]*>/g,'');
      return '';
    },
    set:v=>{e._text=String(v);}                  // 注意:不清 _html,与浏览器不同处已在静态层单独测
  });
  Object.defineProperty(e,'children',{get:()=>e._children});
  e.appendChild=c=>{c.parentNode=e;e._children.push(c);return c;};
  e.removeChild=c=>{e._children=e._children.filter(x=>x!==c);};
  e.remove=()=>{if(e.parentNode)e.parentNode.removeChild(e);};
  e.setAttribute=(k,v)=>{e.attrs[k]=String(v);
    if(k.slice(0,5)==='data-')e.dataset[k.slice(5).replace(/-(\w)/g,(m,c)=>c.toUpperCase())]=String(v);};
  e.getAttribute=k=>(k in e.attrs)?e.attrs[k]:null;
  e.addEventListener=(t,f)=>{(e._ev=e._ev||{})[t]=(e._ev[t]||[]).concat([f]);};
  e.removeEventListener=()=>{};
  e.focus=()=>{};e.blur=()=>{};e.scrollIntoView=()=>{};
  e.getContext=()=>({clearRect(){},beginPath(){},arc(){},fill(){},
    fillRect(){},save(){},restore(){},translate(){},rotate(){},
    moveTo(){},lineTo(){},stroke(){},closePath(){},set fillStyle(v){},set strokeStyle(v){}});
  e.getBoundingClientRect=()=>({top:0,left:0,width:100,height:100,bottom:100,right:100});
  e.closest=sel=>{
    let cur=e;
    while(cur){
      if(sel.split(',').some(one=>matchSel(cur,one.trim())))return cur;
      cur=cur.parentNode;
    }
    return null;
  };
  e.querySelector=()=>null;
  e.querySelectorAll=()=>[];
  return e;
}
function matchSel(el,sel){
  // 只支持 .a.b 和 [attr] 组合,够用
  const parts=sel.match(/(\.[A-Za-z0-9_-]+|\[[^\]]+\])/g)||[];
  return parts.every(p=>{
    if(p[0]==='.')return el.classList.contains(p.slice(1));
    const k=p.slice(1,-1);return el.getAttribute(k)!==null;
  });
}

function build(htmlPath){
  const html=fs.readFileSync(htmlPath,'utf8');
  const ids=(html.match(/\bid="([^"]+)"/g)||[]).map(x=>x.slice(4,-1));
  const store={};
  ids.forEach(id=>{
    const m=new RegExp('<(\\w+)[^>]*\\bid="'+id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'"').exec(html);
    const el=makeEl(m?m[1]:'div');
    el.id=id;
    // 还原 HTML 里写死的 type / hidden
    const tagm=new RegExp('<\\w+[^>]*\\bid="'+id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'"[^>]*>').exec(html);
    if(tagm){
      const t=/\btype="([^"]+)"/.exec(tagm[0]);if(t)el.type=t[1];
      if(/\bhidden\b/.test(tagm[0]))el.hidden=true;
    }
    store[id]=el;
  });
  const body=makeEl('body');
  const doc={
    body,
    documentElement:makeEl('html'),
    createElement:makeEl,
    createDocumentFragment:()=>makeEl('frag'),
    getElementById:id=>store[id]||null,
    querySelector:sel=>{
      if(sel[0]==='#')return store[sel.slice(1)]||null;
      if(sel==='body')return body;
      return null;                       // 关键:不按需创建
    },
    querySelectorAll:()=>[],
    addEventListener:()=>{},
    removeEventListener:()=>{},
    _ids:store, _html:html
  };
  return doc;
}
/* 同步 FileReader。导入(impFile.onchange)整条路径在 v18 之前
   从未被真正执行过 —— 八套测试里所有对 impFile 的引用都是源码字符串切片。
   没有它,「导入」这个功能等于没测。传进去的 file 对象用 __text 携带内容。 */
function FileReaderStub(){
  this.result=null;this.onload=null;this.onerror=null;
  this.readAsText=(f)=>{
    if(!f||typeof f.__text!=="string"){this.onerror&&this.onerror();return;}
    this.result=f.__text;this.onload&&this.onload();
  };
}

module.exports={build,makeEl,FileReaderStub};
