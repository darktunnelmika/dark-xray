#!/usr/bin/env python3
"""Minimal operator Telegram bot for DARK License Authority V1.
Commands are restricted to DARK_LICENSE_TELEGRAM_ADMINS. Payment automation is intentionally separate.
"""
import os,time,httpx
TOKEN=os.environ.get('DARK_LICENSE_TELEGRAM_BOT_TOKEN','').strip();ADMIN_TOKEN=os.environ.get('DARK_LICENSE_ADMIN_TOKEN','').strip();ADMINS={int(x) for x in os.environ.get('DARK_LICENSE_TELEGRAM_ADMINS','').split(',') if x.strip().isdigit()};BASE=os.environ.get('DARK_LICENSE_LOCAL_URL','http://127.0.0.1:8099').rstrip('/')
if not TOKEN or not ADMIN_TOKEN or not ADMINS:raise SystemExit('Bot token, admin token and Telegram admin IDs are required')
TG=f'https://api.telegram.org/bot{TOKEN}'
def send(chat,text):httpx.post(TG+'/sendMessage',json={'chat_id':chat,'text':text},timeout=15).raise_for_status()
def admin_post(path,body=None):r=httpx.post(BASE+path,headers={'X-Dark-License-Admin':ADMIN_TOKEN},json=body or {},timeout=15);r.raise_for_status();return r.json()
def handle(msg):
 chat=int(msg.get('chat',{}).get('id') or 0);uid=int(msg.get('from',{}).get('id') or 0);text=str(msg.get('text') or '').strip();parts=text.split()
 if uid not in ADMINS:return send(chat,'دسترسی مدیریت License Server ندارید.')
 try:
  if parts and parts[0] in ('/start','/help'):return send(chat,'DARK License V1\n/issue TELEGRAM_ID DAYS\n/revoke LICENSE_ID\n/replace LICENSE_ID')
  if len(parts)==3 and parts[0]=='/issue':
   doc=admin_post('/admin/licenses',{'telegram_id':int(parts[1]),'days':int(parts[2])});return send(chat,f"License: {doc['license_id']}\nKey: {doc['license_key']}\nاین کلید فقط یک‌بار برای تحویل نمایش داده شود.")
  if len(parts)==2 and parts[0]=='/revoke':admin_post('/admin/licenses/'+parts[1]+'/revoke');return send(chat,'License revoked.')
  if len(parts)==2 and parts[0]=='/replace':
   doc=admin_post('/admin/licenses/'+parts[1]+'/replace');return send(chat,f"Old license revoked.\nNew: {doc['license_id']}\nKey: {doc['license_key']}")
  send(chat,'دستور نامعتبر. /help')
 except Exception as ex:send(chat,'خطا: '+str(ex)[:300])
offset=0
while True:
 try:
  r=httpx.get(TG+'/getUpdates',params={'offset':offset,'timeout':30,'allowed_updates':'["message"]'},timeout=40);r.raise_for_status()
  for item in r.json().get('result',[]):offset=max(offset,int(item['update_id'])+1);msg=item.get('message');msg and handle(msg)
 except Exception:time.sleep(3)