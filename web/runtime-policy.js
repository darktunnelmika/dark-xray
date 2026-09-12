/* DARK XRAY browser-side policy bridge.
 * Keeps HTML constraints aligned with the server policy regardless of UI language.
 */
(function(){
'use strict';
const ACCOUNT_PASSWORD_MIN=8;
function apply(root){
  if(!root)return;
  const candidates=[];
  if(root.nodeType===1&&root.matches?.('input[minlength="12"]'))candidates.push(root);
  if(root.querySelectorAll)candidates.push(...root.querySelectorAll('input[minlength="12"]'));
  for(const el of candidates)el.setAttribute('minlength',String(ACCOUNT_PASSWORD_MIN));
  const walker=document.createTreeWalker(root.nodeType===1?root:document.body,NodeFilter.SHOW_TEXT);
  const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
  for(const n of nodes){
    const p=n.parentElement;if(!p||p.closest('script,style,pre,code,.json-box,.json-preview,.terminal'))continue;
    if(n.nodeValue?.includes('حداقل ۱۲ کاراکتر'))n.nodeValue=n.nodeValue.replaceAll('حداقل ۱۲ کاراکتر','حداقل ۸ کاراکتر');
    if(n.nodeValue?.includes('minimum 12 characters'))n.nodeValue=n.nodeValue.replaceAll('minimum 12 characters','minimum 8 characters');
  }
}
function start(){apply(document.body);new MutationObserver(records=>{for(const r of records)for(const n of r.addedNodes)if(n.nodeType===1)apply(n);}).observe(document.body,{subtree:true,childList:true});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
