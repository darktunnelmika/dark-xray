const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const live=fs.readFileSync(path.join(__dirname,'..','web','live.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');

test('owner navigation exposes representatives without access-control workspace',()=>{
  assert.match(live,/\['resellers','نمایندگان','reseller'\]/);
  assert.doesNotMatch(live,/\['roles','سطوح دسترسی'/);
  assert.doesNotMatch(live,/case'roles'/);
  assert.doesNotMatch(live,/function rolesPage/);
});

test('representative editor is unified and has no role or permission matrix',()=>{
  assert.match(live,/\/api\/resellers\//);
  assert.match(live,/max_client_hwid/);
  assert.match(live,/max_client_ips/);
  assert.match(live,/prefix/);
  assert.match(live,/ownerdelete/);
  assert.match(live,/نماینده Role یا Permission قابل انتخاب ندارد/);
  assert.doesNotMatch(live,/async function adminForm/);
  assert.doesNotMatch(live,/const PERMS=/);
  assert.doesNotMatch(live,/\/api\/admins/);
});

test('legacy RBAC editor is not loaded by the application shell',()=>{
  assert.doesNotMatch(html,/rbac-v2\.js/);
});

test('representative cards expose login and policy state',()=>{
  assert.match(live,/login_ready/);
  assert.match(live,/Max IP \/ HWID/);
  assert.match(live,/Prefix مشتری/);
  assert.match(live,/حذف نماینده/);
});
