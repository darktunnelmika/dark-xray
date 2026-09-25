from __future__ import annotations
import json
import secrets
import threading
import time
from typing import Any

import httpx

from auth import DEFAULTS
from dark_policy import Actor, NAME_RE, PolicyError
from telegram_forum import TelegramForumCenter
from telegram_customer import CustomerCenter
from telegram_customer_runtime import CustomerBotFeatures

API_ROOT='https://api.telegram.org'

def amount(value:int,currency:str)->str:
    code=str(currency or '').upper()
    if code=='IRT':return f"{int(value):,} تومان"
    if code=='IRR':return f"{int(value):,} ریال"
    return f"{int(value):,} {code}"

class TelegramAPI:
    def __init__(self,token:str):
        self.token=token
        self.client=httpx.Client(timeout=httpx.Timeout(35.0,connect=10.0))

    def close(self):
        self.client.close()

    def call(self,method:str,payload:dict[str,Any]|None=None)->Any:
        r=self.client.post(f"{API_ROOT}/bot{self.token}/{method}",json=payload or {})
        try:data=r.json()
        except Exception as ex:raise RuntimeError(f'Telegram returned HTTP {r.status_code}') from ex
        if not data.get('ok'):raise RuntimeError(str(data.get('description') or f'Telegram API {r.status_code}'))
        return data.get('result')

    def send(self,chat_id:int,text:str,reply_markup:dict|None=None)->Any:
        body={'chat_id':int(chat_id),'text':text}
        if reply_markup is not None:body['reply_markup']=reply_markup
        return self.call('sendMessage',body)

    def send_document_bytes(self,chat_id:int,filename:str,data:bytes,caption:str='',message_thread_id:int|None=None)->Any:
        fields={'chat_id':str(int(chat_id)),'caption':caption[:1000]}
        if message_thread_id is not None:fields['message_thread_id']=str(int(message_thread_id))
        r=self.client.post(f"{API_ROOT}/bot{self.token}/sendDocument",data=fields,
                           files={'document':(filename,data,'application/octet-stream')},timeout=60)
        try:doc=r.json()
        except Exception as ex:raise RuntimeError(f'Telegram returned HTTP {r.status_code}') from ex
        if not doc.get('ok'):raise RuntimeError(str(doc.get('description') or f'Telegram API {r.status_code}'))
        return doc.get('result')

class BotWorker(CustomerBotFeatures):
    def __init__(self,runtime,owner:str,token:str,token_mark:str):
        self.runtime=runtime;self.owner=owner;self.token=token;self.token_mark=token_mark
        self.stop_event=threading.Event();self.thread=None;self.api=TelegramAPI(token)
        self.sessions:dict[int,str]={};self.session_data:dict[int,dict[str,str]]={};self.bot_id=0

    def start(self):
        self.thread=threading.Thread(target=self.run,name='dark-telegram-'+self.owner,daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():self.thread.join(timeout=3)
        self.api.close()

    def status(self,state:str,error:str=''):
        self.runtime.set_status(self.owner,state,error)

    def run(self):
        try:
            me=self.api.call('getMe');self.bot_id=int(me.get('id') or 0)
            info=self.api.call('getWebhookInfo')
            if info.get('url'):self.api.call('deleteWebhook',{'drop_pending_updates':False})
            self.api.call('setMyCommands',{'commands':[
                {'command':'start','description':'شروع / منوی اصلی'},
                {'command':'shop','description':'فروشگاه'},
                {'command':'services','description':'سرویس‌های من'},
                {'command':'status','description':'وضعیت'},
            ]})
            with self.runtime.store.transaction() as db:
                db.execute("UPDATE telegram_bots SET bot_username=?,last_error='',last_seen=? WHERE owner=?",
                           (str(me.get('username') or ''),time.time(),self.owner))
            self.status('online')
            self.maybe_forum_prompt()
        except Exception as ex:
            self.runtime.persist_error(self.owner,str(ex));self.status('error',str(ex));return
        while not self.stop_event.is_set():
            try:
                row=self.runtime.commerce.bot_row(self.owner,secret=True)
                if not row or not row['enabled']:break
                offset=int(row.get('update_offset') or 0)
                updates=self.api.call('getUpdates',{'offset':offset,'timeout':25,'limit':50,
                    'allowed_updates':['message','callback_query']}) or []
                if not updates:
                    self.runtime.touch(self.owner)
                    self.runtime.forum.poll_audits(self.api,self.owner,self.owner_role())
                    timezone_name=str(self.runtime.manager.engine.section('panel').get('timezone','UTC'))
                    self.runtime.forum.maybe_daily_summary(self.api,self.owner,self.owner_role(),timezone_name)
                    continue
                for update in updates:
                    uid=int(update.get('update_id') or 0)
                    try:self.handle(update)
                    except Exception as ex:
                        self.runtime.persist_error(self.owner,str(ex))
                        self.notify_admin('خطای پردازش ربات:\n'+str(ex)[:700])
                    finally:
                        if uid:
                            with self.runtime.store.transaction() as db:
                                db.execute("UPDATE telegram_bots SET update_offset=?,last_seen=? WHERE owner=?",
                                           (uid+1,time.time(),self.owner))
                self.runtime.forum.poll_audits(self.api,self.owner,self.owner_role())
                timezone_name=str(self.runtime.manager.engine.section('panel').get('timezone','UTC'))
                self.runtime.forum.maybe_daily_summary(self.api,self.owner,self.owner_role(),timezone_name)
                self.status('online')
            except httpx.ReadTimeout:
                self.runtime.touch(self.owner);self.status('online')
                continue
            except Exception as ex:
                msg=str(ex);self.runtime.persist_error(self.owner,msg);self.status('error',msg)
                try:self.runtime.forum.report(self.api,self.owner,'errors','🚨 Telegram runtime error\n'+msg[:1200])
                except Exception:pass
                if self.stop_event.wait(3):break
        self.status('stopped')

    def bot_config(self)->dict[str,Any]:
        row=self.runtime.commerce.bot_row(self.owner,secret=True)
        if not row:raise PolicyError('Telegram bot configuration is missing')
        return row

    def is_admin(self,user_id:int)->bool:
        return int(self.bot_config()['admin_telegram_id'])==int(user_id)

    def actor(self)->Actor:
        return self.runtime.commerce.actor_for(self.owner)

    def owner_role(self)->str:
        return self.actor().role

    def main_keyboard(self,admin:bool)->dict:
        rows=[
            ['🛍 خرید اشتراک','🔄 تمدید سرویس'],
            ['💰 کیف پول + شارژ','📦 سرویس‌های من'],
            ['👥 زیرمجموعه‌گیری','🎫 پشتیبانی'],
        ]
        if admin:
            rows=[
                ['🏠 داشبورد','👥 کاربران'],
                ['📦 سرویس‌ها','🧾 سفارش‌ها'],
                ['🛠 مدیریت فروشگاه','💳 پرداخت دستی'],
                ['📊 گزارش‌ها','🎫 پشتیبانی'],
                ['💾 بکاپ','⚙️ تنظیمات ربات'],
            ]
            if self.owner_role()=='owner':rows += [['🤝 نمایندگان','➕ ساخت نماینده']]
            rows += [['🛍 خرید اشتراک','📦 سرویس‌های من']]
        return {'keyboard':[[{'text':x} for x in row] for row in rows],
                'resize_keyboard':True,'is_persistent':True}

    def send_home(self,chat_id:int,user_id:int):
        admin=self.is_admin(user_id)
        forum=self.runtime.forum.status(self.owner)
        if admin and forum.get('rebind_required'):
            self.api.send(chat_id,'♻️ بکاپ DARK بازیابی شده است. اطلاعات فروشگاه و انجمن حفظ شده‌اند.\n'
                          'Bot جدید را در انجمن قبلی Admin کن و دسترسی Manage Topics بده، سپس اتصال مجدد را بزن.',
                          {'inline_keyboard':[[{'text':'♻️ اتصال مجدد انجمن بکاپ','callback_data':'forumrebind'}]]})
            self.api.send(chat_id,'اگر انجمن قبلی دیگر وجود ندارد، یک انجمن جدید انتخاب کن.',
                          self.runtime.forum.request_keyboard());return
        if admin and not forum.get('configured'):
            self.api.send(chat_id,'مرحله اول: انجمن گزارش DARK را انتخاب کن. ربات باید Admin انجمن باشد و مجوز مدیریت Topicها را داشته باشد.',
                          self.runtime.forum.request_keyboard());return
        role='مدیریت + فروش' if admin else 'فروشگاه'
        self.api.send(chat_id,f'DARK XRAY BOT\nحالت: {role}\nیکی از گزینه‌ها را انتخاب کن.',self.main_keyboard(admin))

    def maybe_forum_prompt(self,force:bool=False):
        forum=self.runtime.forum.status(self.owner)
        if forum.get('configured'):return False
        row=self.bot_config();now=time.time();last=float(row.get('forum_prompted_at') or 0)
        if not force and now-last<86400:return False
        admin=int(row['admin_telegram_id'])
        try:
            if forum.get('rebind_required'):
                self.api.send(admin,'♻️ Disaster Recovery: اطلاعات انجمن قبلی حفظ شده است. Bot جدید را در همان انجمن Admin کن و سپس اتصال مجدد را بزن.',
                              {'inline_keyboard':[[{'text':'♻️ اتصال مجدد انجمن بکاپ','callback_data':'forumrebind'}]]})
                self.api.send(admin,'اگر انجمن قبلی در دسترس نیست، انجمن جدید را انتخاب کن.',self.runtime.forum.request_keyboard())
            else:
                self.api.send(admin,'برای فعال‌شدن مرکز گزارش DARK، یک گروه انجمن (Forum Supergroup) را انتخاب کن و دسترسی مدیریت Topicها را به ربات بده.',
                              self.runtime.forum.request_keyboard())
            with self.runtime.store.transaction() as db:
                db.execute('UPDATE telegram_bots SET forum_prompted_at=? WHERE owner=?',(now,self.owner))
            return True
        except Exception:
            return False

    def notify_admin(self,text:str,reply_markup:dict|None=None):
        try:self.api.send(int(self.bot_config()['admin_telegram_id']),text,reply_markup)
        except Exception:pass

    def handle(self,update:dict[str,Any]):
        if update.get('callback_query'):
            q=update['callback_query']
            try:self.handle_callback(q)
            finally:
                try:self.api.call('answerCallbackQuery',{'callback_query_id':q['id']})
                except Exception:pass
            return
        msg=update.get('message')
        if not msg:return
        chat=msg.get('chat') or {};sender=msg.get('from') or {}
        if chat.get('type')!='private':return
        chat_id=int(chat['id']);user_id=int(sender['id'])
        if msg.get('chat_shared'):
            if not self.is_admin(user_id):raise PolicyError('Only the configured Telegram admin may connect the report forum')
            result=self.runtime.forum.setup(self.api,self.owner,msg['chat_shared'],self.bot_id)
            self.runtime.manager.audit(self.actor(),self.owner,'telegram.forum_setup',str(result.get('chat_id') or ''),'topics='+str(len(result.get('topics') or [])))
            self.api.send(chat_id,'✅ انجمن DARK متصل شد و دسته‌بندی‌های گزارش ساخته شدند.',self.main_keyboard(True))
            return
        if msg.get('photo') or msg.get('document'):
            if self.handle_customer_media(chat_id,user_id,msg):return
            if self.handle_receipt(chat_id,user_id,msg):return
        text=str(msg.get('text') or '').strip()
        if not text:return
        low=text.lower()
        if low in ('/cancel','cancel','لغو'):
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.api.send(chat_id,'عملیات لغو شد.',self.main_keyboard(self.is_admin(user_id)));return
        session=self.sessions.get(user_id,'')
        if session.startswith('customer_'):
            self.handle_customer_text(chat_id,user_id,text,str(sender.get('username') or ''));return
        if session.startswith('admin_support_') and self.is_admin(user_id):
            self.handle_customer_text(chat_id,user_id,text,str(sender.get('username') or ''));return
        if session.startswith('pay_') and self.is_admin(user_id):
            self.handle_payment_setup_text(chat_id,user_id,text);return
        if session.startswith('store_') and self.is_admin(user_id):
            self.handle_store_text(chat_id,user_id,text);return
        if session.startswith('service_') and self.is_admin(user_id):
            self.handle_service_text(chat_id,user_id,text);return
        if session=='new_rep':
            self.create_representative_from_text(chat_id,user_id,text);return
        if low.startswith('/start') or low=='start':
            parts=text.split(None,1)
            self.runtime.customer.ensure_referral_profile(self.owner,user_id)
            if len(parts)==2 and parts[1].startswith('ref_'):
                self.runtime.customer.register_referral(self.owner,user_id,parts[1][4:])
            self.send_home(chat_id,user_id);return
        if low=='/shop' or text=='🛍 خرید اشتراک':self.shop(chat_id);return
        if text=='🔄 تمدید سرویس':self.customer_renew_services(chat_id,user_id);return
        if text=='💰 کیف پول + شارژ':self.customer_wallet_menu(chat_id,user_id);return
        if low=='/services' or text=='📦 سرویس‌های من':self.services(chat_id,user_id);return
        if text=='👥 زیرمجموعه‌گیری':self.customer_referral_menu(chat_id,user_id);return
        if text=='🎫 پشتیبانی':
            if self.is_admin(user_id):self.admin_support(chat_id)
            else:self.customer_support_menu(chat_id,user_id)
            return
        if low=='/status' or text=='📊 وضعیت ربات':self.status_menu(chat_id,user_id);return
        if text=='🏠 داشبورد' and self.is_admin(user_id):self.admin_dashboard(chat_id);return
        if text in ('👥 کاربران','👥 مدیریت کاربران') and self.is_admin(user_id):self.admin_clients(chat_id);return
        if text=='📦 سرویس‌ها' and self.is_admin(user_id):self.admin_services(chat_id);return
        if text=='🧾 سفارش‌ها' and self.is_admin(user_id):self.admin_orders(chat_id);return
        if text=='🛠 مدیریت فروشگاه' and self.is_admin(user_id):self.admin_store(chat_id);return
        if text=='💳 پرداخت دستی' and self.is_admin(user_id):self.admin_gateways(chat_id);return
        if text=='📊 گزارش‌ها' and self.is_admin(user_id):self.admin_reports(chat_id);return
        if text=='💾 بکاپ' and self.is_admin(user_id):self.admin_backup(chat_id);return
        if text=='⚙️ تنظیمات ربات' and self.is_admin(user_id):self.admin_settings(chat_id);return
        if text=='🤝 نمایندگان' and self.is_admin(user_id) and self.owner_role()=='owner':
            self.representatives(chat_id);return
        if text=='➕ ساخت نماینده' and self.is_admin(user_id) and self.owner_role()=='owner':
            self.sessions[user_id]='new_rep'
            self.api.send(chat_id,'اطلاعات نماینده را در یک خط بفرست:\nشناسه | رمز عبور | حجم GB | تعداد سرویس نامحدود\nمثال:\nseller1 | StrongPass88 | 500 | 20')
            return
        if low.startswith('/user ') and self.is_admin(user_id):
            self.admin_user_search(chat_id,text.split(None,1)[1].strip());return
        self.send_home(chat_id,user_id)

    def shop(self,chat_id:int):
        return self.customer_shop(chat_id)

    def product_by_rowid(self,row_id:int)->dict[str,Any]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("SELECT rowid AS row_id,* FROM commerce_products WHERE rowid=? AND owner=? AND active=1 AND visible=1",
                                              (row_id,self.owner)).fetchone()
        if not row:raise PolicyError('Product not found')
        return dict(row)

    def price_by_rowid(self,row_id:int)->dict[str,Any]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("SELECT rowid AS row_id,* FROM commerce_prices WHERE rowid=? AND owner=? AND active=1",
                                              (row_id,self.owner)).fetchone()
        if not row:raise PolicyError('Price not found')
        out=dict(row);out['inbound_ids']=json.loads(out['inbound_ids']);return out

    def gateway_by_rowid(self,row_id:int)->dict[str,Any]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("SELECT rowid AS row_id,* FROM commerce_gateways WHERE rowid=? AND owner=? AND enabled=1",
                                              (row_id,self.owner)).fetchone()
        if not row:raise PolicyError('Gateway not found')
        return dict(row)

    def send_delivery(self,chat_id:int,result:dict[str,Any]):
        text=f"✅ پرداخت تأیید شد و سرویس شما ساخته شد.\nشناسه: {result.get('client_id','')}"
        if result.get('activation_pending'):
            text+="\n⏱ مدت سرویس از اولین اتصال واقعی شروع می‌شود."
        self.api.send(chat_id,text)
        delivery=result.get('delivery') or {}
        if delivery.get('subscription_url'):
            self.api.send(chat_id,'🔗 Subscription\n'+str(delivery['subscription_url']))
        if delivery.get('main_config'):
            self.api.send(chat_id,'⚡ کانفیگ اصلی\n'+str(delivery['main_config']))
        portal=delivery.get('portal_url')
        if portal and portal!=delivery.get('subscription_url'):
            self.api.send(chat_id,'🌐 Portal\n'+str(portal))

    def handle_callback(self,q:dict[str,Any]):
        data=str(q.get('data') or '');sender=q.get('from') or {};user_id=int(sender['id'])
        msg=q.get('message') or {};chat_id=int((msg.get('chat') or {}).get('id') or user_id)
        if self.handle_customer_callback(data,chat_id,user_id,sender):return
        if data.startswith('p:'):
            p=self.product_by_rowid(int(data.split(':',1)[1]))
            prices=[x for x in self.runtime.commerce.product_rows(self.owner,public=True) if x['id']==p['id']][0]['prices']
            buttons=[[{'text':f"{x['label']} · {amount(x['price_minor'],x['currency'])}",'callback_data':'b:'+str(x['row_id'])}]
                     for x in prices if x['active']]
            self.api.send(chat_id,f"{p['name']}\n{p['description']}\nپلن را انتخاب کن:",{'inline_keyboard':buttons});return
        if data.startswith('b:'):
            price=self.price_by_rowid(int(data.split(':',1)[1]))
            with self.runtime.store.lock:
                p=self.runtime.store.db.execute("SELECT * FROM commerce_products WHERE owner=? AND id=?",
                                                (self.owner,price['product_id'])).fetchone()
            order=self.runtime.commerce.create_order(self.owner,user_id,str(sender.get('username') or ''),p['id'],price['id'])
            self.runtime.manager.audit(self.actor(),self.owner,'commerce.order_create',order['id'],
                                       f"telegram={user_id}; product={p['id']}; price={price['id']}")
            gateways=self.runtime.commerce.gateway_rows(self.owner,enabled_only=True)
            if not gateways:
                self.api.send(chat_id,f"سفارش {order['id']} ساخته شد ولی روش پرداخت فعال نیست. با پشتیبانی تماس بگیر.");return
            kb=[[{'text':g['label'],'callback_data':f"g:{g['row_id']}:{order['id']}"}] for g in gateways]
            self.api.send(chat_id,f"سفارش: {order['id']}\nمبلغ: {amount(order['amount_minor'],order['currency'])}\nروش پرداخت را انتخاب کن:",
                          {'inline_keyboard':kb});return
        if data.startswith('g:'):
            _,gw_row,order_id=data.split(':',2);gw=self.gateway_by_rowid(int(gw_row))
            result=self.runtime.commerce.start_payment(self.owner,order_id,gw['id'])
            self.runtime.manager.audit(self.actor(),self.owner,'commerce.payment_start',order_id,gw['id'])
            if result['mode']=='manual':
                card='\n'.join(x for x in [
                    '💳 کارت به کارت',
                    ('شماره کارت: '+result['card_number']) if result.get('card_number') else '',
                    ('به نام: '+result['card_holder']) if result.get('card_holder') else '',
                    ('بانک: '+result['bank_name']) if result.get('bank_name') else '',
                    result.get('instructions') or '',
                    'پس از پرداخت، تصویر یا فایل رسید را همین‌جا بفرست.'
                ] if x)
                self.api.send(chat_id,card)
            else:self.api.send(chat_id,'این درگاه برای آپدیت آینده رزرو شده است؛ فعلاً پرداخت دستی را انتخاب کن.')
            return
        if data=='stnew' and self.is_admin(user_id):
            self.start_product_create(chat_id,user_id);return
        if data=='stlist' and self.is_admin(user_id):
            self.admin_store_products(chat_id);return
        if data.startswith('stprod:') and self.is_admin(user_id):
            self.admin_product_detail(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('sttype:') and self.is_admin(user_id):
            self.store_choose_type(chat_id,user_id,data.split(':',1)[1]);return
        if data.startswith('stedit:') and self.is_admin(user_id):
            self.start_product_edit(chat_id,user_id,int(data.split(':',1)[1]));return
        if data.startswith('stpreview:') and self.is_admin(user_id):
            self.preview_product(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('stdelete:') and self.is_admin(user_id):
            rid=int(data.split(':',1)[1])
            self.api.send(chat_id,'محصول حذف/آرشیو شود؟ اگر سابقه سفارش داشته باشد فقط از فروش خارج می‌شود.',
                          {'inline_keyboard':[[{'text':'✅ تأیید','callback_data':'stdeletey:'+str(rid)},
                                              {'text':'❌ لغو','callback_data':'noop'}]]});return
        if data.startswith('stdeletey:') and self.is_admin(user_id):
            self.delete_or_archive_product(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('stclone:') and self.is_admin(user_id):
            self.clone_product(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('sttoggle:') and self.is_admin(user_id):
            _,rid,field=data.split(':',2);self.toggle_product(chat_id,int(rid),field);return
        if data.startswith('strenew:') and self.is_admin(user_id):
            self.toggle_product(chat_id,int(data.split(':',1)[1]),'renewal_enabled');return
        if data.startswith('staddvol:') and self.is_admin(user_id):
            self.toggle_product(chat_id,int(data.split(':',1)[1]),'add_volume_enabled');return
        if data.startswith('stpriceadd:') and self.is_admin(user_id):
            self.start_price_create(chat_id,user_id,int(data.split(':',1)[1]));return
        if data.startswith('stprice:') and self.is_admin(user_id):
            self.admin_price_detail(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('stptoggle:') and self.is_admin(user_id):
            self.toggle_price(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('stpdel:') and self.is_admin(user_id):
            rid=int(data.split(':',1)[1]);self.api.send(chat_id,'این Price Variant حذف شود؟',
                {'inline_keyboard':[[{'text':'✅ حذف','callback_data':'stpdely:'+str(rid)},
                                     {'text':'❌ لغو','callback_data':'noop'}]]});return
        if data.startswith('stpdely:') and self.is_admin(user_id):
            self.delete_price(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('stact:') and self.is_admin(user_id):
            self.price_choose_activation(chat_id,user_id,data.split(':',1)[1]);return
        if data.startswith('stdlv:') and self.is_admin(user_id):
            self.price_choose_delivery(chat_id,user_id,data.split(':',1)[1]);return
        if data.startswith('stinb:') and self.is_admin(user_id):
            self.price_toggle_inbound(chat_id,user_id,int(data.split(':',1)[1]));return
        if data=='stinbdone' and self.is_admin(user_id):
            self.price_inbounds_done(chat_id,user_id);return
        if data.startswith('stprimary:') and self.is_admin(user_id):
            self.price_choose_primary(chat_id,user_id,int(data.split(':',1)[1]));return
        if data.startswith('clrenew:') and self.is_admin(user_id):
            self.client_renew_menu(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('clr:') and self.is_admin(user_id):
            _,rid,days=data.split(':',2);self.renew_client(chat_id,int(rid),int(days));return
        if data.startswith('clrcustom:') and self.is_admin(user_id):
            self.start_service_input(chat_id,user_id,'renew',int(data.split(':',1)[1]),'تعداد روز تمدید را بفرست.');return
        if data.startswith('clvol:') and self.is_admin(user_id):
            self.start_service_input(chat_id,user_id,'volume',int(data.split(':',1)[1]),'چند GB به سرویس اضافه شود؟');return
        if data.startswith('clip:') and self.is_admin(user_id):
            self.start_service_input(chat_id,user_id,'ip',int(data.split(':',1)[1]),'IP Limit جدید را بفرست. صفر یعنی نامحدود.');return
        if data.startswith('clhw:') and self.is_admin(user_id):
            self.start_service_input(chat_id,user_id,'hwid',int(data.split(':',1)[1]),'HWID Limit جدید را بفرست. صفر یعنی نامحدود.');return
        if data.startswith('clinb:') and self.is_admin(user_id):
            self.start_client_inbounds(chat_id,user_id,int(data.split(':',1)[1]));return
        if data.startswith('clinbt:') and self.is_admin(user_id):
            self.toggle_client_inbound(chat_id,user_id,int(data.split(':',1)[1]));return
        if data=='clinbdone' and self.is_admin(user_id):
            self.finish_client_inbounds(chat_id,user_id);return
        if data=='forumrebind' and self.is_admin(user_id):
            try:
                result=self.runtime.rebind_forum(self.owner)
                self.runtime.manager.audit(self.actor(),self.owner,'telegram.forum_rebind',str(result.get('chat_id') or ''),
                                           'topics='+str(len(result.get('topics') or [])))
                self.api.send(chat_id,'✅ انجمن بکاپ با Bot جدید دوباره متصل شد.',self.main_keyboard(True))
            except Exception as ex:
                self.api.send(chat_id,'اتصال مجدد انجام نشد: '+str(ex)[:700])
            return
        if data=='paycfg' and self.is_admin(user_id):
            self.start_payment_setup(chat_id,user_id);return
        if data=='paytoggle' and self.is_admin(user_id):
            self.toggle_manual_payment(chat_id,user_id);return
        if data.startswith('cllink:') and self.is_admin(user_id):
            self.client_delivery(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('ord:') and self.is_admin(user_id):
            self.order_detail(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('cl:') and self.is_admin(user_id):
            self.client_detail(chat_id,int(data.split(':',1)[1]));return
        if data.startswith('clon:') and self.is_admin(user_id):
            self.client_action(chat_id,int(data.split(':',1)[1]),'enable');return
        if data.startswith('cloff:') and self.is_admin(user_id):
            self.client_action(chat_id,int(data.split(':',1)[1]),'disable');return
        if data.startswith('clreset:') and self.is_admin(user_id):
            rid=int(data.split(':',1)[1])
            self.api.send(chat_id,'ریست ترافیک این کاربر انجام شود؟ این عملیات قابل بازگشت نیست.',
                          {'inline_keyboard':[[{'text':'✅ تأیید ریست','callback_data':'clresetok:'+str(rid)},
                                              {'text':'❌ لغو','callback_data':'noop'}]]});return
        if data.startswith('clresetok:') and self.is_admin(user_id):
            self.client_action(chat_id,int(data.split(':',1)[1]),'reset');return
        if data.startswith('cldel:') and self.is_admin(user_id):
            rid=int(data.split(':',1)[1])
            self.api.send(chat_id,'این سرویس کامل حذف شود؟ این عملیات قابل بازگشت نیست.',
                          {'inline_keyboard':[[{'text':'✅ حذف قطعی','callback_data':'cldely:'+str(rid)},
                                              {'text':'❌ لغو','callback_data':'noop'}]]});return
        if data.startswith('cldely:') and self.is_admin(user_id):
            self.client_action(chat_id,int(data.split(':',1)[1]),'delete');return
        if data.startswith('payok:') and self.is_admin(user_id):
            row_id=int(data.split(':',1)[1]);result=self.runtime.commerce.approve_payment(self.owner,row_id,self.runtime.manager)
            order=self.runtime.commerce.order(result['id'],self.owner)
            self.runtime.manager.audit(self.actor(),self.owner,'commerce.payment_approve',result['id'],f"telegram_admin={user_id}")
            if result.get('provisioned'):
                self.send_delivery(int(order['buyer_telegram_id']),result)
                note=f"✅ پرداخت {result['id']} تأیید شد؛ سرویس {result['client_id']} ساخته شد."
                if result.get('activation_pending'):note+=' منتظر اولین اتصال است.'
                if not self.runtime.forum.report(self.api,self.owner,'services',note):self.notify_admin(note)
            else:
                note='پرداخت تأیید شد ولی ساخت سرویس خطا داد: '+str(result.get('fulfillment_error') or 'unknown')
                self.notify_admin(note);self.runtime.forum.report(self.api,self.owner,'errors',note)
                self.api.send(int(order['buyer_telegram_id']),'پرداخت شما تأیید شد و سفارش برای تکمیل سرویس در صف مدیریت قرار گرفت.')
            return
        if data.startswith('payno:') and self.is_admin(user_id):
            row_id=int(data.split(':',1)[1]);result=self.runtime.commerce.reject_payment(self.owner,row_id)
            self.runtime.manager.audit(self.actor(),self.owner,'commerce.payment_reject',result['order_id'],f"telegram_admin={user_id}")
            self.notify_admin('❌ رسید سفارش '+result['order_id']+' رد شد.')
            self.api.send(int(result['buyer_telegram_id']),'رسید پرداخت تأیید نشد. می‌توانی دوباره پرداخت و رسید جدید ارسال کنی.')
            return
        if data=='noop':return

    def handle_receipt(self,chat_id:int,user_id:int,msg:dict[str,Any])->bool:
        order=self.runtime.commerce.latest_waiting_order(self.owner,user_id)
        if not order:return False
        if msg.get('photo'):
            file_id=str(msg['photo'][-1]['file_id']);kind='photo';method='sendPhoto'
        else:
            file_id=str(msg['document']['file_id']);kind='document';method='sendDocument'
        result=self.runtime.commerce.record_receipt(self.owner,user_id,f'telegram:{kind}:{file_id}')
        pay=result['payment'];row_id=int(pay['row_id'])
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.payment_receipt',order['id'],f"telegram={user_id}")
        self.api.send(chat_id,'✅ رسید ثبت شد و برای مدیر ارسال شد. نتیجه تأیید از همین ربات اعلام می‌شود.')
        caption=f"رسید جدید\nسفارش: {order['id']}\nکاربر: {user_id}\nمبلغ: {amount(order['amount_minor'],order['currency'])}"
        markup={'inline_keyboard':[[
            {'text':'✅ تأیید و ساخت سرویس','callback_data':'payok:'+str(row_id)},
            {'text':'❌ رد رسید','callback_data':'payno:'+str(row_id)}
        ]]}
        routed=False
        try:routed=self.runtime.forum.report_media(self.api,self.owner,'payments',method,kind,file_id,caption,markup)
        except Exception:routed=False
        if not routed:
            admin=int(self.bot_config()['admin_telegram_id'])
            try:self.api.call(method,{'chat_id':admin,kind:file_id,'caption':caption,'reply_markup':markup})
            except Exception:self.api.send(admin,caption,markup)
        return True

    def services(self,chat_id:int,user_id:int):
        return self.customer_services(chat_id,user_id)

    @staticmethod
    def bytes(v:int)->str:
        x=float(v)
        for unit in ('B','KiB','MiB','GiB','TiB'):
            if x<1024 or unit=='TiB':return f"{x:.1f} {unit}"
            x/=1024
        return str(v)

    def status_menu(self,chat_id:int,user_id:int):
        if not self.is_admin(user_id):
            self.api.send(chat_id,'ربات فعال است و به DARK XRAY متصل است.');return
        products=len(self.runtime.commerce.product_rows(self.owner))
        clients=len(self.runtime.manager.list(self.actor()))
        with self.runtime.store.lock:
            orders=self.runtime.store.db.execute("SELECT COUNT(*) FROM commerce_orders WHERE owner=?",(self.owner,)).fetchone()[0]
            pending=self.runtime.store.db.execute("SELECT COUNT(*) FROM commerce_orders WHERE owner=? AND status IN ('awaiting_payment','payment_review','paid')",(self.owner,)).fetchone()[0]
        forum=self.runtime.forum.status(self.owner);gateway=self.manual_gateway()
        forum_state='متصل ✅' if forum.get('configured') else 'متصل نیست ⛔'
        pay_state='فعال ✅' if gateway and gateway.get('enabled') else ('غیرفعال ⛔' if gateway else 'تنظیم نشده')
        self.api.send(chat_id,f"📊 وضعیت DARK BOT\nForum: {forum_state}\nپرداخت دستی: {pay_state}\nکاربران: {clients}\nمحصولات: {products}\nسفارش‌ها: {orders}\nنیازمند پیگیری: {pending}")

    def admin_dashboard(self,chat_id:int):
        rows=self.runtime.manager.list(self.actor())
        active=sum(1 for r in rows if not r.get('block_reasons'))
        disabled=max(0,len(rows)-active)
        products=len(self.runtime.commerce.product_rows(self.owner))
        forum=self.runtime.forum.status(self.owner);gateway=self.manual_gateway()
        with self.runtime.store.lock:
            orders=int(self.runtime.store.db.execute("SELECT COUNT(*) FROM commerce_orders WHERE owner=?",(self.owner,)).fetchone()[0])
            pending=int(self.runtime.store.db.execute("""SELECT COUNT(*) FROM commerce_orders WHERE owner=?
              AND status IN ('pending','awaiting_payment','payment_review','paid')""",(self.owner,)).fetchone()[0])
            nodes=int(self.runtime.store.db.execute("SELECT COUNT(*) FROM remote_nodes WHERE enabled=1").fetchone()[0])
            online=int(self.runtime.store.db.execute("SELECT COUNT(*) FROM remote_nodes WHERE enabled=1 AND last_error='' AND last_seen>?",
                                                     (time.time()-180,)).fetchone()[0])
            reps=0
            if self.owner_role()=='owner':
                reps=int(self.runtime.store.db.execute("SELECT COUNT(*) FROM api_admins WHERE role='reseller' AND disabled=0").fetchone()[0])
        forum_state='متصل ✅' if forum.get('configured') else ('نیازمند Rebind ♻️' if forum.get('rebind_required') else 'متصل نیست ⛔')
        pay_state='فعال ✅' if gateway and gateway.get('enabled') else ('غیرفعال ⛔' if gateway else 'تنظیم نشده')
        text=(f"🏠 DARK BOT ADMIN V3\n"
              f"👥 کاربران: {len(rows)} · فعال {active} · محدود/خاموش {disabled}\n"
              f"🛍 محصولات: {products}\n🧾 سفارش‌ها: {orders} · پیگیری {pending}\n"
              f"🖥 نودها: {online}/{nodes} آنلاین\n"
              f"💳 پرداخت: {pay_state}\n📊 Forum: {forum_state}")
        if self.owner_role()=='owner':text+=f"\n🤝 نمایندگان فعال: {reps}"
        self.api.send(chat_id,text)

    def admin_services(self,chat_id:int):
        rows=self.runtime.manager.list(self.actor())
        now_ms=int(time.time()*1000);active=expired=disabled=0
        lines=['📦 سرویس‌ها']
        for r in rows:
            c=r.get('client') or {};expiry=int(c.get('expiryTime') or 0)
            if c.get('enable') is False or r.get('block_reasons'):disabled+=1
            elif expiry and expiry<=now_ms:expired+=1
            else:active+=1
        with self.runtime.store.lock:
            waiting=int(self.runtime.store.db.execute("""SELECT COUNT(*) FROM commerce_orders
              WHERE owner=? AND status='provisioned_waiting_activation'""",(self.owner,)).fetchone()[0])
        lines += [f"✅ فعال: {active}",f"⛔ محدود/غیرفعال: {disabled}",f"⌛ منقضی: {expired}",
                  f"🔌 منتظر اولین اتصال: {waiting}",'','برای جستجوی سرویس: /user USERNAME']
        self.api.send(chat_id,'\n'.join(lines))

    def inbound_catalog(self)->list[dict[str,Any]]:
        profile=self.runtime.manager.profile(self.owner)
        allowed={int(x) for x in (profile.get('allowed') or [])}
        with self.runtime.store.lock:
            rows=list(self.runtime.store.db.execute("SELECT id,body FROM core_inbounds ORDER BY id"))
        out=[]
        for row in rows:
            inbound_id=int(row['id'])
            if self.owner_role()!='owner' and inbound_id not in allowed:continue
            try:body=json.loads(row['body'])
            except Exception:body={}
            out.append({'id':inbound_id,'name':str(body.get('remark') or body.get('tag') or ('Inbound '+str(inbound_id))),
                        'port':int(body.get('port') or 0),'protocol':str(body.get('protocol') or '')})
        return out

    def admin_store(self,chat_id:int):
        products=self.runtime.commerce.product_rows(self.owner)
        active=sum(1 for p in products if p['active'] and p['visible'])
        variants=sum(len(p.get('prices') or []) for p in products)
        cats=sorted({str(p.get('category') or 'General') for p in products})
        self.api.send(chat_id,
            f"🛠 STORE MANAGER V3\nمحصولات: {len(products)} · قابل فروش: {active}\n"
            f"Price Variant: {variants}\nدسته‌ها: {', '.join(cats) if cats else '—'}",
            {'inline_keyboard':[
                [{'text':'➕ ساخت محصول','callback_data':'stnew'},
                 {'text':'📦 محصولات','callback_data':'stlist'}]
            ]})

    def admin_store_products(self,chat_id:int):
        with self.runtime.store.lock:
            rows=[dict(r) for r in self.runtime.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_products WHERE owner=? ORDER BY updated_at DESC,id",(self.owner,))]
        if not rows:
            self.api.send(chat_id,'هنوز محصولی ساخته نشده است.',
                          {'inline_keyboard':[[{'text':'➕ ساخت اولین محصول','callback_data':'stnew'}]]});return
        kb=[]
        for r in rows[:40]:
            state='🟢' if r['active'] and r['visible'] else ('🟡' if r['active'] else '🔴')
            kb.append([{'text':f"{state} {r['name']} · {r.get('category') or 'General'}"[:62],
                        'callback_data':'stprod:'+str(r['row_id'])}])
        self.api.send(chat_id,'📦 محصولات فروشگاه · یک محصول را باز کن:',{'inline_keyboard':kb})

    def admin_product_row(self,row_id:int)->dict[str,Any]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_products WHERE rowid=? AND owner=?",(row_id,self.owner)).fetchone()
        if not row:raise PolicyError('Product not found in this bot scope')
        return dict(row)

    def admin_price_row(self,row_id:int)->dict[str,Any]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_prices WHERE rowid=? AND owner=?",(row_id,self.owner)).fetchone()
        if not row:raise PolicyError('Price variant not found in this bot scope')
        out=dict(row)
        try:out['inbound_ids']=json.loads(out.get('inbound_ids') or '[]')
        except Exception:out['inbound_ids']=[]
        return out

    def admin_product_detail(self,chat_id:int,row_id:int):
        p=self.admin_product_row(row_id)
        with self.runtime.store.lock:
            prices=[dict(r) for r in self.runtime.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_prices WHERE owner=? AND product_id=? ORDER BY price_minor,id",
                (self.owner,p['id']))]
            sold=int(self.runtime.store.db.execute(
                "SELECT COUNT(*) FROM commerce_orders WHERE owner=? AND product_id=?",(self.owner,p['id'])).fetchone()[0])
        text=(f"🛠 {p['name']}\nID: {p['id']}\nدسته: {p.get('category') or 'General'} · نوع: {p['kind']}\n"
              f"فروش: {sold} · سقف هر کاربر: {p.get('sale_limit_per_user') or '∞'}\n"
              f"Active: {'✅' if p['active'] else '⛔'} · Visible: {'✅' if p['visible'] else '⛔'}\n"
              f"Renew: {'✅' if p.get('renewal_enabled') else '⛔'} · Add Volume: {'✅' if p.get('add_volume_enabled') else '⛔'}\n"
              f"{p.get('description') or 'بدون توضیح'}")
        kb=[
            [{'text':'✏️ ویرایش','callback_data':'stedit:'+str(row_id)},
             {'text':'📑 Clone','callback_data':'stclone:'+str(row_id)}],
            [{'text':'👁 Preview مشتری','callback_data':'stpreview:'+str(row_id)},
             {'text':'🗑 حذف/آرشیو','callback_data':'stdelete:'+str(row_id)}],
            [{'text':('⛔ خاموش' if p['active'] else '✅ روشن'),'callback_data':f"sttoggle:{row_id}:active"},
             {'text':('🙈 مخفی' if p['visible'] else '👁 نمایش'),'callback_data':f"sttoggle:{row_id}:visible"}],
            [{'text':('🔁 تمدید ON' if p.get('renewal_enabled') else '🔁 تمدید OFF'),'callback_data':'strenew:'+str(row_id)},
             {'text':('+GB ON' if p.get('add_volume_enabled') else '+GB OFF'),'callback_data':'staddvol:'+str(row_id)}],
            [{'text':'➕ Price Variant','callback_data':'stpriceadd:'+str(row_id)}]
        ]
        for price in prices[:20]:
            kb.append([{'text':f"{'🟢' if price['active'] else '🔴'} {price['label']} · {amount(price['price_minor'],price['currency'])}"[:62],
                        'callback_data':'stprice:'+str(price['row_id'])}])
        self.api.send(chat_id,text,{'inline_keyboard':kb})

    def preview_product(self,chat_id:int,row_id:int):
        p=self.admin_product_row(row_id)
        with self.runtime.store.lock:
            prices=[dict(r) for r in self.runtime.store.db.execute("""SELECT * FROM commerce_prices
              WHERE owner=? AND product_id=? AND active=1 ORDER BY price_minor,id""",(self.owner,p['id']))]
        lines=[f"👁 PREVIEW مشتری\n{p['name']}",p.get('description') or '']
        for price in prices:
            volume='نامحدود' if not int(price['volume_bytes'] or 0) else self.bytes(int(price['volume_bytes']))
            lines.append(f"• {price['label']} · {volume} · {price['duration_days']} روز · {amount(price['price_minor'],price['currency'])}")
        if not prices:lines.append('هیچ Price فعال ندارد.')
        self.api.send(chat_id,'\n'.join(x for x in lines if x))

    def delete_or_archive_product(self,chat_id:int,row_id:int):
        p=self.admin_product_row(row_id)
        with self.runtime.store.transaction() as db:
            orders=int(db.execute("SELECT COUNT(*) FROM commerce_orders WHERE owner=? AND product_id=?",
                                  (self.owner,p['id'])).fetchone()[0])
            if orders:
                db.execute("UPDATE commerce_products SET active=0,visible=0,updated_at=? WHERE rowid=? AND owner=?",
                           (time.time(),row_id,self.owner))
                db.execute("UPDATE commerce_prices SET active=0,updated_at=? WHERE owner=? AND product_id=?",
                           (time.time(),self.owner,p['id']))
                mode='آرشیو شد؛ تاریخچه سفارش حفظ شد'
            else:
                db.execute("DELETE FROM commerce_prices WHERE owner=? AND product_id=?",(self.owner,p['id']))
                db.execute("DELETE FROM commerce_products WHERE rowid=? AND owner=?",(row_id,self.owner))
                mode='کامل حذف شد'
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.product_bot_delete',p['id'],mode)
        self.api.send(chat_id,'✅ '+mode)
        self.admin_store_products(chat_id)

    def start_product_create(self,chat_id:int,user_id:int):
        self.sessions[user_id]='store_product_name';self.session_data[user_id]={}
        self.api.send(chat_id,'➕ نام محصول را بفرست.\nبرای لغو: /cancel')

    def start_product_edit(self,chat_id:int,user_id:int,row_id:int):
        p=self.admin_product_row(row_id)
        self.sessions[user_id]='store_edit_name';self.session_data[user_id]={'row_id':row_id}
        self.api.send(chat_id,f"نام جدید را بفرست.\nفعلی: {p['name']}")

    def handle_store_text(self,chat_id:int,user_id:int,text:str):
        state=self.sessions.get(user_id,'');data=self.session_data.setdefault(user_id,{})
        value=text.strip()
        if state in ('store_product_name','store_edit_name'):
            if not 1<=len(value)<=128:self.api.send(chat_id,'نام محصول نامعتبر است.');return
            data['name']=value
            self.sessions[user_id]='store_product_category' if state=='store_product_name' else 'store_edit_category'
            self.api.send(chat_id,'دسته‌بندی را بفرست؛ مثال: Gaming / VIP / Economy');return
        if state in ('store_product_category','store_edit_category'):
            if not 1<=len(value)<=64:self.api.send(chat_id,'دسته‌بندی نامعتبر است.');return
            data['category']=value
            if state=='store_product_category':
                self.sessions[user_id]='store_product_type_wait'
                self.api.send(chat_id,'نوع محصول را انتخاب کن:',{'inline_keyboard':[[
                    {'text':'📦 حجمی','callback_data':'sttype:volume'},
                    {'text':'♾ نامحدود','callback_data':'sttype:unlimited'}],[
                    {'text':'🌍 Multi-location','callback_data':'sttype:multi_location'},
                    {'text':'🎮 Gaming','callback_data':'sttype:gaming'}]]});return
            self.sessions[user_id]='store_edit_description'
            self.api.send(chat_id,'توضیح جدید را بفرست؛ برای خالی گذاشتن - بفرست.');return
        if state in ('store_product_description','store_edit_description'):
            data['description']='' if value=='-' else value[:2000]
            self.sessions[user_id]='store_product_limit' if state=='store_product_description' else 'store_edit_limit'
            self.api.send(chat_id,'سقف خرید هر Telegram ID را بفرست. صفر = نامحدود');return
        if state in ('store_product_limit','store_edit_limit'):
            try:limit=int(value)
            except ValueError:self.api.send(chat_id,'یک عدد صحیح بین 0 تا 100000 بفرست.');return
            if not 0<=limit<=100000:self.api.send(chat_id,'عدد خارج از محدوده است.');return
            data['sale_limit_per_user']=limit;now=time.time()
            if state=='store_product_limit':
                product_id='p_'+secrets.token_hex(5)
                with self.runtime.store.transaction() as db:
                    db.execute("""INSERT INTO commerce_products(id,owner,name,description,category,kind,sale_limit_per_user,
                      renewal_enabled,add_volume_enabled,active,visible,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (product_id,self.owner,data['name'],data.get('description',''),data['category'],data['kind'],limit,
                       1,1,1,1,now,now))
                    row_id=int(db.execute("SELECT rowid FROM commerce_products WHERE owner=? AND id=?",(self.owner,product_id)).fetchone()[0])
                self.runtime.manager.audit(self.actor(),self.owner,'commerce.product_bot_create',product_id,data['category'])
            else:
                row_id=int(data['row_id']);p=self.admin_product_row(row_id)
                with self.runtime.store.transaction() as db:
                    db.execute("""UPDATE commerce_products SET name=?,category=?,description=?,sale_limit_per_user=?,updated_at=?
                      WHERE rowid=? AND owner=?""",(data['name'],data['category'],data.get('description',''),limit,now,row_id,self.owner))
                self.runtime.manager.audit(self.actor(),self.owner,'commerce.product_bot_edit',p['id'],'name/category/description/limit')
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.api.send(chat_id,'✅ محصول ذخیره شد.')
            self.admin_product_detail(chat_id,row_id);return
        if state.startswith('store_price_'):
            self.handle_price_text(chat_id,user_id,text);return
        raise PolicyError('Unknown store wizard state')

    def store_choose_type(self,chat_id:int,user_id:int,kind:str):
        if self.sessions.get(user_id)!='store_product_type_wait':raise PolicyError('Product wizard is not waiting for type')
        if kind not in ('volume','unlimited','multi_location','gaming'):raise PolicyError('Invalid product type')
        self.session_data[user_id]['kind']=kind;self.sessions[user_id]='store_product_description'
        self.api.send(chat_id,'توضیح محصول را بفرست؛ برای خالی گذاشتن - بفرست.')

    def toggle_product(self,chat_id:int,row_id:int,field:str):
        if field not in ('active','visible','renewal_enabled','add_volume_enabled'):raise PolicyError('Invalid product toggle')
        p=self.admin_product_row(row_id);value=0 if bool(p.get(field)) else 1
        with self.runtime.store.transaction() as db:
            db.execute(f"UPDATE commerce_products SET {field}=?,updated_at=? WHERE rowid=? AND owner=?",
                       (value,time.time(),row_id,self.owner))
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.product_bot_toggle',p['id'],field+'='+str(value))
        self.admin_product_detail(chat_id,row_id)

    def clone_product(self,chat_id:int,row_id:int):
        p=self.admin_product_row(row_id);new_id='p_'+secrets.token_hex(5);now=time.time()
        with self.runtime.store.transaction() as db:
            db.execute("""INSERT INTO commerce_products(id,owner,name,description,category,kind,sale_limit_per_user,
              renewal_enabled,add_volume_enabled,active,visible,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (new_id,self.owner,p['name']+' Copy',p.get('description',''),p.get('category','General'),p['kind'],
               int(p.get('sale_limit_per_user') or 0),int(p.get('renewal_enabled') or 0),int(p.get('add_volume_enabled') or 0),
               0,0,now,now))
            prices=[dict(r) for r in db.execute("SELECT * FROM commerce_prices WHERE owner=? AND product_id=?",(self.owner,p['id']))]
            for price in prices:
                new_price='v_'+secrets.token_hex(5)
                db.execute("""INSERT INTO commerce_prices(id,owner,product_id,label,price_minor,currency,duration_days,
                  volume_bytes,unlimited_units,device_limit,ip_limit,hwid_limit,inbound_ids,activation_mode,delivery_mode,
                  primary_inbound_id,show_qr,show_portal,active,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (new_price,self.owner,new_id,price['label'],price['price_minor'],price['currency'],price['duration_days'],
                   price['volume_bytes'],price['unlimited_units'],price['device_limit'],price['ip_limit'],price['hwid_limit'],
                   price['inbound_ids'],price['activation_mode'],price['delivery_mode'],price['primary_inbound_id'],
                   price['show_qr'],price['show_portal'],0,now,now))
            new_row=int(db.execute("SELECT rowid FROM commerce_products WHERE owner=? AND id=?",(self.owner,new_id)).fetchone()[0])
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.product_bot_clone',new_id,'from='+p['id'])
        self.api.send(chat_id,'✅ Clone ساخته شد و برای جلوگیری از فروش اشتباه، خاموش و مخفی است.')
        self.admin_product_detail(chat_id,new_row)

    def start_price_create(self,chat_id:int,user_id:int,product_row:int):
        p=self.admin_product_row(product_row)
        self.sessions[user_id]='store_price_label'
        self.session_data[user_id]={'product_row':product_row,'product_id':p['id'],'product_kind':p['kind']}
        self.api.send(chat_id,'عنوان Price Variant را بفرست؛ مثال: 50GB / 30 Days')

    def handle_price_text(self,chat_id:int,user_id:int,text:str):
        state=self.sessions.get(user_id,'');data=self.session_data.setdefault(user_id,{})
        value=text.strip()
        if state=='store_price_label':
            if not 1<=len(value)<=128:self.api.send(chat_id,'عنوان نامعتبر است.');return
            data['label']=value;self.sessions[user_id]='store_price_amount'
            self.api.send(chat_id,'قیمت را به تومان بفرست؛ فقط عدد.');return
        if state=='store_price_amount':
            try:n=int(value.replace(',',''))
            except ValueError:self.api.send(chat_id,'قیمت باید عدد صحیح باشد.');return
            if not 0<=n<=10**12:self.api.send(chat_id,'قیمت خارج از محدوده است.');return
            data['price_minor']=n;self.sessions[user_id]='store_price_duration'
            self.api.send(chat_id,'مدت سرویس چند روز باشد؟');return
        if state=='store_price_duration':
            try:n=int(value)
            except ValueError:self.api.send(chat_id,'مدت باید عدد صحیح باشد.');return
            if not 1<=n<=3650:self.api.send(chat_id,'مدت باید بین 1 تا 3650 روز باشد.');return
            data['duration_days']=n
            if data['product_kind']=='unlimited':
                data['volume_bytes']=0;data['unlimited_units']=1;self.sessions[user_id]='store_price_ip'
                self.api.send(chat_id,'IP Limit را بفرست. صفر = نامحدود');return
            self.sessions[user_id]='store_price_volume'
            self.api.send(chat_id,'حجم سرویس را به GB بفرست.');return
        if state=='store_price_volume':
            try:n=float(value)
            except ValueError:self.api.send(chat_id,'حجم باید عدد باشد.');return
            if not 0<n<=1000000:self.api.send(chat_id,'حجم خارج از محدوده است.');return
            data['volume_bytes']=int(n*1024**3);data['unlimited_units']=0;self.sessions[user_id]='store_price_ip'
            self.api.send(chat_id,'IP Limit را بفرست. صفر = نامحدود');return
        if state=='store_price_ip':
            try:n=int(value)
            except ValueError:self.api.send(chat_id,'IP Limit باید عدد صحیح باشد.');return
            if not 0<=n<=1000:self.api.send(chat_id,'IP Limit خارج از محدوده است.');return
            data['ip_limit']=n;self.sessions[user_id]='store_price_hwid'
            self.api.send(chat_id,'HWID Limit را بفرست. صفر = نامحدود');return
        if state=='store_price_hwid':
            try:n=int(value)
            except ValueError:self.api.send(chat_id,'HWID باید عدد صحیح باشد.');return
            if not 0<=n<=1000:self.api.send(chat_id,'HWID خارج از محدوده است.');return
            data['hwid_limit']=n;self.sessions[user_id]='store_price_activation_wait'
            self.api.send(chat_id,'فعال‌سازی را انتخاب کن:',{'inline_keyboard':[[
                {'text':'⚡ فوری','callback_data':'stact:immediate'},
                {'text':'🔌 اولین اتصال','callback_data':'stact:first_connection'}]]});return
        raise PolicyError('Price wizard is not waiting for text')

    def price_choose_activation(self,chat_id:int,user_id:int,mode:str):
        if self.sessions.get(user_id)!='store_price_activation_wait':raise PolicyError('Price wizard is not waiting for activation')
        if mode not in ('immediate','first_connection'):raise PolicyError('Invalid activation mode')
        self.session_data[user_id]['activation_mode']=mode;self.sessions[user_id]='store_price_delivery_wait'
        self.api.send(chat_id,'نحوه تحویل را انتخاب کن:',{'inline_keyboard':[
            [{'text':'🔗 Subscription','callback_data':'stdlv:subscription'},
             {'text':'⚡ Main Config','callback_data':'stdlv:config'}],
            [{'text':'🔗+⚡ هر دو','callback_data':'stdlv:both'},
             {'text':'🌐 Portal','callback_data':'stdlv:portal'}]
        ]})

    def price_choose_delivery(self,chat_id:int,user_id:int,mode:str):
        if self.sessions.get(user_id)!='store_price_delivery_wait':raise PolicyError('Price wizard is not waiting for delivery')
        if mode not in ('subscription','config','both','portal'):raise PolicyError('Invalid delivery mode')
        self.session_data[user_id]['delivery_mode']=mode
        self.session_data[user_id]['selected_inbounds']=[]
        self.sessions[user_id]='store_price_inbounds'
        self.show_price_inbounds(chat_id,user_id)

    def show_price_inbounds(self,chat_id:int,user_id:int):
        catalog=self.inbound_catalog();selected=set(self.session_data.get(user_id,{}).get('selected_inbounds') or [])
        if not catalog:self.api.send(chat_id,'هیچ Inbound مجازی برای این پنل وجود ندارد.');return
        kb=[]
        for x in catalog:
            mark='✅' if x['id'] in selected else '⬜'
            kb.append([{'text':f"{mark} {x['id']} · {x['name']} · :{x['port']}"[:62],
                        'callback_data':'stinb:'+str(x['id'])}])
        kb.append([{'text':'✅ پایان انتخاب Inbound','callback_data':'stinbdone'}])
        self.api.send(chat_id,'Inboundهای این Price Variant را انتخاب کن:',{'inline_keyboard':kb})

    def price_toggle_inbound(self,chat_id:int,user_id:int,inbound_id:int):
        if self.sessions.get(user_id)!='store_price_inbounds':raise PolicyError('Price wizard is not selecting inbounds')
        allowed={x['id'] for x in self.inbound_catalog()}
        if inbound_id not in allowed:raise PolicyError('Inbound is outside this panel scope')
        data=self.session_data[user_id];selected=set(data.get('selected_inbounds') or [])
        if inbound_id in selected:selected.remove(inbound_id)
        else:selected.add(inbound_id)
        data['selected_inbounds']=sorted(selected);self.show_price_inbounds(chat_id,user_id)

    def price_inbounds_done(self,chat_id:int,user_id:int):
        if self.sessions.get(user_id)!='store_price_inbounds':raise PolicyError('Price wizard is not selecting inbounds')
        data=self.session_data[user_id];selected=list(data.get('selected_inbounds') or [])
        if not selected:self.api.send(chat_id,'حداقل یک Inbound انتخاب کن.');return
        data['inbound_ids']=selected
        if data.get('delivery_mode') in ('config','both'):
            self.sessions[user_id]='store_price_primary_wait'
            catalog={x['id']:x for x in self.inbound_catalog()}
            kb=[[{'text':f"{i} · {catalog.get(i,{}).get('name','Inbound')}"[:62],
                 'callback_data':'stprimary:'+str(i)}] for i in selected]
            self.api.send(chat_id,'Main Config از کدام Inbound تحویل شود؟',{'inline_keyboard':kb});return
        data['primary_inbound_id']=selected[0]
        self.save_price_wizard(chat_id,user_id)

    def price_choose_primary(self,chat_id:int,user_id:int,inbound_id:int):
        if self.sessions.get(user_id)!='store_price_primary_wait':raise PolicyError('Price wizard is not waiting for primary inbound')
        if inbound_id not in (self.session_data[user_id].get('inbound_ids') or []):raise PolicyError('Primary inbound was not selected')
        self.session_data[user_id]['primary_inbound_id']=inbound_id;self.save_price_wizard(chat_id,user_id)

    def save_price_wizard(self,chat_id:int,user_id:int):
        data=self.session_data[user_id];price_id='v_'+secrets.token_hex(5);now=time.time()
        with self.runtime.store.transaction() as db:
            db.execute("""INSERT INTO commerce_prices(id,owner,product_id,label,price_minor,currency,duration_days,
              volume_bytes,unlimited_units,device_limit,ip_limit,hwid_limit,inbound_ids,activation_mode,delivery_mode,
              primary_inbound_id,show_qr,show_portal,active,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (price_id,self.owner,data['product_id'],data['label'],int(data['price_minor']),'IRT',int(data['duration_days']),
               int(data['volume_bytes']),int(data['unlimited_units']),int(data['ip_limit']),int(data['ip_limit']),
               int(data['hwid_limit']),json.dumps(data['inbound_ids']),data['activation_mode'],data['delivery_mode'],
               int(data['primary_inbound_id']),1,1,1,now,now))
            row_id=int(db.execute("SELECT rowid FROM commerce_prices WHERE owner=? AND id=?",(self.owner,price_id)).fetchone()[0])
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.price_bot_create',price_id,
                                   f"product={data['product_id']}; amount={data['price_minor']} IRT")
        product_row=int(data['product_row']);self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
        self.api.send(chat_id,'✅ Price Variant ساخته شد.')
        self.admin_price_detail(chat_id,row_id)
        self.admin_product_detail(chat_id,product_row)

    def admin_price_detail(self,chat_id:int,row_id:int):
        p=self.admin_price_row(row_id);ids=p.get('inbound_ids') or []
        text=(f"💵 {p['label']}\nID: {p['id']}\nقیمت: {amount(p['price_minor'],p['currency'])}\n"
              f"مدت: {p['duration_days']} روز · حجم: {self.bytes(int(p['volume_bytes'])) if p['volume_bytes'] else 'نامحدود'}\n"
              f"IP/HWID: {p['ip_limit']}/{p['hwid_limit']}\nActivation: {p['activation_mode']}\n"
              f"Delivery: {p['delivery_mode']} · Primary: {p['primary_inbound_id']}\n"
              f"Inbounds: {', '.join(map(str,ids))}\nوضعیت: {'فعال ✅' if p['active'] else 'خاموش ⛔'}")
        self.api.send(chat_id,text,{'inline_keyboard':[
            [{'text':'⛔ خاموش' if p['active'] else '✅ فعال','callback_data':'stptoggle:'+str(row_id)},
             {'text':'🗑 حذف','callback_data':'stpdel:'+str(row_id)}]
        ]})

    def toggle_price(self,chat_id:int,row_id:int):
        p=self.admin_price_row(row_id);value=0 if p['active'] else 1
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE commerce_prices SET active=?,updated_at=? WHERE rowid=? AND owner=?",
                       (value,time.time(),row_id,self.owner))
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.price_bot_toggle',p['id'],'active='+str(value))
        self.admin_price_detail(chat_id,row_id)

    def delete_price(self,chat_id:int,row_id:int):
        p=self.admin_price_row(row_id)
        with self.runtime.store.transaction() as db:
            used=int(db.execute("SELECT COUNT(*) FROM commerce_orders WHERE owner=? AND price_id=?",(self.owner,p['id'])).fetchone()[0])
            if used:
                db.execute("UPDATE commerce_prices SET active=0,updated_at=? WHERE rowid=? AND owner=?",
                           (time.time(),row_id,self.owner));mode='آرشیو شد چون سفارش تاریخی دارد'
            else:
                db.execute("DELETE FROM commerce_prices WHERE rowid=? AND owner=?",(row_id,self.owner));mode='حذف شد'
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.price_bot_delete',p['id'],mode)
        self.api.send(chat_id,'✅ '+mode)

    def admin_reports(self,chat_id:int):
        st=self.runtime.forum.status(self.owner)
        if st.get('rebind_required'):
            state='♻️ نیازمند اتصال مجدد بعد از Restore'
        else:state='✅ متصل' if st.get('configured') else '⛔ متصل نیست'
        topics=len(st.get('topics') or [])
        self.api.send(chat_id,f"📊 مرکز گزارش DARK\nوضعیت: {state}\nTopicها: {topics}/8\n"
                      "فروش · پرداخت · سرویس · خطا · سیستم · بکاپ · امنیت · گزارش روزانه",
                      {'inline_keyboard':[[{'text':'♻️ اتصال مجدد بکاپ','callback_data':'forumrebind'}]]}
                      if st.get('rebind_required') else None)

    def admin_backup(self,chat_id:int):
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("""SELECT at,action,detail FROM live_audit
              WHERE action IN ('backup.full','backup.telegram_sent') ORDER BY id DESC LIMIT 1""").fetchone()
        last='هنوز بکاپی ثبت نشده' if not row else f"{row['action']} · {time.strftime('%Y-%m-%d %H:%M',time.localtime(float(row['at'])))}"
        self.api.send(chat_id,"💾 DARK Full Backup\n"
                      "بکاپ ربات جدا نیست؛ همان Full Backup پنل شامل Users/Products/Orders/Card/Forum/Topics/Representatives است.\n"
                      "بعد Restore، Bot Token عمداً حذف می‌شود و Token جدید + Rebind لازم است.\n"
                      f"آخرین وضعیت: {last}")

    def admin_settings(self,chat_id:int):
        cfg=self.bot_config();forum=self.runtime.forum.status(self.owner);gateway=self.manual_gateway()
        customer=self.runtime.customer.settings(self.owner)
        forum_state='متصل' if forum.get('configured') else ('Rebind Required' if forum.get('rebind_required') else 'متصل نیست')
        self.api.send(chat_id,f"⚙️ تنظیمات DARK BOT\n"
                      f"Bot: @{cfg.get('bot_username') or '—'}\nAdmin ID: {cfg.get('admin_telegram_id')}\n"
                      f"Forum: {forum_state}\nPayment: {'فعال' if gateway and gateway.get('enabled') else 'غیرفعال/تنظیم نشده'}\n"
                      f"Referral reward: {amount(customer['referral_reward_minor'],'IRT')}\n"
                      "Token از پنل وب تغییر می‌کند؛ پرداخت دستی از همین Bot مدیریت می‌شود.",
                      {'inline_keyboard':[[{'text':'👥 تنظیم پاداش زیرمجموعه','callback_data':'refreward'}]]})

    def client_row(self,row_id:int)->str:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("SELECT rowid,id,owner FROM clients WHERE rowid=?",(row_id,)).fetchone()
        if not row or row['owner']!=self.owner:raise PolicyError('Client not found in this bot scope')
        return str(row['id'])

    def admin_clients(self,chat_id:int):
        rows=self.runtime.manager.list(self.actor())[-12:]
        if not rows:self.api.send(chat_id,'کاربری وجود ندارد.');return
        buttons=[]
        with self.runtime.store.lock:
            for r in reversed(rows):
                dbrow=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",(r['email'],self.owner)).fetchone()
                if dbrow:buttons.append([{'text':r['email'],'callback_data':'cl:'+str(dbrow['rowid'])}])
        self.api.send(chat_id,'👥 آخرین کاربران\nبرای جستجوی مستقیم: /user USERNAME',{'inline_keyboard':buttons})

    def admin_user_search(self,chat_id:int,email:str):
        try:
            detail=self.runtime.manager.detail(self.actor(),email,credentials=True)
        except Exception as ex:self.api.send(chat_id,'کاربر پیدا نشد: '+str(ex));return
        with self.runtime.store.lock:row=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",(email,self.owner)).fetchone()
        if not row:self.api.send(chat_id,'کاربر در محدوده این پنل نیست.');return
        self.client_detail(chat_id,int(row['rowid']),detail)

    def client_detail(self,chat_id:int,row_id:int,detail:dict|None=None):
        email=self.client_row(row_id);detail=detail or self.runtime.manager.detail(self.actor(),email,credentials=True)
        c=detail.get('client') or {};enabled=c.get('enable') is not False
        expiry=int(c.get('expiryTime') or 0)
        expiry_text='بدون انقضا' if not expiry else time.strftime('%Y-%m-%d %H:%M',time.localtime(expiry/1000))
        activity=float(detail.get('activity_at') or 0)
        activity_text='—' if activity<=0 else time.strftime('%Y-%m-%d %H:%M',time.localtime(activity))
        inbounds=detail.get('inboundIds') or []
        quota=int(c.get('totalGB') or 0)
        text=(f"👤 {email}\n"
              f"وضعیت: {'فعال ✅' if enabled and not detail.get('block_reasons') else 'محدود/خاموش ⛔'}\n"
              f"مصرف: {self.bytes(int(detail.get('used_bytes') or 0))} / {self.bytes(quota) if quota else 'نامحدود'}\n"
              f"انقضا: {expiry_text}\nIP Limit: {int(c.get('limitIp') or 0)} · HWID: {int(c.get('limitHwid') or 0)}\n"
              f"Telegram: {c.get('tgId') or '—'}\nInboundها: {', '.join(map(str,inbounds)) or '—'}\n"
              f"آخرین فعالیت: {activity_text} · {detail.get('presence_source') or '—'}")
        kb=[[{'text':'⛔ غیرفعال' if enabled else '✅ فعال','callback_data':('cloff:' if enabled else 'clon:')+str(row_id)},
             {'text':'♻️ ریست ترافیک','callback_data':'clreset:'+str(row_id)}],
            [{'text':'🗓 تمدید','callback_data':'clrenew:'+str(row_id)},
             {'text':'➕ حجم','callback_data':'clvol:'+str(row_id)}],
            [{'text':'🌐 IP Limit','callback_data':'clip:'+str(row_id)},
             {'text':'🧬 HWID','callback_data':'clhw:'+str(row_id)}],
            [{'text':'🌍 لوکیشن/Inbound','callback_data':'clinb:'+str(row_id)},
             {'text':'🔗 تحویل سرویس','callback_data':'cllink:'+str(row_id)}],
            [{'text':'🗑 حذف سرویس','callback_data':'cldel:'+str(row_id)}]]
        self.api.send(chat_id,text,{'inline_keyboard':kb})

    def client_delivery(self,chat_id:int,row_id:int):
        email=self.client_row(row_id)
        detail=self.runtime.manager.detail(self.actor(),email,credentials=True)
        sub=str(detail.get('subscription_url') or '')
        if not sub:self.api.send(chat_id,'برای این سرویس لینک Subscription موجود نیست.');return
        self.api.send(chat_id,f"🔗 {email}\n{sub}")

    def client_action(self,chat_id:int,row_id:int,action:str):
        email=self.client_row(row_id);self.runtime.manager.action(self.actor(),email,action)
        self.api.send(chat_id,f'✅ عملیات {action} برای {email} ثبت شد.')

    def client_renew_menu(self,chat_id:int,row_id:int):
        email=self.client_row(row_id)
        with self.runtime.store.lock:
            waiting=self.runtime.store.db.execute("""SELECT 1 FROM commerce_orders
              WHERE owner=? AND client_id=? AND status='provisioned_waiting_activation' LIMIT 1""",(self.owner,email)).fetchone()
        if waiting:
            self.api.send(chat_id,'این سرویس منتظر اولین اتصال است؛ قبل از شروع زمان، تمدید دستی انجام نمی‌شود.');return
        self.api.send(chat_id,f"🗓 تمدید {email}",{'inline_keyboard':[
            [{'text':'30 روز','callback_data':f'clr:{row_id}:30'},
             {'text':'60 روز','callback_data':f'clr:{row_id}:60'},
             {'text':'90 روز','callback_data':f'clr:{row_id}:90'}],
            [{'text':'✍️ سفارشی','callback_data':'clrcustom:'+str(row_id)}]
        ]})

    def renew_client(self,chat_id:int,row_id:int,days:int):
        if not 1<=days<=3650:raise PolicyError('Renewal days are outside the allowed range')
        email=self.client_row(row_id)
        detail=self.runtime.manager.detail(self.actor(),email,credentials=True)
        with self.runtime.store.lock:
            waiting=self.runtime.store.db.execute("""SELECT 1 FROM commerce_orders
              WHERE owner=? AND client_id=? AND status='provisioned_waiting_activation' LIMIT 1""",(self.owner,email)).fetchone()
        if waiting:raise PolicyError('Service is waiting for first connection activation')
        current=int((detail.get('client') or {}).get('expiryTime') or 0)
        base=max(int(time.time()*1000),current)
        new_expiry=base+days*86400*1000
        self.runtime.manager.update(self.actor(),email,{'expiryTime':new_expiry})
        self.runtime.manager.audit(self.actor(),self.owner,'client.bot_renew',email,'days='+str(days))
        self.api.send(chat_id,f"✅ {email} برای {days} روز تمدید شد.")
        self.client_detail(chat_id,row_id)

    def start_service_input(self,chat_id:int,user_id:int,kind:str,row_id:int,prompt:str):
        if kind not in ('renew','volume','ip','hwid'):raise PolicyError('Unknown service input kind')
        self.client_row(row_id)
        self.sessions[user_id]='service_'+kind
        self.session_data[user_id]={'row_id':row_id}
        self.api.send(chat_id,prompt+'\nبرای لغو: /cancel')

    def handle_service_text(self,chat_id:int,user_id:int,text:str):
        state=self.sessions.get(user_id,'');data=self.session_data.get(user_id) or {}
        row_id=int(data.get('row_id') or 0)
        if not row_id:raise PolicyError('Service wizard lost its target')
        email=self.client_row(row_id);value=text.strip()
        if state=='service_renew':
            try:days=int(value)
            except ValueError:self.api.send(chat_id,'تعداد روز باید عدد صحیح باشد.');return
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.renew_client(chat_id,row_id,days);return
        if state=='service_volume':
            try:gb=float(value)
            except ValueError:self.api.send(chat_id,'حجم باید عدد باشد.');return
            if not 0<gb<=1000000:self.api.send(chat_id,'حجم خارج از محدوده است.');return
            detail=self.runtime.manager.detail(self.actor(),email,credentials=True);client=detail.get('client') or {}
            current=int(client.get('totalGB') or 0)
            if current==0:self.api.send(chat_id,'این سرویس نامحدود است و افزایش حجم برای آن معنی ندارد.');return
            add=int(gb*1024**3);self.runtime.manager.update(self.actor(),email,{'totalGB':current+add})
            self.runtime.manager.audit(self.actor(),self.owner,'client.bot_add_volume',email,'bytes='+str(add))
            msg=f"✅ {gb:g} GB به {email} اضافه شد."
        elif state=='service_ip':
            try:n=int(value)
            except ValueError:self.api.send(chat_id,'IP Limit باید عدد صحیح باشد.');return
            if not 0<=n<=1000:self.api.send(chat_id,'IP Limit خارج از محدوده است.');return
            self.runtime.manager.update(self.actor(),email,{'limitIp':n})
            self.runtime.manager.audit(self.actor(),self.owner,'client.bot_ip_limit',email,'limit='+str(n))
            msg=f"✅ IP Limit روی {n} تنظیم شد."
        elif state=='service_hwid':
            try:n=int(value)
            except ValueError:self.api.send(chat_id,'HWID باید عدد صحیح باشد.');return
            if not 0<=n<=1000:self.api.send(chat_id,'HWID خارج از محدوده است.');return
            self.runtime.manager.update(self.actor(),email,{'limitHwid':n})
            self.runtime.manager.audit(self.actor(),self.owner,'client.bot_hwid_limit',email,'limit='+str(n))
            msg=f"✅ HWID Limit روی {n} تنظیم شد."
        else:raise PolicyError('Unknown service wizard state')
        self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
        self.api.send(chat_id,msg);self.client_detail(chat_id,row_id)

    def start_client_inbounds(self,chat_id:int,user_id:int,row_id:int):
        email=self.client_row(row_id);detail=self.runtime.manager.detail(self.actor(),email,credentials=False)
        self.sessions[user_id]='service_inbounds'
        self.session_data[user_id]={'row_id':row_id,'selected_inbounds':[int(x) for x in detail.get('inboundIds') or []]}
        self.show_client_inbounds(chat_id,user_id)

    def show_client_inbounds(self,chat_id:int,user_id:int):
        data=self.session_data.get(user_id) or {};selected=set(data.get('selected_inbounds') or [])
        catalog=self.inbound_catalog()
        if not catalog:self.api.send(chat_id,'Inbound مجازی برای این پنل وجود ندارد.');return
        kb=[]
        for x in catalog:
            mark='✅' if x['id'] in selected else '⬜'
            kb.append([{'text':f"{mark} {x['id']} · {x['name']} · :{x['port']}"[:62],
                        'callback_data':'clinbt:'+str(x['id'])}])
        kb.append([{'text':'✅ ذخیره Inboundها','callback_data':'clinbdone'}])
        self.api.send(chat_id,'🌍 Inboundهای سرویس را انتخاب کن:',{'inline_keyboard':kb})

    def toggle_client_inbound(self,chat_id:int,user_id:int,inbound_id:int):
        if self.sessions.get(user_id)!='service_inbounds':raise PolicyError('Service is not selecting inbounds')
        allowed={x['id'] for x in self.inbound_catalog()}
        if inbound_id not in allowed:raise PolicyError('Inbound is outside this panel scope')
        selected=set(self.session_data[user_id].get('selected_inbounds') or [])
        if inbound_id in selected:selected.remove(inbound_id)
        else:selected.add(inbound_id)
        self.session_data[user_id]['selected_inbounds']=sorted(selected)
        self.show_client_inbounds(chat_id,user_id)

    def finish_client_inbounds(self,chat_id:int,user_id:int):
        if self.sessions.get(user_id)!='service_inbounds':raise PolicyError('Service is not selecting inbounds')
        data=self.session_data[user_id];selected=[int(x) for x in data.get('selected_inbounds') or []]
        if not selected:self.api.send(chat_id,'حداقل یک Inbound باید انتخاب شود.');return
        row_id=int(data['row_id']);email=self.client_row(row_id)
        self.runtime.manager.update(self.actor(),email,{},ids=selected)
        self.runtime.manager.audit(self.actor(),self.owner,'client.bot_inbounds',email,'ids='+','.join(map(str,selected)))
        self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
        self.api.send(chat_id,'✅ Inboundهای سرویس بروزرسانی شدند.')
        self.client_detail(chat_id,row_id)

    def admin_orders(self,chat_id:int):
        with self.runtime.store.lock:
            rows=[dict(r) for r in self.runtime.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_orders WHERE owner=? ORDER BY created_at DESC LIMIT 15",(self.owner,))]
        if not rows:self.api.send(chat_id,'هنوز سفارشی وجود ندارد.');return
        buttons=[]
        for r in rows:
            label=f"{r['status']} · {amount(r['amount_minor'],r['currency'])} · {r['buyer_telegram_id']}"
            buttons.append([{'text':label[:60],'callback_data':'ord:'+str(r['row_id'])}])
        self.api.send(chat_id,'🧾 آخرین سفارش‌ها · برای جزئیات انتخاب کن:',{'inline_keyboard':buttons})

    def order_detail(self,chat_id:int,row_id:int):
        with self.runtime.store.lock:
            r=self.runtime.store.db.execute("SELECT rowid AS row_id,* FROM commerce_orders WHERE rowid=? AND owner=?",
                                            (row_id,self.owner)).fetchone()
        if not r:raise PolicyError('Order not found in this bot scope')
        r=dict(r);created=time.strftime('%Y-%m-%d %H:%M',time.localtime(float(r.get('created_at') or 0)))
        text=(f"🧾 سفارش {r['id']}\n"
              f"وضعیت: {r['status']}\nمشتری: {r['buyer_telegram_id']} @{r.get('buyer_username') or '—'}\n"
              f"محصول: {r['product_id']} / {r['price_id']}\nمبلغ: {amount(r['amount_minor'],r['currency'])}\n"
              f"درگاه: {r.get('gateway_id') or '—'}\nسرویس: {r.get('client_id') or '—'}\n"
              f"فعال‌سازی: {r.get('activation_mode') or '—'} · تحویل: {r.get('delivery_mode') or '—'}\n"
              f"IP/HWID: {r.get('ip_limit') or 0}/{r.get('hwid_limit') or 0}\nزمان: {created}")
        if r.get('fulfillment_error'):text+='\n⚠️ '+str(r['fulfillment_error'])[:600]
        self.api.send(chat_id,text)

    def manual_gateway(self)->dict[str,Any]|None:
        return next((r for r in self.runtime.commerce.gateway_rows(self.owner)
                     if r.get('id')=='card' and r.get('kind')=='manual'),None)

    def admin_gateways(self,chat_id:int):
        row=self.manual_gateway()
        if not row:
            self.api.send(chat_id,'💳 پرداخت دستی هنوز تنظیم نشده است.\nتنظیمات کارت فقط از داخل همین ربات انجام می‌شود.',
                          {'inline_keyboard':[[{'text':'➕ تنظیم کارت بانکی','callback_data':'paycfg'}]]});return
        state='فعال ✅' if row['enabled'] else 'غیرفعال ⛔'
        text=('💳 پرداخت دستی\n'
              f"وضعیت: {state}\n"
              f"شماره کارت: {row.get('card_number') or '—'}\n"
              f"صاحب کارت: {row.get('card_holder') or '—'}\n"
              f"بانک: {row.get('bank_name') or '—'}")
        self.api.send(chat_id,text,{'inline_keyboard':[
            [{'text':'✏️ تغییر اطلاعات کارت','callback_data':'paycfg'}],
            [{'text':'⛔ غیرفعال' if row['enabled'] else '✅ فعال','callback_data':'paytoggle'}]
        ]})

    def start_payment_setup(self,chat_id:int,user_id:int):
        self.sessions[user_id]='pay_card';self.session_data[user_id]={}
        self.api.send(chat_id,'💳 شماره کارت ۱۶ رقمی را بفرست.\nبرای لغو: /cancel')

    def handle_payment_setup_text(self,chat_id:int,user_id:int,text:str):
        state=self.sessions.get(user_id,'');data=self.session_data.setdefault(user_id,{})
        if state=='pay_card':
            digits=''.join(str(int(ch)) for ch in text if ch.isdigit())
            if len(digits)!=16:
                self.api.send(chat_id,'شماره کارت باید دقیقاً ۱۶ رقم باشد. دوباره بفرست.');return
            data['card_number']=digits;self.sessions[user_id]='pay_holder'
            self.api.send(chat_id,'نام صاحب کارت را بفرست.');return
        if state=='pay_holder':
            value=text.strip()
            if not 2<=len(value)<=128:self.api.send(chat_id,'نام صاحب کارت نامعتبر است.');return
            data['card_holder']=value;self.sessions[user_id]='pay_bank'
            self.api.send(chat_id,'نام بانک را بفرست.');return
        if state=='pay_bank':
            value=text.strip()
            if not 2<=len(value)<=128:self.api.send(chat_id,'نام بانک نامعتبر است.');return
            data['bank_name']=value;self.sessions[user_id]='pay_instructions'
            self.api.send(chat_id,'متن راهنمای پرداخت را بفرست. اگر توضیح اضافه نمی‌خواهی فقط - بفرست.');return
        if state!='pay_instructions':raise PolicyError('Unknown payment setup state')
        data['instructions']='' if text.strip()=='-' else text.strip()[:4000]
        now=time.time()
        with self.runtime.store.transaction() as db:
            db.execute("""INSERT INTO commerce_gateways(id,owner,label,kind,enabled,card_number,card_holder,bank_name,
              instructions,plugin,secret_enc,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(owner,id) DO UPDATE SET label=excluded.label,kind='manual',enabled=1,
              card_number=excluded.card_number,card_holder=excluded.card_holder,bank_name=excluded.bank_name,
              instructions=excluded.instructions,plugin='',secret_enc='',updated_at=excluded.updated_at""",
              ('card',self.owner,'کارت به کارت','manual',1,data['card_number'],data['card_holder'],data['bank_name'],
               data['instructions'],'','',now))
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.manual_payment_bot_save','card','configured from Telegram admin')
        self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
        self.api.send(chat_id,'✅ اطلاعات پرداخت دستی ذخیره و فعال شد.')
        self.admin_gateways(chat_id)

    def toggle_manual_payment(self,chat_id:int,user_id:int):
        row=self.manual_gateway()
        if not row:self.start_payment_setup(chat_id,user_id);return
        enabled=not bool(row['enabled'])
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE commerce_gateways SET enabled=?,updated_at=? WHERE owner=? AND id=?',
                       (int(enabled),time.time(),self.owner,'card'))
        self.runtime.manager.audit(self.actor(),self.owner,'commerce.manual_payment_toggle','card','enabled='+str(enabled))
        self.admin_gateways(chat_id)

    def representatives(self,chat_id:int):
        with self.runtime.store.lock:
            rows=[dict(r) for r in self.runtime.store.db.execute("""SELECT a.id,a.disabled,p.name,o.volume_credit_bytes,o.unlimited_credit
              FROM api_admins a JOIN owner_profiles p ON p.id=a.id JOIN owners o ON o.id=a.id
              WHERE a.role='reseller' ORDER BY p.name,a.id""")]
        if not rows:self.api.send(chat_id,'هنوز نماینده‌ای ساخته نشده است.');return
        lines=['🤝 نمایندگان']
        for r in rows[:30]:
            lines.append(f"• {r['name']} ({r['id']}) · {self.bytes(r['volume_credit_bytes'])} · Unlimited {r['unlimited_credit']} · {'غیرفعال' if r['disabled'] else 'فعال'}")
        self.api.send(chat_id,'\n'.join(lines))

    def create_representative_from_text(self,chat_id:int,user_id:int,text:str):
        if not self.is_admin(user_id) or self.owner_role()!='owner':
            self.sessions.pop(user_id,None);raise PolicyError('Owner admin required')
        parts=[x.strip() for x in text.split('|')]
        if len(parts)!=4:
            self.api.send(chat_id,'فرمت درست نیست. چهار بخش با | بفرست.');return
        rid,password,volume_text,unlimited_text=parts
        if not NAME_RE.fullmatch(rid):self.api.send(chat_id,'شناسه نماینده نامعتبر است.');return
        if len(password)<8:self.api.send(chat_id,'رمز باید حداقل ۸ کاراکتر باشد.');return
        try:volume_gb=int(volume_text);unlimited=int(unlimited_text)
        except ValueError:self.api.send(chat_id,'حجم و تعداد Unlimited باید عدد صحیح باشند.');return
        if volume_gb<0 or unlimited<0:self.api.send(chat_id,'مقادیر اعتبار نمی‌توانند منفی باشند.');return
        actor=self.actor()
        with self.runtime.store.lock:
            exists=self.runtime.store.db.execute('SELECT 1 FROM api_admins WHERE id=?',(rid,)).fetchone()
        if exists:self.api.send(chat_id,'این شناسه قبلاً وجود دارد.');return
        self.runtime.manager.owner_put(actor,rid,name=rid,allowed=[],volume_credit_bytes=volume_gb*1024**3,
            unlimited_credit=unlimited,max_clients=0,manual=False,prefix=rid+'_',max_client_ips=0,max_client_hwid=0)
        self.runtime.auth.admin_create(actor,rid,password,'reseller',DEFAULTS['reseller'])
        self.runtime.manager.audit(actor,rid,'representative.bot_create',rid,f'volume_gb={volume_gb}; unlimited={unlimited}')
        self.sessions.pop(user_id,None)
        self.api.send(chat_id,f"✅ نماینده ساخته شد.\nشناسه ورود: {rid}\nاعتبار حجمی: {volume_gb} GB\nUnlimited: {unlimited}\nبرای فروش، Inboundهای مجاز را از پنل اصلی به نماینده اختصاص بده.")

class TelegramBotRuntime:
    def __init__(self,commerce,manager,auth,audit):
        self.commerce=commerce;self.store=commerce.store;self.manager=manager;self.auth=auth;self.audit=audit
        self.forum=TelegramForumCenter(self.store)
        self.customer=CustomerCenter(self.store,commerce,manager)
        self.stop_event=threading.Event();self.wake_event=threading.Event();self.thread=None
        self.workers:dict[str,BotWorker]={};self.statuses:dict[str,dict[str,Any]]={};self.lock=threading.RLock()

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.run,name='dark-telegram-supervisor',daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set();self.wake_event.set()
        if self.thread and self.thread.is_alive():self.thread.join(timeout=4)
        with self.lock:workers=list(self.workers.values());self.workers.clear()
        for w in workers:w.stop()

    def wake(self):
        self.wake_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_workers()
                activated=self.commerce.activate_first_connections(self.manager)
                self.notify_activations(activated)
            except Exception:
                pass
            self.wake_event.wait(3);self.wake_event.clear()
        self.sync_workers(stop_all=True)

    def notify_activations(self,items:list[dict[str,Any]]):
        for item in items:
            owner=str(item['owner'])
            with self.lock:worker=self.workers.get(owner)
            if not worker:continue
            expires=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(float(item['expires_at'])))
            text=f"✅ اولین اتصال ثبت شد. مدت سرویس شروع شد.\nسرویس: {item['client_id']}\nانقضا: {expires}"
            try:worker.api.send(int(item['buyer_telegram_id']),text)
            except Exception:pass
            try:
                self.manager.audit(self.commerce.actor_for(owner),owner,'commerce.first_connection_activate',
                                   item['client_id'],f"order={item['order_id']}; expires_at={item['expires_at']}")
                self.forum.report(worker.api,owner,'services',text)
            except Exception:pass

    def sync_workers(self,stop_all:bool=False):
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("SELECT owner,enabled,token_enc FROM telegram_bots")]
        desired={} if stop_all else {r['owner']:r for r in rows if r['enabled'] and r['token_enc']}
        stop_list=[]
        with self.lock:
            for owner,worker in list(self.workers.items()):
                row=desired.get(owner);mark=(row or {}).get('token_enc','')
                if not row or mark!=worker.token_mark:
                    stop_list.append(worker);self.workers.pop(owner,None)
        for worker in stop_list:worker.stop()
        with self.lock:
            missing=[(owner,row) for owner,row in desired.items() if owner not in self.workers]
        for owner,row in missing:
            try:token=self.commerce._open(row['token_enc'])
            except Exception as ex:self.persist_error(owner,str(ex));continue
            w=BotWorker(self,owner,token,row['token_enc'])
            with self.lock:
                if owner in self.workers:continue
                self.workers[owner]=w
            w.start()

    def set_status(self,owner:str,state:str,error:str=''):
        with self.lock:self.statuses[owner]={'runtime_state':state,'runtime_error':error,'runtime_at':time.time()}

    def status(self,owner:str)->dict[str,Any]:
        with self.lock:return dict(self.statuses.get(owner,{'runtime_state':'stopped','runtime_error':'','runtime_at':0}))

    def persist_error(self,owner:str,error:str):
        with self.store.transaction() as db:
            db.execute("UPDATE telegram_bots SET last_error=?,last_seen=? WHERE owner=?",(str(error)[:1000],time.time(),owner))

    def touch(self,owner:str):
        with self.store.transaction() as db:db.execute("UPDATE telegram_bots SET last_seen=?,last_error='' WHERE owner=?",(time.time(),owner))

    def rebind_forum(self,owner:str)->dict[str,Any]:
        with self.lock:worker=self.workers.get(owner)
        if worker and worker.bot_id:
            return self.forum.rebind_existing(worker.api,owner,worker.bot_id)
        row=self.commerce.bot_row(owner,secret=True)
        if not row or not row.get('bot_token'):raise PolicyError('New Bot Token is required before forum rebind')
        api=TelegramAPI(row['bot_token'])
        try:
            me=api.call('getMe')
            return self.forum.rebind_existing(api,owner,int(me.get('id') or 0))
        finally:api.close()

    def repair_forum(self,owner:str)->dict[str,Any]:
        with self.lock:worker=self.workers.get(owner)
        if worker and worker.bot_id:
            return self.forum.repair(worker.api,owner,worker.bot_id)
        row=self.commerce.bot_row(owner,secret=True)
        if not row or not row.get('bot_token'):raise PolicyError('Bot token is not configured')
        api=TelegramAPI(row['bot_token'])
        try:
            me=api.call('getMe')
            return self.forum.repair(api,owner,int(me.get('id') or 0))
        finally:api.close()

    def send_backup(self,owner:str,filename:str,data:bytes,caption:str)->bool:
        with self.lock:worker=self.workers.get(owner)
        if worker:
            api=worker.api;temporary=False
        else:
            row=self.commerce.bot_row(owner,secret=True)
            if not row or not row.get('bot_token'):return False
            api=TelegramAPI(row['bot_token']);temporary=True
        try:
            target=self.forum._target(owner,'backups')
            if target:
                chat_id,thread_id=target
                api.send_document_bytes(chat_id,filename,data,caption,thread_id);return True
            admin=int(self.commerce.bot_row(owner,secret=True)['admin_telegram_id'])
            api.send_document_bytes(admin,filename,data,caption);return True
        finally:
            if temporary:api.close()

    def test_bot(self,owner:str)->dict[str,Any]:
        row=self.commerce.bot_row(owner,secret=True)
        if not row or not row.get('bot_token'):raise PolicyError('Bot token is not configured')
        api=TelegramAPI(row['bot_token'])
        try:
            me=api.call('getMe')
            return {'ok':True,'id':me.get('id'),'username':me.get('username') or '',
                    'name':me.get('first_name') or ''}
        finally:api.close()