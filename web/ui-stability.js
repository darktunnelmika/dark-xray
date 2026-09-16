/* DARK XRAY UI Stability — preserve operator context across live refreshes. */
(function(){
'use strict';
if(typeof refresh!=='function'||typeof renderPage!=='function')return;
const baseRefresh=refresh,baseRenderPage=renderPage;
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
 const snap=capture();
 const content=document.getElementById('content');
 if(content)content.setAttribute('aria-busy','true');
 try{return await baseRenderPage();}
 finally{
  const next=document.getElementById('content');
  if(next)next.removeAttribute('aria-busy');
  restore(snap);
 }
};
refresh=async function(){
 const content=document.getElementById('content');
 // Timer refreshes should never destroy an actively edited control. Manual
 // refresh from the top bar moves focus to its button first and still proceeds.
 if(content&&content.contains(document.activeElement)&&editable(document.activeElement))return;
 return baseRefresh();
};
})();
