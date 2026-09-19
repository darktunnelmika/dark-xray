const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','live.js'),'utf8');

test('renderPage rejects stale async renders after navigation',()=>{
  assert.match(src,/renderSeq:0/);
  assert.match(src,/const requestedPage=state\.page,seq=\+\+state\.renderSeq/);
  assert.match(src,/seq!==state\.renderSeq\|\|requestedPage!==state\.page/);
  assert.match(src,/if\(seq!==state\.renderSeq\|\|requestedPage!==state\.page\)return/);
});

test('renderPage dispatch is bound to captured requested page',()=>{
  assert.match(src,/if\(enginePages\[requestedPage\]\)/);
  assert.match(src,/switch\(requestedPage\)/);
});
