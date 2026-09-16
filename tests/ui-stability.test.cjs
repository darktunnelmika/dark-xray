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
 const content=new Element({id:'content'}),oldInput=new Element({id:'search',editable:true});oldInput.selectionStart=2;oldInput.selectionEnd=4;content.children.add(oldInput);
 const byId={content,search:oldInput};
 const doc={activeElement:oldInput,getElementById:id=>byId[id]||null,getElementsByName:()=>[],querySelectorAll:()=>[]};
 let refreshCalls=0,renderCalls=0,loadCalls=0,scroll=null,ctx;
 ctx={console,Element,document:doc,state:{busy:false,page:'clients'},load:async()=>{loadCalls++;},window:{scrollX:11,scrollY:29,scrollTo:v=>{scroll=v;}},requestAnimationFrame:fn=>fn(),refresh:async()=>{refreshCalls++;},renderPage:async()=>{renderCalls++;const next=new Element({id:'search',editable:true});content.children=new Set([next]);byId.search=next;},runAction:async act=>{if(act==='refresh')await ctx.refresh();}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','ui-stability.js'),'utf8'),ctx,{filename:'ui-stability.js'});
 return {ctx,doc,content,oldInput,byId,get refreshCalls(){return refreshCalls},get renderCalls(){return renderCalls},get loadCalls(){return loadCalls},get scroll(){return scroll}};
}

test('active editor receives data-only timer refresh without destroying focused DOM',async()=>{
 const t=build();await t.ctx.refresh();assert.equal(t.refreshCalls,0);assert.equal(t.loadCalls,1);assert.equal(t.doc.activeElement,t.oldInput);assert.equal(t.ctx.state.busy,false);
 t.doc.activeElement=t.content;await t.ctx.refresh();assert.equal(t.refreshCalls,1);assert.equal(t.loadCalls,1);
});

test('explicit refresh action bypasses the active-editor render guard',async()=>{
 const t=build();await t.ctx.runAction('refresh',{});assert.equal(t.refreshCalls,1);assert.equal(t.loadCalls,0);
});

test('same-page render preserves focus, selection and scroll position',async()=>{
 const t=build();await t.ctx.renderPage();assert.equal(t.renderCalls,1);
 const next=t.byId.search;assert.equal(t.doc.activeElement,next);assert.equal(next.focused,true);
 assert.equal(next.selectionStart,2);assert.equal(next.selectionEnd,4);
 assert.equal(t.scroll.left,11);assert.equal(t.scroll.top,29);assert.equal(t.scroll.behavior,'auto');
 assert.equal(t.content.attrs['aria-busy'],undefined);
});

test('page navigation starts at top and does not refocus a stale control',async()=>{
 const t=build();t.ctx.state.page='finance';await t.ctx.renderPage();
 const next=t.byId.search;assert.equal(next.focused,false);
 assert.equal(t.scroll.left,0);assert.equal(t.scroll.top,0);assert.equal(t.scroll.behavior,'auto');
});
