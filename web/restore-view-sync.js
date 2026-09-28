/* Keep an operator's latest Restore search when an asynchronous refresh finishes. */
(function(){
'use strict';
if(!globalThis.DarkRestoreGroups||typeof enginePage!=='function'||typeof renderPage!=='function')return;
const basePage=enginePage,baseRender=renderPage;
let focus=null;
enginePage=async function(){
 const requested=state.page,seq=state.renderSeq,html=await basePage();
 if(requested==='darkrestore'&&state.page===requested&&seq===state.renderSeq){
  const input=document.querySelector('[data-dr-search]');
  // Capture at commit time, not at request start: typing may begin during I/O.
  focus=input&&document.activeElement===input?{seq,start:input.selectionStart,end:input.selectionEnd}:null;
 }
 return html;
};
renderPage=async function(){
 const requested=state.page,result=await baseRender();
 if(requested!=='darkrestore'||state.page!==requested||!isOwner())return result;
 const input=document.querySelector('.dark-restore-v2 [data-dr-search]');
 if(!input)return result;
 const query=String(DarkRestoreGroups.state.query||'');
 if(input.value!==query){
  input.value=query;
  // Reuse the existing Restore filter handler; no duplicate renderer or API call.
  input.dispatchEvent(new Event('input',{bubbles:true}));
 }
 if(focus&&focus.seq===state.renderSeq){
  input.focus({preventScroll:true});
  if(Number.isInteger(focus.start)&&Number.isInteger(focus.end))input.setSelectionRange(focus.start,focus.end);
 }
 focus=null;
 return result;
};
globalThis.DarkRestoreViewSync={ready:true};
})();
