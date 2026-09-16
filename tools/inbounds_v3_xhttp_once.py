#!/usr/bin/env python3
import re
from pathlib import Path

p=Path('web/inbounds-v3.js')
s=p.read_text(encoding='utf-8')
new_x=""" if(network==='xhttp'){st.xhttpSettings={...(st.xhttpSettings||{}),path:fd.get('xhPath')||'/',host:fd.get('xhHost')||'',mode:fd.get('xhMode')||'auto'};if(fd.get('xhPadding'))st.xhttpSettings.xPaddingBytes=fd.get('xhPadding');else delete st.xhttpSettings.xPaddingBytes;}"""
new_submit="""form.addEventListener('submit',async ev=>{ev.preventDefault();try{await saveEditor(form,id);}catch(ex){toast(ex?.message||L('Inbound save failed.','ذخیره اینباند ناموفق بود.'),true);}});"""

if new_x not in s:
    pat_x=re.compile(r" if\(network==='xhttp'\)st\.xhttpSettings=\{\.\.\.\(st\.xhttpSettings\|\|\{\}\),path:fd\.get\('xhPath'\)\|\|'/',host:fd\.get\('xhHost'\)\|\|'',mode:fd\.get\('xhMode'\)\|\|'auto'\};if\(fd\.get\('xhPadding'\)\)st\.xhttpSettings\.xPaddingBytes=fd\.get\('xhPadding'\);else delete st\.xhttpSettings\.xPaddingBytes;")
    s,n=pat_x.subn(new_x,s,count=1)
    if n!=1:raise SystemExit(f'xhttp anchor match count={n}')
if new_submit not in s:
    pat_submit=re.compile(r"form\.addEventListener\('submit',async ev=>\{ev\.preventDefault\(\);await saveEditor\(form,id\);\}\);")
    s,n=pat_submit.subn(new_submit,s,count=1)
    if n!=1:raise SystemExit(f'submit anchor match count={n}')

p.write_text(s,encoding='utf-8')
