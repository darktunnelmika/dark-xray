const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');

const i18n=read('web/i18n-en.js');
const live=read('web/live.js');
const inbounds=read('web/inbounds-v3.js');
const webDir=path.join(__dirname,'..','web');
const html=fs.readFileSync(path.join(webDir,'index.html'),'utf8');
const loadedJs=[...html.matchAll(/src="assets\/([^"]+\.js)"/g)].map(m=>m[1]);
const allUi=html+'\n'+loadedJs.map(n=>fs.readFileSync(path.join(webDir,n),'utf8')).join('\n');
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

test('CPU telemetry uses a stable sample and formats zero consistently',()=>{
  assert.match(core,/def _host_cpu_percent/);
  assert.match(core,/self\._cpu_sample_at/);
  assert.match(core,/psutil\.cpu_percent\(interval=\.2\)/);
  assert.doesNotMatch(core,/cpu_percent\(interval=\.05\)/);
  assert.doesNotMatch(server,/cpu_percent\(interval=\.05\)/);
  assert.match(overview,/function fmtPercent/);
  assert.match(overview,/if\(v===0\)return '0\.0'/);
  assert.match(overview,/if\(v>0&&v<0\.1\)return '<0\.1'/);
  assert.match(overview,/AVG \$\{e\(fmtPercent\(avg\(history\)\)\)\}%/);
  assert.match(overview,/PEAK \$\{e\(fmtPercent\(peak\(history\)\)\)\}%/);
  assert.match(live,/Number\(n\.cpu\)===0\?'0\.0'/);
});


test('language purity audit covers the whole live UI and both directions',()=>{
  assert.match(i18n,/window\.DarkI18nAudit=/);
  assert.match(i18n,/collectLeaks:auditLeaks/);
  assert.match(i18n,/const reverseWords=/);
  assert.match(i18n,/const persianLeakRe=/);
  assert.match(i18n,/\.cyber-lang-switch/);
  assert.match(i18n,/\[data-sv2-segment="language"\]/);
});

test('Persian mode normalizes common user-facing UI concepts while preserving technical surfaces',()=>{
  for(const token of ["['Outbound','اوتباند']","['Rule','قانون']","['Runtime','محیط اجرا']",
    "['Subscription','اشتراک']","['Dashboard','داشبورد']","['Certificate','گواهی']"]){
    assert.ok(i18n.includes(token),token);
  }
  assert.match(i18n,/\[dir="ltr"\]/);
  assert.match(i18n,/\.mono/);
});


test('technical literals are excluded from language leakage heuristics without disabling prose audit',()=>{
  assert.ok(i18n.includes('function auditComparable'));
  assert.ok(i18n.includes(".replace(/\\?[A-Za-z]"));
  assert.ok(i18n.includes(".replace(/\\/[A-Za-z0-9_.-]+"));
  assert.ok(i18n.includes('persianLeakRe.test(auditComparable(raw))'));
});


test('LTR document direction does not disable English normalization and English is the browser default',()=>{
  assert.match(i18n,/return el\.matches\('\[dir="ltr"\]'\)/);
  assert.doesNotMatch(i18n,/closest\([^\n]*\[dir="ltr"\]/);
  assert.match(live,/if\(!localStorage\.getItem\('dark_lang'\)\)localStorage\.setItem\('dark_lang','en'\)/);
  assert.doesNotMatch(live,/localStorage\.setItem\('dark_lang',state\.me\.ui\?\.language/);
});


test('English normalization applies multi-word exact mappings inside compound labels',()=>{
  assert.match(i18n,/const exactPhrases=/);
  assert.match(i18n,/for\(const \[a,b\] of exactPhrases\)core=core\.split\(a\)\.join\(b\)/);
  assert.ok(i18n.includes("'آدرس‌های عمومی':'Public Endpoints'"));
  assert.ok(i18n.includes("'نمای کلی':'Overview'"));
});


test('English normalization applies single-word exact mappings inside compound labels',()=>{
  assert.match(i18n,/const exactWords=/);
  assert.match(i18n,/for\(const \[a,b\] of exactWords\)core=replaceFaWord\(core,a,b\)/);
  assert.ok(i18n.includes("'کاربران':'Clients'"));
  assert.ok(i18n.includes("'نمایندگان':'Resellers'"));
});


test('full sentence translations run before partial phrase and word replacements',()=>{
  const phrasePos=i18n.indexOf('for(const [a,b] of phrases)core=core.split(a).join(b)');
  const exactPhrasePos=i18n.indexOf('for(const [a,b] of exactPhrases)core=core.split(a).join(b)');
  assert.ok(phrasePos>0 && exactPhrasePos>phrasePos);
  assert.ok(i18n.includes("['با تغییر رمز، تمام نشست‌های این حساب باطل می‌شوند.','Changing the password revokes all sessions for this account.']"));
});


test('healthy production shell has no redundant live status banner and top breadcrumb is compact',()=>{
  assert.match(live,/const runtimeBanner=state\.me\.test_engine/);
  assert.match(live,/!state\.me\.writes_enabled/);
  assert.match(live,/:'';\$\('#app'\)\.innerHTML/);
  assert.match(live,/<span class="breadcrumb"><b>\$\{e\(active\)\}<\/b><\/span>/);
  assert.doesNotMatch(live,/حالت متصل · بدون دادهٔ نمونه/);
});
