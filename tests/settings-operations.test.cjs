const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const live=fs.readFileSync(path.join(__dirname,'..','web','live.js'),'utf8');
const ops=fs.readFileSync(path.join(__dirname,'..','web','ops-v2.js'),'utf8');
const settings=fs.readFileSync(path.join(__dirname,'..','web','settings-v2.js'),'utf8');
const overview=fs.readFileSync(path.join(__dirname,'..','web','overview-v4.js'),'utf8');

test('Logs and Audit are absent from primary sidebar navigation',()=>{
  const nav=live.slice(live.indexOf('function navItems()'),live.indexOf('function shell()'));
  assert.doesNotMatch(nav,/\['audit','گزارش عملیات'/);
  assert.doesNotMatch(ops,/extras=\[\['logs'/);
  assert.doesNotMatch(ops,/n\.push\(.*logs/);
});

test('Settings exposes one Operations workspace containing logs and Audit',()=>{
  assert.match(settings,/\['operations',L\('Operations','عملیات'\),'log'\]/);
  assert.match(settings,/\/api\/logs\//);
  assert.match(settings,/\/api\/audit/);
  assert.match(settings,/Runtime logs/);
  assert.match(settings,/Operation report \/ Audit/);
  assert.match(settings,/globalThis\.DarkSettingsV2/);
  assert.match(settings,/data-sv2-action="log-kind"/);
});

test('Overview and legacy operations actions route logs/history into Settings Operations',()=>{
  assert.match(overview,/ov4logs/);
  assert.match(overview,/DarkSettingsV2\?\.open/);
  assert.match(overview,/open\('operations'\)/);
  assert.doesNotMatch(overview,/if\(act==='ov4logs'\)\{await go\('logs'\)/);
  assert.doesNotMatch(overview,/if\(act==='ov4history'\)\{await go\('audit'\)/);
  assert.match(ops,/DarkSettingsV2\?\.open/);
});


test('Primary sidebar hides Outbounds and Routing and exposes Smart WARP instead',()=>{
  const nav=live.slice(live.indexOf('function navItems()'),live.indexOf('function shell()'));
  assert.doesNotMatch(nav,/\['outbounds','اوتباندها'/);
  assert.doesNotMatch(nav,/\['routing','روتینگ'/);
  assert.match(nav,/\['smart','وارپ هوشمند','activity'\]/);
});
