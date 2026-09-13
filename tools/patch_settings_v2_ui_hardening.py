#!/usr/bin/env python3
from pathlib import Path


def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:100])
    return s.replace(old,new,1)

p=Path('web/settings-v2.js');s=p.read_text()
old="""async function fetchData(){const req=[['panel','/api/settings/panel'],['runtime','/api/settings/runtime'],['subscription','/api/settings/subscription'],['guard','/api/settings/ipguard'],['runtimeStatus','/api/runtime-config'],['core','/api/core/state']];const out={};await Promise.all(req.map(async([k,u])=>{try{const r=await api(u);out[k]=r.value??r;}catch(ex){out[k]={error:ex.message};}}));SV.data=out;return out;}
"""
new="""function applyPrefs(p={}){document.body.dataset.darkDensity=p.density||'comfortable';document.body.classList.toggle('dark-reduced-motion',!!p.reduced_motion);document.title=(p.title||'DARK XRAY')+' | Cyber Control Center';}
async function fetchData(){const req=[['panel','/api/settings/panel'],['runtime','/api/settings/runtime'],['subscription','/api/settings/subscription'],['guard','/api/settings/ipguard'],['runtimeStatus','/api/runtime-config'],['core','/api/core/state']];const out={};await Promise.all(req.map(async([k,u])=>{try{const r=await api(u);out[k]=r.value??r;}catch(ex){out[k]={error:ex.message};}}));SV.data=out;applyPrefs(out.panel);return out;}
"""
s=once(s,old,new)
old="""${field(L('Rows per page','تعداد ردیف صفحه'),'page_size',p.page_size||50,'number','', 'min=\"10\" max=\"1000\" required')}<label class=\"sv2-field\"><span>${L('Calendar','تقویم')}</span>"""
new="""<label class=\"sv2-field\"><span>${L('Calendar','تقویم')}</span>"""
s=once(s,old,new)
old="""old.timezone=formValue(form,'timezone').trim();old.page_size=n(formValue(form,'page_size'),50);old.datepicker=formValue(form,'datepicker');localStorage.setItem('dark_lang',old.language);"""
new="""old.timezone=formValue(form,'timezone').trim();old.datepicker=formValue(form,'datepicker');localStorage.setItem('dark_lang',old.language);"""
s=once(s,old,new)
old="""}await api('/api/settings/panel','PUT',{value:old});toast(L('Settings saved.','تنظیمات ذخیره شد.'));}"""
new="""}await api('/api/settings/panel','PUT',{value:old});if(state.me)state.me.ui={...old};applyPrefs(old);toast(L('Settings saved.','تنظیمات ذخیره شد.'));}"""
s=once(s,old,new)
old="""}else{old.access_mode=formValue(form,'access_mode');old.domain=formValue(form,'domain').trim();old.acme_email=formValue(form,'acme_email').trim();}await api('/api/settings/runtime','PUT',{value:old});"""
new="""}else{old.access_mode=formValue(form,'access_mode');if(old.access_mode==='ssh'){old.domain='';old.acme_email='';}else{old.domain=formValue(form,'domain').trim();old.acme_email=formValue(form,'acme_email').trim();}}await api('/api/settings/runtime','PUT',{value:old});"""
s=once(s,old,new);p.write_text(s)

p=Path('web/live.js');s=p.read_text()
old="""const gb=1024**3, enc=encodeURIComponent, fa=n=>Number(n||0).toLocaleString('fa-IR');
const date=t=>t?new Date(t*1000).toLocaleString('fa-IR'):'—';
"""
new="""const gb=1024**3, enc=encodeURIComponent, fa=n=>Number(n||0).toLocaleString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US');
const date=t=>{if(!t)return '—';const lang=(localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US',cal=state?.me?.ui?.datepicker==='jalalian'?'-u-ca-persian':'',zone=state?.me?.ui?.timezone||'UTC';try{return new Date(t*1000).toLocaleString(lang+cal,{timeZone:zone});}catch{return new Date(t*1000).toLocaleString(lang);}};
"""
s=once(s,old,new)
old="""function shell(){const nav=navItems();const active=nav.find(n=>n[0]===state.page)?.[1]||enginePages[state.page]?.[0]||'DARK XRAY';"""
new="""function shell(){const ui=state.me?.ui||{};document.body.dataset.darkDensity=ui.density||'comfortable';document.body.classList.toggle('dark-reduced-motion',!!ui.reduced_motion);document.title=(ui.title||'DARK XRAY')+' | Cyber Control Center';const nav=navItems();const active=nav.find(n=>n[0]===state.page)?.[1]||enginePages[state.page]?.[0]||'DARK XRAY';"""
s=once(s,old,new)
old="""async function renderPage(){let html;try{"""
new="""async function renderPage(){let html;try{"""
# No textual change: marker retained for the footer patch below.
if old not in s:raise RuntimeError('renderPage anchor missing')
old_footer="""if($('#content'))$('#content').innerHTML=html+`<footer><span>DARK API · DARK DATABASE · Xray-core</span><span class=\"mono\">0.6.0 STANDALONE</span></footer>`;}"""
new_footer="""if($('#content')){const support=state.me?.ui?.support_url||'';$('#content').innerHTML=html+`<footer><span>DARK API · DARK DATABASE · Xray-core${support?` · <a href=\"${e(support)}\" target=\"_blank\" rel=\"noopener noreferrer\">Support</a>`:''}</span><span class=\"mono\">0.6.0 STANDALONE</span></footer>`;}}"""
s=once(s,old_footer,new_footer)
old="""await api('/api/auth/login','POST',{username:f.get('username'),password:f.get('password'),otp:f.get('otp')});state.me=await api('/api/me');state.page='dashboard';shell();await refresh();"""
new="""await api('/api/auth/login','POST',{username:f.get('username'),password:f.get('password'),otp:f.get('otp')});state.me=await api('/api/me');if(!localStorage.getItem('dark_lang'))localStorage.setItem('dark_lang',state.me.ui?.language||'en');state.page='dashboard';shell();await refresh();"""
s=once(s,old,new)
old="""(async()=>{try{state.me=await api('/api/me');shell();await refresh();}catch{loginPage();}})();"""
new="""(async()=>{try{state.me=await api('/api/me');if(!localStorage.getItem('dark_lang'))localStorage.setItem('dark_lang',state.me.ui?.language||'en');shell();await refresh();}catch{loginPage();}})();"""
s=once(s,old,new);p.write_text(s)
