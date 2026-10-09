const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const root=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'classic-gangster-v2.css'),'utf8');
const source=fs.readFileSync(path.join(root,'inbounds-v3.js'),'utf8');

test('Classic Gangster loads last, preserving the older skin for rollback',()=>{
  const styles=[...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(x=>x[1]);
  assert.ok(styles.includes('assets/classic-gangster-v2.css?v=1'));
  assert.ok(styles.indexOf('assets/classic-gangster-v2.css?v=1') > styles.indexOf('assets/premium-cyber-v1.css?v=1'));
  assert.ok(styles.includes('assets/premium-cyber-v1.css?v=1'));
  assert.ok(styles.includes('assets/cyber-classic.css'));
  assert.ok(styles.includes('assets/mobile-responsive-v1.css'));
  assert.ok(styles.indexOf('assets/classic-gangster-v2.css?v=1') > styles.indexOf('assets/premium-cyber-v1.css?v=1'));
});

test('Classic skin is vivid and intentional without desaturating warnings or errors',()=>{
  for(const needle of [
    '--gang-emerald: #4cffb4','--gang-cyan: #5bc7ec','--gang-gold: #deb979',
    'body.skin-cyber-classic .nav-btn','body.skin-cyber-classic .nav-btn.active',
    'body.skin-cyber-classic .btn-primary','body.skin-cyber-classic .btn.danger',
    'body.skin-cyber-classic .page-heading h1','body.skin-cyber-classic .iv3-drawer',
    'body.skin-cyber-classic .iv3-tab-more','body.skin-cyber-classic .iv3-field>span',
    'body.skin-cyber-classic .iv3-deploy-grid','body.skin-cyber-classic .iv3-tunnel-route',
    'body.skin-cyber-classic .notice.warning','body.skin-cyber-classic .notice.error'
  ]) assert.ok(css.includes(needle),needle);
  assert.ok(css.includes('min-height:42px'));
  assert.ok(css.includes('font-size:12px'));
});

test('Inbound editor keeps every section/action and puts complex tools behind optional disclosure',()=>{
  const nav=source.slice(source.indexOf('<nav class="iv3-tabs"'),source.indexOf('</nav><main>'));
  for(const t of ['general','transport','security','sniffing','fallbacks','advanced']){
    assert.ok(nav.includes("'"+t+"'"),t);
  }
  assert.ok(nav.includes('class="iv3-tab-group"'));
  assert.ok(nav.includes('<details class="iv3-tab-more">'));
  assert.ok(nav.includes('data-v3-action="tab"'));
  assert.ok(nav.includes('aria-pressed'));
  for(const field of ['remark','port','listen','protocol','network','security','privateKey','shortIds','deployLocal']) {
    assert.ok(source.includes(field),'preserve '+field);
  }
  assert.ok(source.includes('form.addEventListener(\'submit\''));
  assert.ok(source.includes('await saveEditor(form,id)'));
  assert.ok(source.includes("x.setAttribute('aria-pressed'"));
});

test('Styling cannot change hidden field meanings, control events or layout contracts',()=>{
  for(const bad of [/\/api\//,/fetch\s*\(/,/localStorage/,/sessionStorage/,
    /pointer-events:\s*none/i,/grid-template-areas\s*:/i,/content:\s*url\(/i]) {
    assert.doesNotMatch(css,bad);
  }
  assert.match(css,/@media\(max-width:760px\)/);
  assert.match(css,/@media\(max-width:390px\)/);
  assert.match(css,/prefers-reduced-motion:reduce/);
  assert.match(css,/:focus-visible/);
  assert.match(css,/html\[dir=rtl\]/);
  let depth=0;
  for(const c of css.replace(/\/\*[\s\S]*?\*\//g,'')){
    if(c==='{')depth++;
    if(c==='}')depth--;
    assert.ok(depth>=0,'extra closing block');
  }
  assert.equal(depth,0);
});
