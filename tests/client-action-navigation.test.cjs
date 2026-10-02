const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const live=fs.readFileSync('web/live.js','utf8');
test('generic action buttons never submit surrounding dialogs',()=>{assert.match(live,/function button\([^)]*\)\{return `<button type=\"button\"/);});
test('delegated data actions suppress native navigation and form submission',()=>{assert.match(live,/closest\('\[data-page\],\[data-act\]'\);if\(!el\)return;ev\.preventDefault\(\)/);});