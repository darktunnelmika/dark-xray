const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'inbound-deployment-visual-v1.css'),'utf8');
const js=fs.readFileSync(path.join(root,'inbounds-v3.js'),'utf8');

test('Visual polish loads last without replacing editor markup or scripts',()=>{
  const links=[...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(x=>x[1]);
  assert.equal(links.at(-1),'assets/inbound-deployment-visual-v1.css?v=1');
  assert.ok(links.indexOf('assets/classic-gangster-v2.css?v=1') < links.length-1);
  assert.ok(js.includes('function deploymentCard('));
  assert.ok(js.includes('function deploymentTargets()'));
  for(const token of ['name="tunnelEnable"','name="tunnelPort"','name="swapEnable"','name="deployLocal"','name="deployNode"']) {
    if(!js.includes(token) && ['name="deployLocal"','name="deployNode"'].includes(token)){
      assert.ok(js.includes("'deployLocal'") || js.includes("'deployNode'"),token);
    }else assert.ok(js.includes(token),token);
  }
});
test('Target card, tunnel switch, port and SWAP have distinct readable states',()=>{
  for(const token of [
    '#iv3-editor .iv3-deploy','iv3-deploy .iv3-section-title.compact',
    '#iv3-editor .iv3-deploy-card','#iv3-editor .iv3-deploy-grid',
    '.iv3-deploy-target:has(input:checked)',
    '.iv3-tunnel-toggle:has(input:checked)',
    '.iv3-tunnel-port > input:disabled',
    '.iv3-tunnel-head > b.ok','.iv3-tunnel-head > b.warn',
    '.iv3-tunnel-head > b.bad','.iv3-swap-route',
    '.iv3-swap-route .btn:hover:not(:disabled)',
  ]) assert.ok(css.includes(token),token);
  assert.match(css,/font:\s*680 14px\/1\.4 var\(--mono\)/);
  assert.match(css,/font-size:\s*13px/);
});
test('Visual-only patch cannot change control semantics or mobile columns',()=>{
  for(const forbidden of [/\/api\//,/fetch\s*\(/,/localStorage/i,/sessionStorage/i,
    /\.innerHTML/,/display:\s*none\s*!important/i,/pointer-events:\s*none/i,
    /grid-template-columns\s*:/i,/\bposition:\s*fixed\b/i])
    assert.doesNotMatch(css,forbidden);
  assert.match(css,/@media\(max-width:620px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  let balance=0;
  for(const ch of css.replace(/\/\*[\s\S]*?\*\//g,'')){
    if(ch==='{')balance++;
    if(ch==='}')balance--;
    assert.ok(balance>=0,'unexpected closing brace');
  }
  assert.equal(balance,0);
});
