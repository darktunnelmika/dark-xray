const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','sync-v4.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','sync-v4.css'),'utf8');
const live=fs.readFileSync(path.join(__dirname,'..','web','live.js'),'utf8');

test('Sync V4 maps backend reason codes instead of parsing error text',()=>{
  for(const code of ['clean','queued','retry_wait','operation_error','uncertain_reset','identity_conflict','runtime_missing','external_disabled'])
    assert.match(src,new RegExp(code));
  assert.match(src,/reason_code/);
  assert.match(src,/next_action/);
  assert.doesNotMatch(src,/includes\(['"]CoreEngine client missing/);
});

test('Sync V4 exposes safe recovery actions',()=>{
  assert.match(src,/sy4retry/);
  assert.match(src,/\/sync-retry/);
  assert.match(src,/sy4restore/);
  assert.match(src,/\/restore-missing/);
  assert.match(src,/sy4resolve/);
  assert.match(src,/\/resolve-reset/);
  assert.match(src,/sy4control/);
  assert.match(src,/action:'enable'/);
});

test('Missing and uncertain destructive recovery require typed identity',()=>{
  assert.match(src,/Client identity confirmation/);
  assert.match(src,/value!==id/);
  assert.match(src,/does NOT replay the destructive reset/);
  assert.match(src,/Automatic recreation is intentionally refused/);
});

test('Sync V4 separates Manager Runtime queue and Nodes',()=>{
  for(const label of ['RECONCILER','LOCAL XRAY','CLIENT QUEUE','NODES'])assert.match(src,new RegExp(label));
  assert.match(src,/generation_state/);
  assert.match(src,/poll_age_seconds/);
  assert.match(src,/assignment_errors/);
  assert.match(src,/pending_deploys/);
});

test('Sync V4 has dedicated Cyber Classic responsive layout',()=>{
  assert.match(css,/DARK XRAY Sync \/ Runtime V4/);
  assert.match(css,/\.sy4-control-grid/);
  assert.match(css,/\.sy4-items/);
  assert.match(css,/\.sy4-chip/);
  assert.match(css,/@media\(max-width:760px\)/);
});

test('legacy sync page remains only a fallback overridden by Sync V4',()=>{
  assert.match(live,/function syncPage\(\)/);
  assert.match(src,/syncPage=function\(\)/);
});
