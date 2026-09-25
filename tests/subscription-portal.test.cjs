const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'sub-portal.html'),'utf8');
const css=fs.readFileSync(path.join(root,'sub-portal.css'),'utf8');
const js=fs.readFileSync(path.join(root,'sub-portal.js'),'utf8');
const iconsSrc=fs.readFileSync(path.join(root,'sub-icons.js'),'utf8');

function panel(name){
  const marker='<div class="app-grid" data-platform-panel="'+name+'">';
  const start=html.indexOf(marker);
  assert.ok(start>=0,'missing '+name+' app panel');
  const next=html.indexOf('<div class="app-grid" data-platform-panel="',start+marker.length);
  const end=next>=0?next:html.indexOf('<p class="app-help"',start);
  return html.slice(start,end);
}

test('platform app contract is exact',()=>{
  const android=panel('android'),ios=panel('ios'),windows=panel('windows');
  for(const name of ['V2Box','HAPP','Streisand']){
    assert.ok(android.includes('>'+name+'<'),name+' missing from Android');
    assert.ok(ios.includes('>'+name+'<'),name+' missing from iOS');
  }
  for(const name of ['Hiddify','v2rayN'])assert.ok(windows.includes('>'+name+'<'),name+' missing from Windows');
  for(const name of ['V2Box','HAPP','Streisand'])assert.ok(!windows.includes('>'+name+'<'),name+' leaked into Windows');
});

test('all app artwork is bundled as local data URIs',()=>{
  const ctx=vm.createContext({window:{},encodeURIComponent});
  vm.runInContext(iconsSrc,ctx);
  const icons=ctx.window.__DARK_APP_ICONS__;
  for(const name of ['v2box','happ','streisand','hiddify','v2rayn']){
    assert.equal(typeof icons[name],'string',name);
    assert.ok(icons[name].startsWith('data:image/'),name);
  }
  assert.ok(!/<img[^>]+src=["']https?:/i.test(html));
  assert.ok(html.includes('assets/sub-icons.js'));
});

test('deep-link and fallback contract covers every launcher',()=>{
  for(const scheme of ['v2box://install-sub','happ://add/','streisand://import/','hiddify://import/'])assert.ok(js.includes(scheme),scheme);
  assert.ok(js.includes("app==='v2rayn'"));
  assert.ok(js.includes('await copy(base)'));
  for(const app of ['v2box','happ','streisand','hiddify','v2rayn'])assert.ok(html.includes('data-app="'+app+'"'),app);
});

test('mobile connect controls and reduced-motion safety are permanent',()=>{
  assert.ok(css.includes('min-height:48px'));
  assert.ok(css.includes('@media(max-width:700px)'));
  assert.ok(css.includes('@media(prefers-reduced-motion:reduce)'));
  assert.ok(css.includes('.app-card.launching'));
  assert.ok(html.includes('<span>CONNECT</span><i>↗</i>'));
});
