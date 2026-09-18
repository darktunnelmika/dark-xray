const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','overview-v4.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','overview-v4.css'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');

test('Overview V4 is loaded before Update Center so the dashboard slot is wrapped correctly',()=>{
  const ov=html.indexOf('assets/overview-v4.js');
  const up=html.indexOf('assets/update-center.js');
  assert.ok(ov>0 && up>ov);
  assert.match(html,/assets\/overview-v4\.css/);
});

test('Overview V4 exposes the requested monitoring-first structure',()=>{
  for(const token of ['ov4-commandbar','ov4-resource-grid','ov4-traffic-card','ov4-connections',
    'ov4-telemetry-strip','ov4-summary-grid','ov4-management-grid','ov4-lower-grid',
    'NODE FLEET','BACKUP & RECOVERY','REPRESENTATIVES','HEALTH & ALERTS','RECENT ACTIVITY']){
    assert.ok(src.includes(token),token);
  }
  assert.match(src,/dark-update-center-slot/);
});

test('Overview V4 charts use only sampled API telemetry and no random/demo series',()=>{
  assert.doesNotMatch(src,/Math\.random|crypto\.getRandomValues|demoData|fakeData/i);
  assert.match(src,/state\.system\?\.engine/);
  assert.match(src,/n\.netIO/);
  assert.match(src,/n\.connections/);
  assert.match(src,/n\.netTraffic/);
});

test('Overview V4 provides owner quick actions without bypassing DARK APIs',()=>{
  for(const action of ['ov4restart','ov4stop','ov4logs','ov4config','ov4backup','ov4history','ov4metrics']){
    assert.ok(src.includes(action),action);
  }
  assert.match(src,/\/api\/core\/restart/);
  assert.match(src,/\/api\/core\/stop/);
  assert.doesNotMatch(src,/child_process|sudo\s|pkill|killall/);
});

test('Overview V4 has responsive desktop and mobile geometry',()=>{
  assert.match(css,/grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(css,/ov4-main-grid/);
  assert.match(css,/ov4-management-grid/);
  assert.match(css,/@media\(max-width:700px\)/);
});
