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
