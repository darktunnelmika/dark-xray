const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function build(){
 class Element{
  constructor({id='',name='',editable=false}={}){this.id=id;this.name=name;this.editable=editable;this.dataset={};this.selectionStart=0;this.selectionEnd=0;this.attrs={};this.focused=false;this.children=new Set();}
  closest(sel){return this.editable&&sel.includes('input')?this:null;}
  getAttribute(k){return k==='name'?this.name:(this.attrs[k]||null);}
  setAttribute(k,v){this.attrs[k]=String(v);}
  removeAttribute(k){delete this.attrs[k];}
  contains(el){return this.children.has(el);}
  focus(){this.focused=true;doc.activeElement=this;}
  setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}
 }
 const content=new Element({id:'content'}), oldInput=new Element({id:'search',editable:true});oldInput.selectionStart=2;oldInput.selectionEnd=4;content.children.add(oldInput);
 const byId={content,search:oldInput};
 const doc={activeElement:oldInput,getElementById:id=>byId[id]||null,getElementsByName:()=>[],querySelectorAll:()=>[]};
 let refreshCalls=0,renderCalls=0,loadCalls=0,scroll=null;
 const ctx={console,Element,document:doc,state:{busy:false},load:async()=>{loadCalls++;},window:{scrollX:11,scrollY:29,scrollTo:v=>{scroll=v;}},requestAnimationFrame:fn=>fn(),refresh:async()=>{refreshCalls++;},renderPage:async()=>{renderCalls++;const next=new Element({id:'search',editable:true});content.children=new Set([next]);byId.search=next;},};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','ui-stability.js'),'utf8'),ctx,{filename:'ui-stability.js'});
 return {ctx,doc,content,oldInput,byId,get refreshCalls(){return refreshCalls},get renderCalls(){return renderCalls},get loadCalls(){return loadCalls},get scroll(){return scroll}};
}

test('active editor receives data-only refresh without destroying focused DOM',async()=>{
 const t=build();await t.ctx.refresh();assert.equal(t.refreshCalls,0);assert.equal(t.loadCalls,1);assert.equal(t.doc.activeElement,t.oldInput);assert.equal(t.ctx.state.busy,false);
 t.doc.activeElement=t.content;await t.ctx.refresh();assert.equal(t.refreshCalls,1);assert.equal(t.loadCalls,1);
});

test('render preserves focus, selection and scroll position',async()=>{
 const t=build();await t.ctx.renderPage();assert.equal(t.renderCalls,1);
 const next=t.byId.search;assert.equal(t.doc.activeElement,next);assert.equal(next.focused,true);
 assert.equal(next.selectionStart,2);assert.equal(next.selectionEnd,4);
 assert.deepEqual(t.scroll,{left:11,top:29,behavior:'auto'});
 assert.equal(t.content.attrs['aria-busy'],undefined);
});
