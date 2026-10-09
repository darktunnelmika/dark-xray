const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'dark-carbon-neon-v6.css'),'utf8');
const inbound=fs.readFileSync(path.join(root,'inbounds-v3.js'),'utf8');

test('Carbon is the only newest skin and preserves V4 font assets',()=>{
 const links=[...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(x=>x[1]);
 assert.equal(links.at(-1),'assets/dark-carbon-neon-v6.css?v=1');
 assert.ok(links.indexOf('assets/original-cyber-colors-v5.css?v=1')<links.length-1);
 assert.ok(links.includes('assets/graphite-eclipse-fontfaces-v4.css?v=1'));
 assert.match(html,/<meta name="theme-color" content="#080b0a">/);
});
test('Neutral matte backgrounds, green only for active states and meaningful statuses',()=>{
 for(const token of [
  '--carbon-bg:#080b0a','--carbon-surface:#141918','--carbon-card:#202624',
  '--carbon-line:#34433b','--carbon-neon:#19ff86',
  '--ge-surface:var(--carbon-surface)',
  'body.skin-cyber-classic .sidebar','body.skin-cyber-classic .topbar',
  'body.skin-cyber-classic .nav-btn.active',
  'body.skin-cyber-classic .ov4-card','body.skin-cyber-classic .btn-primary',
  'body.skin-cyber-classic .btn.danger',
  'body.skin-cyber-classic .iv3-drawer',
  'body.skin-cyber-classic #iv3-editor .iv3-deploy-card',
  'body.skin-cyber-classic #iv3-editor .iv3-tunnel-port>input',
  'body.skin-cyber-classic #iv3-editor .iv3-swap-route',
  'body.skin-cyber-classic #iv3-editor .iv3-tunnel-head>b.bad',
  'body.skin-cyber-classic #iv3-editor .iv3-tunnel-head>b.warn',
 ])assert.ok(css.includes(token),token);
 assert.doesNotMatch(css,/#10291c|#1c4930|#235438|#153824|#151d2b|#222f4d/i);
});
test('Carbon Deployment cards do not stretch the short Hub to match SWAP cards',()=>{
 assert.match(css,/\.iv3-deploy-grid\s*\{\s*align-items:start;/);
 for(const token of ['name="tunnelPort"','name="tunnelEnable"','name="swapEnable"',
   'function deploymentCard(','function deploymentTargets()'])
   assert.ok(inbound.includes(token),token);
});
test('Palette cannot modify font definitions, interaction, API, or table geometry',()=>{
 for(const blocked of [/font-family\s*:/,/--font\s*:/,/--mono\s*:/,
   /grid-template-columns\s*:/,/grid-template-areas\s*:/,
   /pointer-events\s*:/,/display\s*:\s*none/i,/position\s*:\s*fixed/i,
   /\/api\//,/fetch\s*\(/i,/localStorage|sessionStorage|document\.cookie/])
  assert.doesNotMatch(css,blocked);
 assert.match(css,/html\[dir="rtl"\]/);
 assert.match(css,/:focus-visible/);
 assert.match(css,/prefers-reduced-motion:reduce/);
 assert.match(css,/@media\(max-width:760px\)/);
 let depth=0;
 for(const ch of css.replace(/\/\*[\s\S]*?\*\//g,'')){
  if(ch==='{')depth++;
  if(ch==='}')depth--;
  assert.ok(depth>=0,'extraneous CSS block close');
 }
 assert.equal(depth,0,'CSS braces should be balanced');
});
