const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const palette=fs.readFileSync(path.join(root,'graphite-eclipse-v4.css'),'utf8');
const fonts=fs.readFileSync(path.join(root,'graphite-eclipse-fontfaces-v4.css'),'utf8');
const inbound=fs.readFileSync(path.join(root,'inbounds-v3.js'),'utf8');

test('graphite layers are loaded last; removal is a two-link rollback',()=>{
 const links=[...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(x=>x[1]);
 assert.deepEqual(links.slice(-2),['assets/graphite-eclipse-fontfaces-v4.css?v=1','assets/graphite-eclipse-v4.css?v=1']);
 assert.ok(links.includes('assets/obsidian-pulse-v3.css?v=1'));
 assert.match(html,/<meta name="theme-color" content="#171a1b">/);
});
test('self-contained variable fonts cover Persian, Latin and technical labels',()=>{
 assert.equal((fonts.match(/@font-face/g)||[]).length,3);
 for(const token of ['font-family:"DX UI"','font-family:"DX Mono"','font-display:swap','unicode-range:U+0600-06FF','unicode-range:U+0000-05FF','font-weight:100 900'])
   assert.ok(fonts.includes(token),token);
 for(const font of ['vazirmatn-arabic-variable.woff2','manrope-latin-variable.woff2','jetbrains-mono-latin-variable.woff2']) {
   assert.ok(fonts.includes('./fonts/'+font),font);
   const buf=fs.readFileSync(path.join(root,'fonts',font));
   assert.equal(buf.toString('utf8',0,4),'wOF2');
   assert.ok(buf.length>10000 && buf.length<100000);
 }
 assert.doesNotMatch(fonts,/data:font\\/woff2/i);
 // Persian ZWNJ/ZWJ belongs to the Arabic font, not Latin fallback.
 assert.match(fonts,/U\+200C-200D/);
 assert.doesNotMatch(fonts,/U\+2000-206F/);
});
test('neutral charcoal skin improves typography without bringing back navy',()=>{
 for(const token of ['--ge-back:#171a1b','--ge-sidebar:#202526','--ge-surface:#272d2e',
   '--ge-surface-high:#303839','--ge-mint:#7af0c0','--ge-violet:#baa8ff',
   '--font:"DX UI"','--mono:"DX Mono"','font-optical-sizing:auto',
   'body.skin-cyber-classic .page-heading h1','body.skin-cyber-classic .nav-btn',
   'body.skin-cyber-classic .ov4-card','body.skin-cyber-classic .btn-primary',
   'body.skin-cyber-classic .btn.danger','body.skin-cyber-classic .iv3-drawer',
   'body.skin-cyber-classic #iv3-editor .iv3-deploy-card'])
   assert.ok(palette.includes(token),token);
 assert.doesNotMatch(palette,/#151d2b|#111725|#202d40|#1a2333|#283a51/);
});
test('strictly cosmetic: all routing controls and mobile contracts preserved',()=>{
 for(const token of ['function deploymentCard(','function deploymentTargets()',
   'name="tunnelEnable"','name="tunnelPort"','name="swapEnable"']) assert.ok(inbound.includes(token));
 for(const bad of [/\/api\//,/fetch\s*\(/i,/localStorage|sessionStorage/i,
   /\.innerHTML/,/pointer-events:\s*none/i,/grid-template-columns:/,
   /grid-template-areas:/,/position:\s*fixed/i,/display:\s*none\s*!important/i])
   assert.doesNotMatch(palette,bad);
 assert.match(palette,/:focus-visible/);
 assert.match(palette,/@media\(max-width:760px\)/);
 assert.match(palette,/html\[dir="rtl"\]/);
 assert.match(palette,/prefers-reduced-motion:reduce/);
 for(const css of [palette,fonts]){
   let n=0;
   for(const ch of css.replace(/\/\*[\s\S]*?\*\//g,'')){if(ch==='{')n++;else if(ch==='}')n--;assert.ok(n>=0)}
   assert.equal(n,0);
 }
});
