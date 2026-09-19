const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');

const i18n=read('web/i18n-en.js');
const live=read('web/live.js');
const inbounds=read('web/inbounds-v3.js');
const webDir=path.join(__dirname,'..','web');
const allUi=fs.readdirSync(webDir).filter(n=>/\.(?:js|html)$/.test(n)).map(n=>fs.readFileSync(path.join(webDir,n),'utf8')).join('\n');
const overview=read('web/overview-v4.js');
const core=read('backend/core.py');
const server=read('backend/server.py');

test('language normalizer is bidirectional instead of English-only',()=>{
  assert.match(i18n,/const reverseExact=new Map/);
  assert.match(i18n,/const reversePhrases=/);
  assert.match(i18n,/const faExact=new Map/);
  assert.match(i18n,/selected==='en'/);
  assert.doesNotMatch(i18n,/function process\(root\)\{\s*if\(selected!=='en'/);
  assert.match(i18n,/process\(document\.body\)/);
});

test('user-facing panel UI does not mention legacy Sanaei or Mirza products',()=>{
  assert.doesNotMatch(allUi,/sanaei|sanayi|mirza|سنایی|میرزا/i);
  assert.match(inbounds,/DARK-native essentials/);
  assert.match(live,/authorized DARK XRAY integrations|اتصال ابزارهای مجاز به DARK XRAY/);
});

test('CPU telemetry uses a stable sample and dashboard never renders false 0.0 percent',()=>{
  assert.match(core,/def _host_cpu_percent/);
  assert.match(core,/psutil\.cpu_times\(\)/);
  assert.match(core,/psutil\.cpu_percent\(interval=\.2\)/);
  assert.doesNotMatch(core,/cpu_percent\(interval=\.05\)/);
  assert.doesNotMatch(server,/cpu_percent\(interval=\.05\)/);
  assert.match(overview,/function fmtCpu/);
  assert.match(overview,/return v<=0\?'<0\.1':v\.toFixed\(1\)/);
});
