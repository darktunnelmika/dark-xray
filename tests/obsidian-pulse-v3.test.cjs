const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const dir=path.join(__dirname,'..','web');
const index=fs.readFileSync(path.join(dir,'index.html'),'utf8');
const css=fs.readFileSync(path.join(dir,'obsidian-pulse-v3.css'),'utf8');
const inbound=fs.readFileSync(path.join(dir,'inbounds-v3.js'),'utf8');

test('Obsidian Pulse is a reversible last-in-cascade presentation layer',()=>{
  const links=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>m[1]);
  assert.equal(links.at(-1),'assets/obsidian-pulse-v3.css?v=1');
  for(const old of ['assets/cyber-classic.css','assets/premium-cyber-v1.css?v=1',
    'assets/classic-gangster-v2.css?v=1','assets/inbound-deployment-visual-v1.css?v=1'])
    assert.ok(links.indexOf(old)>=0&&links.indexOf(old)<links.length-1,old);
  assert.match(index,/<body class="skin-cyber-classic">/);
});

test('V3 replaces dead near-black slabs with vivid raised surfaces and rounded controls',()=>{
  for(const selector of [
    'body.skin-cyber-classic .sidebar','body.skin-cyber-classic .nav-btn.active',
    'body.skin-cyber-classic .topbar','body.skin-cyber-classic .ov4-card',
    'body.skin-cyber-classic .panel','body.skin-cyber-classic .btn-primary',
    'body.skin-cyber-classic .btn.danger','body.skin-cyber-classic .dialog',
    'body.skin-cyber-classic .cv4-row','body.skin-cyber-classic .iv3-drawer',
    'body.skin-cyber-classic .notice.warning','body.skin-cyber-classic .notice.error'
  ])assert.ok(css.includes(selector),selector);
  for(const token of ['--pulse-bg:#111725','--pulse-panel:#202d40',
    '--pulse-turquoise:#76f0df','--pulse-mint:#79f6b9',
    '--pulse-iris:#a59aff','--pulse-coral:#ff8fae'])
    assert.ok(css.includes(token),token);
  assert.match(css,/border-radius:19px\s*!important/);
  assert.match(css,/border-radius:21px\s*!important/);
});

test('Deployment Targets stay two-column and are visually slim, not square or oversized',()=>{
  for(const x of [
    '#iv3-editor .iv3-deploy','#iv3-editor .iv3-deploy-grid',
    '#iv3-editor .iv3-deploy-card',
    '#iv3-editor .iv3-deploy-card.selected',
    '#iv3-editor .iv3-deploy-card > .iv3-deploy-target',
    '#iv3-editor .iv3-tunnel-route',
    '#iv3-editor .iv3-tunnel-toggle:has(input:checked)',
    '#iv3-editor .iv3-tunnel-port > input',
    '#iv3-editor .iv3-swap-route',
    '#iv3-editor .iv3-tunnel-head>b.warn',
    '#iv3-editor .iv3-tunnel-head>b.bad'
  ])assert.ok(css.includes(x),x);
  assert.match(css,/min-height:48px\s*!important/);
  assert.match(css,/height:37px/);
  assert.doesNotMatch(css,/grid-template-columns:/);
  assert.doesNotMatch(css,/grid-template-areas:/);
  assert.match(inbound,/function deploymentCard\(/);
  assert.match(inbound,/function deploymentTargets\(/);
  assert.match(inbound,/name="tunnelEnable"/);
  assert.match(inbound,/name="tunnelPort"/);
  assert.match(inbound,/name="swapEnable"/);
  assert.match(inbound,/name="\$\{inputName\}"/);
});

test('V3 is scoped CSS only and preserves the working application contract',()=>{
  for(const bad of [/\/api\//,/fetch\s*\(/i,/localStorage/i,/sessionStorage/i,
    /\.innerHTML/,/pointer-events:\s*none/i,/position:\s*fixed/i,
    /@import/i,/content:\s*url/i,/display:\s*none\s*!important/i])
    assert.doesNotMatch(css,bad);
  assert.match(css,/:focus-visible/);
  assert.match(css,/html\[dir="rtl"\]/);
  assert.match(css,/@media\(max-width:760px\)/);
  assert.match(css,/@media\(max-width:390px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/\.dark-reduced-motion/);
  let balance=0;
  for(const ch of css.replace(/\/\*[\s\S]*?\*\//g,'')){
    if(ch==='{')balance++;
    if(ch==='}')balance--;
    assert.ok(balance>=0,'unexpected closing CSS brace');
  }
  assert.equal(balance,0);
});

test('mobile menu retains its existing stacking hierarchy above an open sidebar',()=>{
  const topbar=css.match(/body\.skin-cyber-classic \.topbar\s*\{([^}]+)\}/);
  assert.ok(topbar,'Topbar must be styled');
  assert.doesNotMatch(topbar[1],/backdrop-filter\s*:/i);
  const original=fs.readFileSync(path.join(dir,'style.css'),'utf8');
  assert.match(original,/mobile-menu/);
  assert.match(original,/sidebar/);
});
