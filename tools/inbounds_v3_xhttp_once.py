#!/usr/bin/env python3
from pathlib import Path

p=Path('web/inbounds-v3.js')
s=p.read_text(encoding='utf-8')
old_x=""" if(network==='xhttp')st.xhttpSettings={...(st.xhttpSettings||{}),path:fd.get('xhPath')||'/',host:fd.get('xhHost')||'',mode:fd.get('xhMode')||'auto'};if(fd.get('xhPadding'))st.xhttpSettings.xPaddingBytes=fd.get('xhPadding');else delete st.xhttpSettings.xPaddingBytes;"""
new_x=""" if(network==='xhttp'){st.xhttpSettings={...(st.xhttpSettings||{}),path:fd.get('xhPath')||'/',host:fd.get('xhHost')||'',mode:fd.get('xhMode')||'auto'};if(fd.get('xhPadding'))st.xhttpSettings.xPaddingBytes=fd.get('xhPadding');else delete st.xhttpSettings.xPaddingBytes;}"""
old_submit=""" form.addEventListener('submit',async ev=>{ev.preventDefault();await saveEditor(form,id);});"""
new_submit=""" form.addEventListener('submit',async ev=>{ev.preventDefault();try{await saveEditor(form,id);}catch(ex){toast(ex?.message||L('Inbound save failed.','ذخیره اینباند ناموفق بود.'),true);}});"""
if new_x not in s:
    if old_x not in s:raise SystemExit('xhttp anchor missing')
    s=s.replace(old_x,new_x,1)
if new_submit not in s:
    if old_submit not in s:raise SystemExit('submit anchor missing')
    s=s.replace(old_submit,new_submit,1)
p.write_text(s,encoding='utf-8')
