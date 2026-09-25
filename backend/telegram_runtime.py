from __future__ import annotations
import json
import threading
import time
from typing import Any

import httpx

from auth import DEFAULTS
from dark_policy import Actor, NAME_RE, PolicyError

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

class BotWorker:
    def __init__(self,runtime,owner:str,token:str,token_mark:str):
        self.runtime=runtime;self.owner=owner;self.token=token;self.token_mark=token_mark
        self.stop_event=threading.Event();self.thread=None;self.api=TelegramAPI(token)
        self.sessions:dict[int,str]={}

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
            me=self.api.call('getMe')
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
                    self.runtime.touch(self.owner);continue
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
                self.status('online')
            except Exception as ex:
                msg=str(ex);self.runtime.persist_error(self.owner,msg);self.status('error',msg)
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
            rows += [['👥 مدیریت کاربران','🧾 سفارش‌ها'],['📊 وضعیت ربات','💳 درگاه‌ها']]
            if self.owner_role()=='owner':rows += [['🤝 نمایندگان','➕ ساخت نماینده']]
        return {'keyboard':[[{'text':x} for x in row] for row in rows],
                'resize_keyboard':True,'is_persistent':True}

    def send_home(self,chat_id:int,user_id:int):
        admin=self.is_admin(user_id)
        role='مدیریت + فروش' if admin else 'فروشگاه'
        self.api.send(chat_id,f'DARK XRAY BOT\nحالت: {role}\nیکی از گزینه‌ها را انتخاب کن.',self.main_keyboard(admin))

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
        if msg.get('photo') or msg.get('document'):
            if self.handle_receipt(chat_id,user_id,msg):return
        text=str(msg.get('text') or '').strip()
        if not text:return
        if self.sessions.get(user_id)=='new_rep':
            self.create_representative_from_text(chat_id,user_id,text);return
        low=text.lower()
        if low in ('/start','start'):self.send_home(chat_id,user_id);return
        if low=='/shop' or text=='🛍 فروشگاه':self.shop(chat_id);return
        if low=='/services' or text=='📦 سرویس‌های من':self.services(chat_id,user_id);return
        if low=='/status' or text=='📊 وضعیت ربات':self.status_menu(chat_id,user_id);return
        if text=='👥 مدیریت کاربران' and self.is_admin(user_id):self.admin_clients(chat_id);return
        if text=='🧾 سفارش‌ها' and self.is_admin(user_id):self.admin_orders(chat_id);return
        if text=='💳 درگاه‌ها' and self.is_admin(user_id):self.admin_gateways(chat_id);return
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
            gateways=self.runtime.commerce.gateway_rows(self.owner,enabled_only=True)
            if not gateways:
                self.api.send(chat_id,f"سفارش {order['id']} ساخته شد ولی روش پرداخت فعال نیست. با پشتیبانی تماس بگیر.");return
            kb=[[{'text':g['label'],'callback_data':f"g:{g['row_id']}:{order['id']}"}] for g in gateways]
            self.api.send(chat_id,f"سفارش: {order['id']}\nمبلغ: {amount(order['amount_minor'],order['currency'])}\nروش پرداخت را انتخاب کن:",
                          {'inline_keyboard':kb});return
        if data.startswith('g:'):
            _,gw_row,order_id=data.split(':',2);gw=self.gateway_by_rowid(int(gw_row))
            result=self.runtime.commerce.start_payment(self.owner,order_id,gw['id'])
            if result['mode']=='manual':
                self.api.send(chat_id,f"مبلغ سفارش را پرداخت کن و سپس تصویر یا فایل رسید را همین‌جا بفرست.\n\n{result['instructions']}")
            else:self.api.send(chat_id,'این درگاه به‌صورت Plugin تعریف شده ولی اتصال Checkout آن هنوز فعال نشده است.')
            return
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
            if result.get('provisioned'):
                detail=result['client'];sub=detail.get('subscription_url') or ''
                self.api.send(chat_id,f"✅ پرداخت تأیید و سرویس ساخته شد.\n{result['client_id']}")
                self.api.send(int(order['buyer_telegram_id']),f"✅ پرداخت تأیید شد و سرویس شما ساخته شد.\nشناسه: {result['client_id']}\n{sub}")
            else:
                self.api.send(chat_id,'پرداخت تأیید شد ولی ساخت سرویس خطا داد:\n'+str(result.get('fulfillment_error') or 'unknown'))
                self.api.send(int(order['buyer_telegram_id']),'پرداخت شما تأیید شد و سفارش برای تکمیل سرویس در صف مدیریت قرار گرفت.')
            return
        if data.startswith('payno:') and self.is_admin(user_id):
            row_id=int(data.split(':',1)[1]);result=self.runtime.commerce.reject_payment(self.owner,row_id)
            self.api.send(chat_id,'❌ رسید رد شد.')
            self.api.send(int(result['buyer_telegram_id']),'رسید پرداخت تأیید نشد. می‌توانی دوباره پرداخت و رسید جدید ارسال کنی.')
            return
        if data=='noop':return

    def handle_receipt(self,chat_id:int,user_id:int,msg:dict[str,Any])->bool:
        order=self.runtime.commerce.latest_waiting_order(self.owner,user_id)
        if not order:return False
        if msg.get('photo'):
            file_id=str(msg['photo'][-1]['file_id']);kind='photo'
        else:
            file_id=str(msg['document']['file_id']);kind='document'
        result=self.runtime.commerce.record_receipt(self.owner,user_id,f'telegram:{kind}:{file_id}')
        pay=result['payment'];row_id=int(pay['row_id'])
        self.api.send(chat_id,'✅ رسید ثبت شد و برای مدیر ارسال شد. نتیجه تأیید از همین ربات اعلام می‌شود.')
        admin=int(self.bot_config()['admin_telegram_id'])
        caption=f"رسید جدید\nسفارش: {order['id']}\nکاربر: {user_id}\nمبلغ: {amount(order['amount_minor'],order['currency'])}"
        try:
            method='sendPhoto' if kind=='photo' else 'sendDocument'
            self.api.call(method,{'chat_id':admin,kind:file_id,'caption':caption})
        except Exception:self.api.send(admin,caption)
        self.api.send(admin,'تأیید یا رد رسید:',{'inline_keyboard':[[
            {'text':'✅ تأیید و ساخت سرویس','callback_data':'payok:'+str(row_id)},
            {'text':'❌ رد رسید','callback_data':'payno:'+str(row_id)}
        ]]})
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
        self.api.send(chat_id,f"📊 وضعیت DARK BOT\nکاربران: {clients}\nمحصولات: {products}\nسفارش‌ها: {orders}\nنیازمند پیگیری: {pending}")

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

    def admin_gateways(self,chat_id:int):
        rows=self.runtime.commerce.gateway_rows(self.owner)
        if not rows:self.api.send(chat_id,'هیچ درگاه/روش پرداختی تعریف نشده است. از پنل وب اضافه کن.');return
        self.api.send(chat_id,'💳 روش‌های پرداخت\n'+'\n'.join(f"• {r['label']} · {r['kind']} · {'فعال' if r['enabled'] else 'خاموش'}" for r in rows))

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
            try:self.sync_workers()
            except Exception:pass
            self.wake_event.wait(3);self.wake_event.clear()
        self.sync_workers(stop_all=True)

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
        with self.store.transaction() as db:db.execute("UPDATE telegram_bots SET last_seen=? WHERE owner=?",(time.time(),owner))

    def test_bot(self,owner:str)->dict[str,Any]:
        row=self.commerce.bot_row(owner,secret=True)
        if not row or not row.get('bot_token'):raise PolicyError('Bot token is not configured')
        api=TelegramAPI(row['bot_token'])
        try:
            me=api.call('getMe')
            return {'ok':True,'id':me.get('id'),'username':me.get('username') or '',
                    'name':me.get('first_name') or ''}
        finally:api.close()