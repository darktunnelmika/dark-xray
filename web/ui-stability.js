/* DARK XRAY UI Stability — preserve operator context across live refreshes. */
(function(){
'use strict';
if(typeof refresh!=='function'||typeof renderPage!=='function'||typeof load!=='function')return;
const baseRefresh=refresh,baseRenderPage=renderPage,baseRunAction=typeof runAction==='function'?runAction:null;
let renderedPage=state?.page||'',forceRefresh=false;
const editable=el=>!!el&&el instanceof Element&&!!el.closest('input,textarea,select,[contenteditable="true"]');
const keyFor=el=>{
 if(!el||!(el instanceof Element))return null;
 if(el.id)return {type:'id',value:el.id};
 if(el.getAttribute('name'))return {type:'name',value:el.getAttribute('name')};
 if(el.dataset?.stableFocus)return {type:'data',value:el.dataset.stableFocus};
 return null;
};
const findKey=key=>{
 if(!key)return null;
 if(key.type==='id')return document.getElementById(key.value);
 const list=key.type==='name'?document.getElementsByName(key.value):document.querySelectorAll('[data-stable-focus]');
 return Array.from(list||[]).find(el=>key.type!=='data'||el.dataset.stableFocus===key.value)||null;
};
function capture(){
 const active=document.activeElement,inside=active&&document.getElementById('content')?.contains(active);
 let selection=null;
 if(inside&&typeof active.selectionStart==='number')selection=[active.selectionStart,active.selectionEnd];
 return {x:window.scrollX||0,y:window.scrollY||0,key:inside?keyFor(active):null,selection};
}
function restore(snap){
 const target=findKey(snap.key);
 if(target){
  try{target.focus({preventScroll:true});}catch{try{target.focus();}catch{}}
  if(snap.selection&&typeof target.setSelectionRange==='function'){
   try{target.setSelectionRange(snap.selection[0],snap.selection[1]);}catch{}
  }
 }
 requestAnimationFrame(()=>window.scrollTo({left:snap.x,top:snap.y,behavior:'auto'}));
}
renderPage=async function(){
 const page=state?.page||'',samePage=page===renderedPage,snap=capture();
 const content=document.getElementById('content');
 if(content)content.setAttribute('aria-busy','true');
 try{return await baseRenderPage();}
 finally{
  const next=document.getElementById('content');
  if(next)next.removeAttribute('aria-busy');
  const stillSame=state?.page===page;
  if(samePage&&stillSame)restore(snap);
  else requestAnimationFrame(()=>window.scrollTo({left:0,top:0,behavior:'auto'}));
  renderedPage=state?.page||page;
 }
};
refresh=async function(){
 const content=document.getElementById('content');
 // Timer refreshes keep network state fresh while an operator is typing, but do
 // not replace the focused DOM subtree. An explicit top-bar refresh is forced.
 if(!forceRefresh&&content&&content.contains(document.activeElement)&&editable(document.activeElement)){
  if(state?.busy)return;
  state.busy=true;
  try{await load();}finally{state.busy=false;}
  return;
 }
 return baseRefresh();
};
if(baseRunAction){
 runAction=async function(act,el){
  if(act!=='refresh')return baseRunAction(act,el);
  forceRefresh=true;
  try{return await baseRunAction(act,el);}
  finally{forceRefresh=false;}
 };
}
})();
