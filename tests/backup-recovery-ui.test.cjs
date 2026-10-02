const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const js=fs.readFileSync('web/ops-v2.js','utf8'),overview=fs.readFileSync('web/overview-v4.js','utf8');
function fixture(){
 const calls=[];const ctx={console,state:{page:'backup',me:{id:'dark',role:'owner'},errors:{}},enginePages:{},
  dashboard(){return ''},load:async()=>{},enginePage:async()=>'',navItems:()=>[],runAction:async(...args)=>calls.push(args),
  go:async()=>{},refresh:async()=>{},renderPage:async()=>{},api:async()=>({database_bytes:10,managed_clients:2,audit_rows:3}),
  isOwner:()=>true,can:()=>true,localStorage:{getItem:()=> 'en'},e:x=>String(x??''),
  heading:()=>'',icon:()=>'',bytes:String,appUrl:x=>x};
 vm.createContext(ctx);vm.runInContext(js,ctx);return {ctx,calls};
}
test('Backup page exposes existing encrypted workflow and honest DB-only scope',async()=>{
 const {ctx}=fixture(),html=await ctx.enginePage();
 assert.match(html,/data-act="backupfull"/);assert.match(html,/not encrypted/);assert.match(html,/not a full disaster-recovery backup/);
 assert.match(html,/DNS\/TLS recheck/);assert.match(html,/new token and forum rebind/);
 assert.doesNotMatch(html,/stays a terminal operation/);
});
test('legacy backup helper delegates to existing passphrase/confirmation workflow',async()=>{
 const {ctx,calls}=fixture();const element={};await ctx.runAction('ov2backuphelp',element);
 assert.equal(calls.length,1);assert.equal(calls[0][0],'backupfull');assert.equal(calls[0][1],element);
});
test('active overview offers full encrypted backup as well as DB-only download',()=>{
 const card=overview.slice(overview.indexOf('function backupCard()'),overview.indexOf('function repsCard()'));
 assert.match(card,/data-act="backupfull"/);assert.match(card,/\/api\/backup/);assert.match(card,/ov2restorehelp/);
});

test('shell commands opt out of automatic UI translation and remain left-to-right',()=>{
 const commands=[...js.matchAll(/<div class="ov2-command"([^>]*)>/g)];assert.ok(commands.length>=4);
 for(const [,attrs] of commands){assert.match(attrs,/data-no-i18n/);assert.match(attrs,/dir="ltr"/);}
});
