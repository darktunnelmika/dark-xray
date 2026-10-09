const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'original-cyber-colors-v5.css'),'utf8');
const editor=fs.readFileSync(path.join(root,'inbounds-v3.js'),'utf8');

test('Original Cyber palette is the final visual layer, V4 local fonts remain',()=>{
 const styles=[...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(x=>x[1]);
 assert.ok(styles.includes('assets/original-cyber-colors-v5.css?v=1'));
 assert.ok(styles.indexOf('assets/original-cyber-colors-v5.css?v=1') > styles.indexOf('assets/graphite-eclipse-v4.css?v=1'));
 for(const x of ['assets/graphite-eclipse-fontfaces-v4.css?v=1','assets/graphite-eclipse-v4.css?v=1','assets/obsidian-pulse-v3.css?v=1'])
   assert.ok(styles.indexOf(x)>=0&&styles.indexOf(x)<styles.length-1,x);
 assert.match(html,/<meta name="theme-color" content="#080b0a">/);
});
test('Original DARK XRAY green/black is restored across all theme token layers',()=>{
 for(const x of [
   '--cyber-original-black:#000604','--cyber-original-green:#19ff86',
   '--classic-green:#19ff86','--classic-black:#000604',
   '--bg:#000604','--green:#19ff86','--dx-bg:#000604',
   '--pulse-bg:#000604','--ge-back:#000604','--ov4-accent:#19ff86'
 ])assert.ok(css.includes(x),x);
 for(const sel of [
   'body.skin-cyber-classic .sidebar','body.skin-cyber-classic .topbar',
   'body.skin-cyber-classic .nav-btn.active',
   'body.skin-cyber-classic .ov4-card','body.skin-cyber-classic .btn-primary',
   'body.skin-cyber-classic .cv4-row','body.skin-cyber-classic .iv3-drawer',
   'body.skin-cyber-classic #iv3-editor .iv3-deploy-card',
   'body.skin-cyber-classic #iv3-editor .iv3-swap-route'
 ])assert.ok(css.includes(sel),sel);
});
test('No typography, structure, routing or dynamic controls changed',()=>{
 for(const property of [/^\s*font-family\s*:/m,/^\s*font-size\s*:/m,
   /^\s*border-radius\s*:/m,/^\s*grid-template-/m,
   /pointer-events\s*:/,/display\s*:\s*none/i,
   /position\s*:\s*fixed/i,/\/api\//,/fetch\s*\(/i,
   /localStorage|sessionStorage|document\.cookie/])
  assert.doesNotMatch(css,property);
 for(const input of ['name="tunnelPort"','name="tunnelEnable"','name="swapEnable"'])
  assert.ok(editor.includes(input),input);
 assert.ok(fs.existsSync(path.join(root,'fonts','vazirmatn-arabic-variable.woff2')));
 assert.ok(fs.existsSync(path.join(root,'fonts','manrope-latin-variable.woff2')));
 assert.ok(fs.existsSync(path.join(root,'fonts','jetbrains-mono-latin-variable.woff2')));
});
test('Semantic warning/error and RTL accessibility remain clear',()=>{
 assert.match(css,/\.notice\.warning\s*\{/);
 assert.match(css,/\.notice\.error\s*\{/);
 assert.match(css,/:focus-visible/);
 assert.match(css,/html\[dir="rtl"\]/);
 let depth=0;for(const ch of css.replace(/\/\*[\s\S]*?\*\//g,'')){
  if(ch==='{')depth++;else if(ch==='}')depth--;
  assert.ok(depth>=0,'unexpected CSS close');
 }
 assert.equal(depth,0);
});
