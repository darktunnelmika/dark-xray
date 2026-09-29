const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-detail-v8.js'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','nodes-detail-v8.css'),'utf8');

test('Nodes V8 loads after recovery and adds a Details action without replacing Node actions',()=>{
 assert.ok(index.includes('assets/nodes-detail-v8.css'));
 const recovery=index.indexOf('assets/node-recovery.js');
 const detail=index.indexOf('assets/nodes-detail-v8.js');
 assert.ok(recovery>0&&detail>recovery);
 for(const token of ['data-act="nv8details"','actionBase=runAction','return actionBase(act,el)','querySelectorAll(\'.nv2-actions\')'])
  assert.ok(src.includes(token),token);
});

test('Nodes V8 exposes Live 1h 24h Hub-side history and four charts',()=>{
 for(const token of ["/metrics?window=","['live','LIVE']","['1h','1H']","['24h','24H']",'CPU / RAM','RX / TX','Health / Capacity','Connections'])
  assert.ok(src.includes(token),token);
 for(const token of ['nv8-chart-grid','nv8-range-tabs','nv8-detail-grid'])assert.ok(css.includes(token),token);
});

test('Nodes V8 detail is read-only and excludes tunnel path health',()=>{
 assert.ok(src.includes('Hub history uses Agent/Xray/system health samples only.'));
 assert.ok(src.includes('Tunnel/WARP/path health is excluded.'));
 assert.equal(src.includes('/probe'),false);
 assert.equal(src.includes(",'POST'"),false);
});
