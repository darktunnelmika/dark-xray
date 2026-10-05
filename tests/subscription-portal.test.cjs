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

test('launcher collection iteration never uses single-element selector',()=>{
  for(const selector of ['[data-app-icon]','.app-copy','.app-import']){
    const good="$$('"+selector+"').forEach";
    const bad="$('"+selector+"').forEach";
    assert.ok(js.includes(good),'missing collection selector: '+selector);
    assert.ok(!js.replaceAll(good,'').includes(bad),'single selector iteration: '+selector);
  }
});

function expiryView(data,now=Date.UTC(2026,9,5,12)){
  const elements=new Map(),timers=[];
  const document={querySelector:s=>{
    if(!elements.has(s))elements.set(s,{textContent:'',style:{},classList:{add(){},remove(){},toggle(){}}});
    return elements.get(s);
  },querySelectorAll:()=>[]};
  class Clock extends Date {static now(){return now}}
  const context=vm.createContext({window:{__DARK_SUB__:data},document,Date:Clock,Intl,
    navigator:{userAgent:'iPhone'},location:{href:'https://vpn.test/sub/test'},
    setTimeout(){},setInterval:fn=>timers.push(fn),encodeURIComponent});
  vm.runInContext(js,context);
  return {text:()=>elements.get('#expiry').textContent,hint:()=>elements.get('#expiryHint').textContent,
    advance:ms=>{now+=ms;timers.forEach(fn=>fn())}};
}

test('expiry shows the exact local end date, clock and remaining time',()=>{
  const expiry=Date.UTC(2026,9,7,12)/1000,view=expiryView({expiry});
  const dt=new Date(expiry*1000),pad=n=>String(n).padStart(2,'0');
  assert.equal(view.text(),dt.getFullYear()+'-'+pad(dt.getMonth()+1)+'-'+pad(dt.getDate()));
  assert.match(view.hint(),/2 DAYS LEFT/);
  assert.match(view.hint(),/\d{2}:\d{2}/);
  view.advance(2*86400000);
  assert.match(view.hint(),/EXPIRED/);
  assert.notEqual(view.text(),'UNLIMITED');
});

test('less than one day is shown in hours',()=>{
  const view=expiryView({expiry:(Date.UTC(2026,9,5,12)+2*3600000)/1000});
  assert.match(view.hint(),/2 HOURS LEFT/);
});

test('first-connection plans are never labelled unlimited or given a guessed end date',()=>{
  const view=expiryView({expiry:0,activation_pending:true,duration_days:30});
  assert.equal(view.text(),'30 DAYS');
  assert.match(view.hint(),/STARTS ON FIRST CONNECTION/);
  assert.match(view.hint(),/EXPIRY NOT SET YET/);
});

test('an actual timestamp takes precedence over stale activation metadata',()=>{
  const view=expiryView({expiry:Date.UTC(2026,9,7,12)/1000,activation_pending:true,duration_days:30});
  assert.match(view.hint(),/DAYS LEFT/);
  assert.doesNotMatch(view.hint(),/FIRST CONNECTION/);
});

test('genuine no-expiry services retain unlimited status',()=>{
  const view=expiryView({expiry:0,activation_pending:false});
  assert.equal(view.text(),'UNLIMITED');
});
