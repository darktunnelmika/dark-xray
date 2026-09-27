const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const matrix=fs.readFileSync(path.join(__dirname,'..','web','traffic-matrix.js'),'utf8');
const inbound=fs.readFileSync(path.join(__dirname,'..','web','inbounds-v3.js'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');

test('Traffic Matrix is the owner entry point for runtime Direct and Tunnel policy',()=>{
 assert.match(inbound,/data-act="tmopen"/);
 assert.match(matrix,/DARK TRAFFIC MATRIX/);
 assert.match(matrix,/warp_ai_adblock/);
 assert.match(matrix,/warp_all_adblock/);
 assert.match(matrix,/Apply both/);
});

test('Traffic Matrix exposes per-path and per-server probe controls',()=>{
 assert.match(matrix,/data-act="tmprobe"/);
 assert.match(matrix,/data-act="tmprobeall"/);
 assert.match(matrix,/\/api\/traffic-matrix\/probe/);
 assert.match(matrix,/listener ok/);
});

test('WARP creation and path scan live inside Traffic Matrix',()=>{
 assert.match(matrix,/data-act="tmwarpcreate"/);
 assert.match(matrix,/data-act="tmwarpscan"/);
 assert.match(matrix,/\/api\/traffic-matrix\/warp\/create/);
 assert.match(matrix,/\/api\/traffic-matrix\/warp\/scan/);
 assert.match(matrix,/\/api\/traffic-matrix\/warp\/endpoint/);
});

test('Traffic Matrix assets load after Inbounds V3',()=>{
 assert.match(index,/assets\/traffic-matrix\.css/);
 assert.match(index,/assets\/traffic-matrix\.js/);
 assert.ok(index.indexOf('assets/inbounds-v3.js')<index.indexOf('assets/traffic-matrix.js'));
});
