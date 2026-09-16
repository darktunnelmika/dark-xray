/* DARK XRAY RBAC V2 — role ceilings mirrored from the server-side authority model. */
(function(){
'use strict';
if(typeof adminForm!=='function')return;

const RPERMS={
 reseller:['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','system.read','audit.read','api.manage','inbounds.read'],
 readonly:['clients.read','owners.read','finance.read','ip.read','system.read','audit.read','inbounds.read'],
 owner:[]
};
const ORDER=['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','system.read','audit.read','api.manage','inbounds.read'];
const SCOPE={none:'بدون دسترسی',own:'فقط خود',all:'همه'};

function permissionGrid(role,permissions={}){
 const allowed=new Set(RPERMS[role]||[]);
 if(role==='owner')return '<div class="notice warning">مالک اصلی به همه بخش‌های مدیریتی دسترسی دارد؛ Credit/Refund و تنظیمات سیستمی قابل واگذاری نیستند.</div>';
 return `<div class="notice">سقف نقش روی سرور enforce می‌شود. گزینه‌های خاکستری عمداً قابل اعطا نیستند؛ Credit/Refund همیشه Owner-only است.</div><div class="permission-grid">${ORDER.map(k=>{
   const ok=allowed.has(k),value=ok?(permissions[k]||'none'):'none';
   return `<label class="${ok?'':'muted'}">${e(k)}${ok?'':' · locked'}</label><select name="perm:${e(k)}" ${ok?'':'disabled'}>${['none','own','all'].map(v=>`<option value="${v}" ${v===value?'selected':''}>${SCOPE[v]}</option>`).join('')}</select>`;
 }).join('')}</div>`;
}
function delegated(role,f){
 const out={};for(const k of RPERMS[role]||[])out[k]=f.get('perm:'+k)||'none';return out;
}

adminForm=async function(id=null){
 const a=id?(await api('/api/admins')).find(x=>x.id===id):null;
 const role=a?.role||'reseller';
 dialog(id?'مجوزهای '+id:'حساب ورود جدید',`<div class="form-grid">
   ${field('نام کاربری','username',id||'','text',id?'readonly':'required')}
   ${field(id?'رمز جدید (خالی = بدون تغییر)':'رمز (حداقل ۸ کاراکتر)','password','','password',id?'minlength="8"':'required minlength="8"')}
   ${id?field('نقش','role',role,'text','readonly'):select('نقش پایه','role',[['reseller','نماینده'],['readonly','مشاهده‌گر'],['owner','مالک اصلی']],role)}
   ${id?select('وضعیت حساب','disabled',[['false','فعال'],['true','غیرفعال']],String(!!a.disabled)):''}
   <div class="span-2 notice">نماینده به Owner Profile همنام نیاز دارد. Scope «همه» فقط برای نقش نماینده و با تصمیم مالک می‌تواند دسترسی بین نماینده‌ها بدهد.</div>
   <div class="span-2" id="rbac-permissions">${permissionGrid(role,a?.permissions||{})}</div>
 </div>`,async f=>{
   const selectedRole=id?role:f.get('role');
   if(id){
     const patch={disabled:f.get('disabled')==='true'};
     if(selectedRole!=='owner')patch.permissions=delegated(selectedRole,f);
     if(f.get('password'))patch.password=f.get('password');
     await api('/api/admins/'+enc(id),'PATCH',patch);
   }else{
     await api('/api/admins','POST',{username:f.get('username'),password:f.get('password'),role:selectedRole,permissions:selectedRole==='owner'?{}:delegated(selectedRole,f)});
   }
   closeDialog();toast('حساب و مجوزها با سقف نقش ذخیره شدند.');await refresh();
 });
 if(!id){
   const roleSelect=document.querySelector('#dialog-form select[name="role"]');
   roleSelect?.addEventListener('change',()=>{const box=document.querySelector('#rbac-permissions');if(box)box.innerHTML=permissionGrid(roleSelect.value,{});});
 }
};
})();
