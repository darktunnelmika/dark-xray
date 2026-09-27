const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','inbounds-v3.js'),'utf8');

test('XHTTP padding mutation is scoped to the XHTTP transport block',()=>{
  const guarded="if(network==='xhttp'){st.xhttpSettings=";
  assert.ok(src.includes(guarded),'XHTTP settings must be opened inside an explicit block');
  assert.ok(src.includes("else delete st.xhttpSettings.xPaddingBytes;}"),'XHTTP padding cleanup must close inside that block');
  assert.ok(!src.includes("if(network==='xhttp')st.xhttpSettings="),'the former one-statement guard must not return');
});

test('Inbound editor submit failures are surfaced to the operator',()=>{
  assert.ok(src.includes("try{await saveEditor(form,id);}catch(ex){toast(ex?.message"));
  assert.ok(!src.includes("ev.preventDefault();await saveEditor(form,id);"));
});


test('REALITY UI no longer suggests the known-bad Microsoft target',()=>{
  assert.ok(src.includes('placeholder="www.bing.com:443"'));
  assert.ok(src.includes("www.microsoft.com is blocked as a REALITY target"));
  assert.ok(src.includes("use-suggested-target"));
});

test('Representative inbound surface is read-only and client creation is not exposed there',()=>{
  assert.ok(src.includes("ownerMode=isOwner()"));
  assert.ok(src.includes("isOwner()?`<button class=\"btn btn-primary\" data-v3-action=\"new\""));
  assert.ok(src.includes("iv3-readonly-toggle"));
  assert.ok(!src.includes('data-v3-action="add-client"'));
  assert.ok(!src.includes("if(act==='add-client')"));
});

test('Inbound mobile stylesheet increases touch targets and technical text readability',()=>{
  const css=fs.readFileSync(path.join(__dirname,'..','web','inbounds-v3.css'),'utf8');
  assert.match(css,/Representative read-only state \+ mobile readability/);
  assert.match(css,/\.iv3-name strong\{font-size:13px\}/);
  assert.match(css,/\.iv3-actions button,.iv3-client-actions button,.iv3-icon\{width:40px;height:40px\}/);
  assert.match(css,/\.iv3-search input\{font-size:16px;min-height:42px\}/);
});
