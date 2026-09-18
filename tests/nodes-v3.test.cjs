const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');

test('Node add/edit exposes per-node inbound assignment picker',()=>{
  assert.match(src,/name="inboundIds"/);
  assert.match(src,/getAll\('inboundIds'\)/);
  assert.match(src,/Select at least one inbound for this node/);
  assert.match(src,/Only selected inbounds and their attached clients are mirrored/);
});

test('Node workflow replaces manual clone with assignment sync',()=>{
  assert.match(src,/'nv2sync'/);
  assert.match(src,/\/api\/nodes\/'.*\/sync/);
  assert.doesNotMatch(src,/nv2cloneinbound/);
  assert.doesNotMatch(src,/Clone local inbound to node/);
});

test('Node cards expose assigned inbound names and sync state',()=>{
  assert.match(src,/function assignedNames\(n\)/);
  assert.match(src,/ASSIGNED INBOUNDS/);
  assert.match(src,/n\.assignments/);
  assert.match(src,/n\.assignments/);
  assert.match(src,/last_error/);
});
