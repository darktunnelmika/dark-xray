from __future__ import annotations
import json
import threading
import time
from typing import Any

import httpx

from auth import DEFAULTS
from dark_policy import Actor, NAME_RE, PolicyError
from telegram_forum import TelegramForumCenter

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

class BotWorker:
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
        rows=[['🛍 فروشگاه','📦 سرویس‌های من']]
        if admin:
            rows += [['👥 مدیریت کاربران','🧾 سفارش‌ها'],['📊 وضعیت ربات','💳 پرداخت دستی']]
            if self.owner_role()=='owner':rows += [['🤝 نمایندگان','➕ ساخت نماینده']]
        return {'keyboard':[[{'text':x} for x in row] for row in rows],
                'resize_keyboard':True,'is_persistent':True}

    def send_home(self,chat_id:int,user_id:int):
        admin=self.is_admin(user_id)
        if admin and not self.runtime.forum.status(self.owner).get('configured'):
            self.api.send(chat_id,'مرحله اول: انجمن گزارش DARK را انتخاب کن. ربات باید Admin انجمن باشد و مجوز مدیریت Topicها را داشته باشد.',
                          self.runtime.forum.request_keyboard());return
        role='مدیریت + فروش' if admin else 'فروشگاه'
        self.api.send(chat_id,f'DARK XRAY BOT\nحالت: {role}\nیکی از گزینه‌ها را انتخاب کن.',self.main_keyboard(admin))

    def maybe_forum_prompt(self,force:bool=False):
        if self.runtime.forum.status(self.owner).get('configured'):return False
        row=self.bot_config();now=time.time();last=float(row.get('forum_prompted_at') or 0)
        if not force and now-last<86400:return False
        admin=int(row['admin_telegram_id'])
        try:
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
            if self.handle_receipt(chat_id,user_id,msg):return
        text=str(msg.get('text') or '').strip()
        if not text:return
        low=text.lower()
        if low in ('/cancel','cancel','لغو'):
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.api.send(chat_id,'عملیات لغو شد.',self.main_keyboard(self.is_admin(user_id)));return
        session=self.sessions.get(user_id,'')
        if session.startswith('pay_') and self.is_admin(user_id):
            self.handle_payment_setup_text(chat_id,user_id,text);return
        if session=='new_rep':
            self.create_representative_from_text(chat_id,user_id,text);return
        if low in ('/start','start'):self.send_home(chat_id,user_id);return
        if low=='/shop' or text=='🛍 فروشگاه':self.shop(chat_id);return
        if low=='/services' or text=='📦 سرویس‌های من':self.services(chat_id,user_id);return
        if low=='/status' or text=='📊 وضعیت ربات':self.status_menu(chat_id,user_id);return
        if text=='👥 مدیریت کاربران' and self.is_admin(user_id):self.admin_clients(chat_id);return
        if text=='🧾 سفارش‌ها' and self.is_admin(user_id):self.admin_orders(chat_id);return
        if text=='💳 پرداخت دستی' and self.is_admin(user_id):self.admin_gateways(chat_id);return
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
        products=self.runtime.commerce.product_rows(self.owner,public=True)
        if not products:self.api.send(chat_id,'فعلاً محصول فعالی در فروشگاه تعریف نشده است.');return
        buttons=[]
        for p in products:
            if not any(x['active'] for x in p['prices']):continue
            buttons.append([{'text':p['name'],'callback_data':'p:'+str(p['row_id'])}])
        if not buttons:self.api.send(chat_id,'فعلاً پلن قابل خریدی وجود ندارد.');return
        self.api.send(chat_id,'🛍 فروشگاه DARK\nمحصول را انتخاب کن:',{'inline_keyboard':buttons})

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
        if data=='paycfg' and self.is_admin(user_id):
            self.start_payment_setup(chat_id,user_id);return
        if data=='paytoggle' and self.is_admin(user_id):
            self.toggle_manual_payment(chat_id,user_id);return
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
        rows=[x for x in self.runtime.manager.list(self.actor()) if int(x.get('client',{}).get('tgId') or 0)==int(user_id)]
        if not rows:self.api.send(chat_id,'هنوز سرویسی به Telegram ID شما متصل نیست.');return
        lines=['📦 سرویس‌های من']
        for r in rows[:20]:
            c=r.get('client') or {};quota=int(c.get('totalGB') or 0);used=int(r.get('used_bytes') or 0)
            left='نامحدود' if quota==0 else self.bytes(max(0,quota-used))
            lines.append(f"• {r['email']}\n  باقی‌مانده: {left}\n  وضعیت: {'فعال' if not r.get('block_reasons') else 'محدود'}")
        self.api.send(chat_id,'\n'.join(lines))

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
            detail=self.runtime.manager.detail(self.actor(),email,credentials=False)
        except Exception as ex:self.api.send(chat_id,'کاربر پیدا نشد: '+str(ex));return
        with self.runtime.store.lock:row=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",(email,self.owner)).fetchone()
        if not row:self.api.send(chat_id,'کاربر در محدوده این پنل نیست.');return
        self.client_detail(chat_id,int(row['rowid']),detail)

    def client_detail(self,chat_id:int,row_id:int,detail:dict|None=None):
        email=self.client_row(row_id);detail=detail or self.runtime.manager.detail(self.actor(),email,credentials=False)
        c=detail.get('client') or {};enabled=c.get('enable') is not False
        text=f"👤 {email}\nمصرف: {self.bytes(int(detail.get('used_bytes') or 0))}\nسهمیه: {self.bytes(int(c.get('totalGB') or 0)) if c.get('totalGB') else 'نامحدود'}\nوضعیت: {'فعال' if enabled else 'غیرفعال'}"
        kb=[[{'text':'⛔ غیرفعال' if enabled else '✅ فعال','callback_data':('cloff:' if enabled else 'clon:')+str(row_id)},
             {'text':'♻️ ریست ترافیک','callback_data':'clreset:'+str(row_id)}]]
        self.api.send(chat_id,text,{'inline_keyboard':kb})

    def client_action(self,chat_id:int,row_id:int,action:str):
        email=self.client_row(row_id);self.runtime.manager.action(self.actor(),email,action)
        self.api.send(chat_id,f'✅ عملیات {action} برای {email} ثبت شد.')

    def admin_orders(self,chat_id:int):
        with self.runtime.store.lock:
            rows=[dict(r) for r in self.runtime.store.db.execute(
                "SELECT * FROM commerce_orders WHERE owner=? ORDER BY created_at DESC LIMIT 15",(self.owner,))]
        if not rows:self.api.send(chat_id,'هنوز سفارشی وجود ندارد.');return
        lines=['🧾 آخرین سفارش‌ها']
        for r in rows:lines.append(f"• {r['id']} · {amount(r['amount_minor'],r['currency'])} · {r['status']}")
        self.api.send(chat_id,'\n'.join(lines))

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