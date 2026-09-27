const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const cyber=fs.readFileSync(path.join(__dirname,'..','web','cyber.css'),'utf8');
const style=fs.readFileSync(path.join(__dirname,'..','web','style.css'),'utf8');
const live=fs.readFileSync(path.join(__dirname,'..','web','live.js'),'utf8');

test('mobile sidebar uses one transform-only off-canvas system for LTR and RTL',()=>{
  assert.doesNotMatch(cyber,/html\[dir="ltr"\] \.sidebar\{left:-100%/);
  assert.match(cyber,/html\[dir="ltr"\] \.sidebar\{[\s\S]*?transform:translate3d\(-105%,0,0\)!important/);
  assert.match(cyber,/\.sidebar\{[\s\S]*?transform:translate3d\(105%,0,0\)!important/);
  assert.match(cyber,/\.sidebar\.open,html\[dir="ltr"\] \.sidebar\.open,html\[dir="rtl"\] \.sidebar\.open\{[\s\S]*?translate3d\(0,0,0\)!important/);
  assert.match(cyber,/visibility:hidden;pointer-events:none/);
});

test('mobile shell cannot create document-level horizontal scroll',()=>{
  assert.match(style,/html,body,#app\{width:100%;max-width:100%;overflow-x:hidden\}/);
  assert.match(style,/\.main\{width:100%;max-width:100vw;min-width:0;overflow-x:hidden\}/);
  assert.match(style,/\.topbar,\.content\{width:100%;max-width:100%;min-width:0\}/);
});

test('mobile menu state resets on shell rebuild, close and navigation',()=>{
  assert.match(live,/function shell\(\)\{document\.documentElement\.classList\.remove\('dark-mobile-menu-open'\)/);
  assert.match(live,/document\.documentElement\.classList\.toggle\('dark-mobile-menu-open',next\)/);
  assert.match(live,/if\(!next&&window\.innerWidth<=760&&window\.scrollX\)/);
  assert.match(live,/async function go\(page\)\{setMobileMenu\(false\);/);
});
