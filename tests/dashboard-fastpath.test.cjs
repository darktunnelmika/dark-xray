const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/dashboard-fastpath-v1.js'), 'utf8');
const overview = fs.readFileSync(path.join(root, 'web/overview-v4.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');

function runtime(onRequest, role='owner') {
  const calls = [];
  let normalLoads = 0;
  let captures = 0;
  const state = {
    me: {id: 'test-owner', role}, page: 'dashboard', busy: false,
    errors: {}, clients: [], inbounds: [], owners: [], resellers: [],
    system: null, sync: null, history: [],
    ov2: {nodes: [], overview: null, overviewOwner: '', audit: [], ip: null,
          core: null, backup: null},
    ov4: {history: {cpu: [], mem: [], up: [], down: []},
          capture(){ captures++; }},
    updateCenter: {status: null, error: ''}
  };
  const ctx = {
    state, load: async()=>{normalLoads++;},
    api: path=>{calls.push(path);return onRequest(path);},
    isOwner: ()=>state.me?.role==='owner',
    can: ()=>true,
    renderPage: async()=>{},
    // Short timers make the critical-budget and coalescing tests deterministic.
    setTimeout: (fn,ms)=>setTimeout(fn,Math.min(ms, 12)),
    clearTimeout
  };
  vm.createContext(ctx);
  vm.runInContext(source,ctx,{filename:'dashboard-fastpath-v1.js'});
  return {ctx,state,calls,normalLoads:()=>normalLoads,captures:()=>captures};
}

test('Fastpath mounts after all existing dashboard and UI-stability wrappers',()=>{
  const fp = html.indexOf('assets/dashboard-fastpath-v1.js');
  const ui = html.indexOf('assets/ui-stability.js');
  const ov = html.indexOf('assets/overview-v4.js');
  assert.ok(fp>ui && fp>ov);
  assert.match(source,/standardLoad\(\)/);
  assert.match(source,/\/api\/system\?live_only=true/);
  assert.doesNotMatch(source,/\/api\/core\/(restart|stop)|['"]POST['"]|\/api\/nodes\/[^'"]+\/sync/);
});

test('Dashboard first paint only awaits bounded host telemetry, not large lists',async()=>{
  const pending = new Promise(()=>{});
  const t = runtime(p => p.startsWith('/api/system?') ?
    {engine:{cpu:18,netIO:{up:0,down:0},connections:{tcp:0,udp:0,available:true}}} : pending);
  await t.ctx.load();
  assert.equal(t.normalLoads(),0);
  assert.equal(t.state.system.engine.cpu,18);
  assert.equal(t.captures(),1);
  assert.equal(t.state.dashboardFast.status.system,'ready');
  assert.ok(t.state.dashboardFast.active<=2);
  assert.ok(t.calls.some(x=>x==='/api/operations/overview'));
  assert.ok(t.calls.some(x=>x==='/api/nodes'));
  assert.ok(!t.calls.includes('/api/clients'), 'Expensive client enumeration waits behind critical node status');
});

test('An unresponsive system sample does not stall Dashboard indefinitely',async()=>{
  let resolveHost;
  const pending = new Promise(resolve=>{resolveHost=resolve;});
  const t = runtime(p => p.startsWith('/api/system?') ? pending : new Promise(()=>{}));
  await t.ctx.load();
  assert.equal(t.state.system,null);
  assert.equal(t.state.dashboardFast.status.system,'loading');
  resolveHost({engine:{cpu:28}});
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(t.state.system.engine.cpu,28);
  assert.equal(t.captures(),1);
});

test('Late results from an earlier login cannot restore another owner cache',async()=>{
  let resolveHost;
  const host = new Promise(resolve=>{resolveHost=resolve;});
  const t = runtime(p => p.startsWith('/api/system?') ? host : new Promise(()=>{}));
  const firstLoad = t.ctx.load();
  t.state.me = {id:'new-session',role:'reseller'};
  resolveHost({engine:{cpu:84}});
  await firstLoad;
  assert.equal(t.state.system,null);
  assert.equal(t.captures(),0);
});

test('Other pages use the original full loader and do not launch Dashboard jobs',async()=>{
  const t = runtime(()=>{throw Error('no request expected');});
  t.state.page='nodes';
  await t.ctx.load();
  assert.equal(t.normalLoads(),1);
  assert.equal(t.calls.length,0);
});

test('Missing and stale telemetry never becomes invented zero or a healthy node',()=>{
  assert.match(overview,/state\.ov4\.capture=sample/);
  assert.match(overview,/if\(value===null\|\|value===undefined\|\|value===''\)return/);
  assert.match(overview,/sourceReady\('nodes',45000\)/);
  assert.match(overview,/health_sample_state==='fresh'/);
  assert.match(overview,/Waiting for two real network samples/);
  assert.match(overview,/Runtime telemetry unavailable/);
});
