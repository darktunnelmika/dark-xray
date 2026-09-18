const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const html=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','cyber-classic.css'),'utf8');

test('Cyber Classic is the default DARK XRAY body skin',()=>{
  assert.match(html,/<body class="skin-cyber-classic">/);
  assert.match(html,/assets\/cyber-classic\.css/);
});

test('Cyber Classic loads after feature CSS so it wins presentation only',()=>{
  const skin=html.indexOf('assets/cyber-classic.css');
  const update=html.indexOf('assets/update-center.css');
  const nodes=html.indexOf('assets/nodes-v2.css');
  assert.ok(skin>update && skin>nodes);
});

test('Cyber Classic keeps the classic green terminal palette and squared geometry',()=>{
  assert.match(css,/--classic-green:#19ff86/);
  assert.match(css,/--radius:3px/);
  assert.match(css,/body\.skin-cyber-classic \.panel/);
  assert.match(css,/border-radius:2px !important/);
  assert.match(css,/repeating-linear-gradient/);
  assert.match(css,/LIVE CONSOLE/);
});

test('skin is a presentation layer with no API or fetch behavior',()=>{
  assert.doesNotMatch(css,/\/api\//);
  assert.doesNotMatch(css,/fetch\s*\(/);
  assert.doesNotMatch(css,/localStorage|sessionStorage|document\.cookie/);
});
