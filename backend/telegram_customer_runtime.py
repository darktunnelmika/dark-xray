from __future__ import annotations
import time
from typing import Any

from dark_policy import PolicyError

def money(value:int,currency:str='IRT')->str:
    code=str(currency or '').upper()
    if code=='IRT':return f"{int(value):,} تومان"
    if code=='IRR':return f"{int(value):,} ریال"
    return f"{int(value):,} {code}"

class CustomerBotFeatures:
    def customer_shop(self,chat_id:int):
        products=self.runtime.commerce.product_rows(self.owner,public=True)
        rows=[p for p in products if any(x.get('active') for x in p.get('prices') or [])]
        if not rows:self.api.send(chat_id,'فعلاً اشتراک قابل خریدی وجود ندارد.');return
        buttons=[]
        for p in rows[:40]:
            category=str(p.get('category') or 'General')
            buttons.append([{'text':f"🛍 {p['name']} · {category}"[:62],'callback_data':'p:'+str(p['row_id'])}])
        self.api.send(chat_id,'🛍 خرید اشتراک\nمحصول را انتخاب کن:',{'inline_keyboard':buttons})

    def customer_wallet_menu(self,chat_id:int,user_id:int):
        wallet=self.runtime.customer.wallet(self.owner,user_id)
        with self.runtime.store.lock:
            pending=int(self.runtime.store.db.execute("""SELECT COUNT(*) FROM customer_topups
              WHERE owner=? AND telegram_id=? AND status IN ('awaiting_receipt','review')""",
              (self.owner,int(user_id))).fetchone()[0])
        self.api.send(chat_id,
            f"💰 کیف پول DARK\nموجودی: {money(wallet['balance_minor'],wallet['currency'])}\n"
            f"شارژ در انتظار: {pending}",
            {'inline_keyboard':[
                [{'text':'➕ 100,000 تومان','callback_data':'wtop:100000'},
                 {'text':'➕ 200,000 تومان','callback_data':'wtop:200000'}],
                [{'text':'➕ 500,000 تومان','callback_data':'wtop:500000'},
                 {'text':'✍️ مبلغ دلخواه','callback_data':'wcustom'}],
                [{'text':'📜 تراکنش‌ها','callback_data':'whist'}]
            ]})

    def customer_start_topup(self,chat_id:int,user_id:int,username:str,amount_minor:int):
        gateway=self.manual_gateway()
        if not gateway or not gateway.get('enabled'):
            self.api.send(chat_id,'روش شارژ کارت‌به‌کارت هنوز توسط مدیریت فعال نشده است.');return
        topup=self.runtime.customer.create_topup(self.owner,user_id,username,amount_minor)
        text='\n'.join(x for x in [
            '💰 شارژ کیف پول',
            'مبلغ: '+money(topup['amount_minor'],topup['currency']),
            ('شماره کارت: '+gateway['card_number']) if gateway.get('card_number') else '',
            ('به نام: '+gateway['card_holder']) if gateway.get('card_holder') else '',
            ('بانک: '+gateway['bank_name']) if gateway.get('bank_name') else '',
            gateway.get('instructions') or '',
            'بعد از واریز، تصویر یا فایل رسید را همین‌جا بفرست.'
        ] if x)
        self.api.send(chat_id,text)

    def customer_wallet_history(self,chat_id:int,user_id:int):
        rows=self.runtime.customer.ledger(self.owner,user_id,20)
        if not rows:self.api.send(chat_id,'هنوز تراکنشی در کیف پول ثبت نشده است.');return
        lines=['📜 تراکنش‌های کیف پول']
        labels={'topup':'شارژ','purchase':'خرید','renewal':'تمدید','referral':'پاداش زیرمجموعه'}
        for r in rows:
            sign='+' if int(r['delta_minor'])>=0 else ''
            when=time.strftime('%m/%d %H:%M',time.localtime(float(r['created_at'])))
            lines.append(f"• {when} · {labels.get(r['kind'],r['kind'])} · {sign}{money(r['delta_minor'],r['currency'])}")
        self.api.send(chat_id,'\n'.join(lines))

    def _customer_services(self,user_id:int)->list[dict[str,Any]]:
        return [x for x in self.runtime.manager.list(self.actor())
                if int((x.get('client') or {}).get('tgId') or 0)==int(user_id)]

    def _customer_client_row(self,user_id:int,row_id:int)->tuple[str,dict[str,Any]]:
        with self.runtime.store.lock:
            row=self.runtime.store.db.execute("SELECT rowid,id,owner FROM clients WHERE rowid=? AND owner=?",
                                              (int(row_id),self.owner)).fetchone()
        if not row:raise PolicyError('Service not found')
        detail=self.runtime.manager.detail(self.actor(),str(row['id']),credentials=True)
        if int((detail.get('client') or {}).get('tgId') or 0)!=int(user_id):raise PolicyError('Service does not belong to this Telegram account')
        return str(row['id']),detail

    def customer_services(self,chat_id:int,user_id:int):
        rows=self._customer_services(user_id)
        if not rows:self.api.send(chat_id,'هنوز سرویسی به حساب تلگرام شما متصل نیست.');return
        if len(rows)==1:
            with self.runtime.store.lock:
                row=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",
                                                  (rows[0]['email'],self.owner)).fetchone()
            self.customer_service_detail(chat_id,user_id,int(row['rowid']));return
        buttons=[]
        with self.runtime.store.lock:
            for r in rows[:30]:
                dbrow=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",
                                                    (r['email'],self.owner)).fetchone()
                if dbrow:buttons.append([{'text':'📦 '+r['email'],'callback_data':'usvc:'+str(dbrow['rowid'])}])
        self.api.send(chat_id,'📦 سرویس‌های من\nیک سرویس را انتخاب کن:',{'inline_keyboard':buttons})

    def customer_service_detail(self,chat_id:int,user_id:int,row_id:int):
        email,detail=self._customer_client_row(user_id,row_id);c=detail.get('client') or {}
        quota=int(c.get('totalGB') or 0);used=int(detail.get('used_bytes') or 0)
        left='نامحدود' if quota==0 else self.bytes(max(0,quota-used))
        expiry=int(c.get('expiryTime') or 0)
        expiry_text='بدون انقضا' if not expiry else time.strftime('%Y-%m-%d %H:%M',time.localtime(expiry/1000))
        origin=self.runtime.customer.latest_service_order(self.owner,user_id,email)
        product_name='سرویس DARK';renewal=False
        if origin:
            with self.runtime.store.lock:
                p=self.runtime.store.db.execute("SELECT name,renewal_enabled FROM commerce_products WHERE owner=? AND id=?",
                                                (self.owner,origin['product_id'])).fetchone()
            if p:product_name=str(p['name']);renewal=bool(p['renewal_enabled'])
        text=(f"📦 {product_name}\nشناسه: {email}\nباقی‌مانده: {left}\n"
              f"انقضا: {expiry_text}\nوضعیت: {'فعال ✅' if not detail.get('block_reasons') else 'محدود ⛔'}")
        buttons=[[{'text':'🔗 دریافت اتصال','callback_data':'usvclink:'+str(row_id)}]]
        if renewal:buttons[0].append({'text':'🔄 تمدید','callback_data':'usvcrenew:'+str(row_id)})
        self.api.send(chat_id,text,{'inline_keyboard':buttons})

    def customer_connection(self,chat_id:int,user_id:int,row_id:int):
        email,detail=self._customer_client_row(user_id,row_id)
        origin=self.runtime.customer.latest_service_order(self.owner,user_id,email)
        if origin:
            delivery=self.runtime.commerce.delivery_payload(origin['id'],self.runtime.manager)
            if delivery.get('subscription_url'):self.api.send(chat_id,'🔗 Subscription\n'+str(delivery['subscription_url']))
            if delivery.get('main_config'):self.api.send(chat_id,'⚡ Main Config\n'+str(delivery['main_config']))
            portal=delivery.get('portal_url')
            if portal and portal!=delivery.get('subscription_url'):self.api.send(chat_id,'🌐 Portal\n'+str(portal))
            if any(delivery.get(x) for x in ('subscription_url','main_config','portal_url')):return
        sub=str(detail.get('subscription_url') or '')
        self.api.send(chat_id,'🔗 Subscription\n'+sub if sub else 'لینک اتصال برای این سرویس موجود نیست.')

    def customer_renew_services(self,chat_id:int,user_id:int):
        rows=self._customer_services(user_id)
        if not rows:self.api.send(chat_id,'سرویسی برای تمدید وجود ندارد.');return
        buttons=[]
        with self.runtime.store.lock:
            for r in rows:
                origin=self.runtime.customer.latest_service_order(self.owner,user_id,r['email'])
                if not origin:continue
                p=self.runtime.store.db.execute("SELECT renewal_enabled FROM commerce_products WHERE owner=? AND id=?",
                                                (self.owner,origin['product_id'])).fetchone()
                if not p or not p['renewal_enabled']:continue
                dbrow=self.runtime.store.db.execute("SELECT rowid FROM clients WHERE id=? AND owner=?",(r['email'],self.owner)).fetchone()
                if dbrow:buttons.append([{'text':'🔄 '+r['email'],'callback_data':'usvcrenew:'+str(dbrow['rowid'])}])
        if not buttons:self.api.send(chat_id,'تمدید برای سرویس‌های فعلی فعال نیست.');return
        self.api.send(chat_id,'🔄 تمدید سرویس\nسرویس را انتخاب کن:',{'inline_keyboard':buttons})

    def customer_renew_options(self,chat_id:int,user_id:int,row_id:int):
        email,_=self._customer_client_row(user_id,row_id)
        try:data=self.runtime.customer.renewal_prices(self.owner,user_id,email)
        except PolicyError as ex:self.api.send(chat_id,str(ex));return
        buttons=[]
        for p in data['prices']:
            buttons.append([{'text':f"{p['label']} · {money(p['price_minor'],p['currency'])}"[:62],
                             'callback_data':f"urnp:{row_id}:{p['row_id']}"}])
        if not buttons:self.api.send(chat_id,'پلن تمدید فعالی وجود ندارد.');return
        self.api.send(chat_id,f"🔄 تمدید {email}\nپلن را انتخاب کن:",{'inline_keyboard':buttons})

    def customer_referral_menu(self,chat_id:int,user_id:int):
        stats=self.runtime.customer.referral_stats(self.owner,user_id)
        bot=str(self.bot_config().get('bot_username') or '')
        link=f"https://t.me/{bot}?start=ref_{stats['code']}" if bot else 'Bot username unavailable'
        self.api.send(chat_id,
            f"👥 زیرمجموعه‌گیری\nلینک دعوت:\n{link}\n\n"
            f"دعوت‌شده‌ها: {stats['invited']}\nخرید موفق: {stats['qualified']}\n"
            f"پاداش دریافت‌شده: {money(stats['earned'])}\n"
            f"پاداش هر اولین خرید موفق: {money(stats['reward_minor'])}",
            {'inline_keyboard':[[{'text':'🔄 بروزرسانی','callback_data':'uref'}]]})

    def customer_support_menu(self,chat_id:int,user_id:int):
        rows=self.runtime.customer.tickets_for_customer(self.owner,user_id,20)
        kb=[[{'text':'➕ تیکت جدید','callback_data':'supnew'}]]
        for t in rows[:15]:
            icon='🟢' if t['status']=='open' else ('🔵' if t['status']=='answered' else '⚫')
            kb.append([{'text':f"{icon} {t['subject']}"[:62],'callback_data':'supt:'+str(t['row_id'])}])
        self.api.send(chat_id,'🎫 پشتیبانی\nتیکت جدید بساز یا یکی از تیکت‌های قبلی را باز کن.',
                      {'inline_keyboard':kb})

    def customer_ticket_detail(self,chat_id:int,user_id:int,row_id:int):
        t=self.runtime.customer.ticket_by_rowid(self.owner,row_id)
        if int(t['telegram_id'])!=int(user_id):raise PolicyError('Ticket does not belong to this Telegram account')
        messages=self.runtime.customer.ticket_messages(self.owner,t['id'],12)
        lines=[f"🎫 {t['subject']}\nوضعیت: {t['status']}"]
        for m in messages:
            who='شما' if m['sender_type']=='customer' else 'پشتیبانی'
            body=m['text'] or ('['+m['file_kind']+']')
            lines.append(f"\n{who}: {body[:700]}")
        kb=[]
        if t['status']!='closed':
            kb=[[{'text':'✍️ پاسخ','callback_data':'supreply:'+str(row_id)},
                 {'text':'✅ بستن تیکت','callback_data':'supclose:'+str(row_id)}]]
        self.api.send(chat_id,'\n'.join(lines),{'inline_keyboard':kb} if kb else None)

    def admin_support(self,chat_id:int):
        rows=self.runtime.customer.open_tickets(self.owner,30)
        if not rows:self.api.send(chat_id,'🎫 تیکت بازی وجود ندارد.');return
        kb=[]
        for t in rows:
            kb.append([{'text':f"🎫 {t['subject']} · {t['telegram_id']}"[:62],
                        'callback_data':'asupt:'+str(t['row_id'])}])
        self.api.send(chat_id,'🎫 پشتیبانی مشتریان',{'inline_keyboard':kb})

    def admin_support_detail(self,chat_id:int,row_id:int):
        t=self.runtime.customer.ticket_by_rowid(self.owner,row_id)
        messages=self.runtime.customer.ticket_messages(self.owner,t['id'],20)
        lines=[f"🎫 {t['subject']}\nکاربر: {t['telegram_id']} @{t['username'] or '—'}\nوضعیت: {t['status']}"]
        for m in messages:
            who='مشتری' if m['sender_type']=='customer' else 'ادمین'
            lines.append(f"\n{who}: {(m['text'] or '['+m['file_kind']+']')[:700]}")
        kb=[]
        if t['status']!='closed':
            kb=[[{'text':'✍️ پاسخ','callback_data':'asupreply:'+str(row_id)},
                 {'text':'✅ بستن','callback_data':'asupclose:'+str(row_id)}]]
        self.api.send(chat_id,'\n'.join(lines),{'inline_keyboard':kb} if kb else None)

    def handle_customer_callback(self,data:str,chat_id:int,user_id:int,sender:dict[str,Any])->bool:
        if data.startswith('p:'):
            p=self.product_by_rowid(int(data.split(':',1)[1]))
            prices=[x for x in self.runtime.commerce.product_rows(self.owner,public=True) if x['id']==p['id']][0]['prices']
            buttons=[[{'text':f"{x['label']} · {money(x['price_minor'],x['currency'])}",
                       'callback_data':'b:'+str(x['row_id'])}] for x in prices if x['active']]
            self.api.send(chat_id,f"{p['name']}\n{p['description']}\nپلن را انتخاب کن:",{'inline_keyboard':buttons});return True
        if data.startswith('b:'):
            price=self.price_by_rowid(int(data.split(':',1)[1]))
            with self.runtime.store.lock:
                product=self.runtime.store.db.execute("SELECT * FROM commerce_products WHERE owner=? AND id=?",
                                                      (self.owner,price['product_id'])).fetchone()
            order=self.runtime.commerce.create_order(self.owner,user_id,str(sender.get('username') or ''),
                                                     product['id'],price['id'])
            wallet=self.runtime.customer.wallet(self.owner,user_id)
            enough=int(wallet['balance_minor'])>=int(order['amount_minor'])
            kb=[]
            if enough:kb.append([{'text':'✅ پرداخت از کیف پول','callback_data':'uwpay:'+order['id']}])
            kb.append([{'text':'💰 شارژ کیف پول','callback_data':'wmenu'}])
            self.api.send(chat_id,f"سفارش: {order['id']}\nمبلغ: {money(order['amount_minor'],order['currency'])}\n"
                          f"موجودی کیف پول: {money(wallet['balance_minor'])}",
                          {'inline_keyboard':kb});return True
        if data.startswith('g:'):
            self.api.send(chat_id,'پرداخت مستقیم غیرفعال است. برای خرید، ابتدا کیف پول را شارژ کن.');return True
        if data.startswith('uwpay:'):
            order_id=data.split(':',1)[1]
            try:
                result=self.runtime.customer.pay_purchase(self.owner,order_id)
                self.runtime.manager.audit(self.actor(),self.owner,'commerce.wallet_purchase',order_id,
                                           f"telegram={user_id}")
                self.send_delivery(chat_id,result)
            except PolicyError as ex:
                self.api.send(chat_id,'پرداخت انجام نشد: '+str(ex));return True
            except Exception as ex:
                self.api.send(chat_id,'پرداخت ثبت شد ولی ساخت سرویس نیاز به بررسی مدیریت دارد.')
                self.notify_admin('خطای ساخت سرویس بعد از پرداخت Wallet: '+str(ex)[:700]);return True
            return True
        if data=='wmenu':self.customer_wallet_menu(chat_id,user_id);return True
        if data.startswith('wtop:'):
            self.customer_start_topup(chat_id,user_id,str(sender.get('username') or ''),int(data.split(':',1)[1]));return True
        if data=='wcustom':
            self.sessions[user_id]='customer_topup_custom';self.session_data[user_id]={}
            self.api.send(chat_id,'مبلغ شارژ را به تومان بفرست.\nبرای لغو: /cancel');return True
        if data=='whist':self.customer_wallet_history(chat_id,user_id);return True
        if data.startswith('utopok:') and self.is_admin(user_id):
            top=self.runtime.customer.approve_topup(self.owner,int(data.split(':',1)[1]))
            wallet=self.runtime.customer.wallet(self.owner,int(top['telegram_id']))
            self.runtime.manager.audit(self.actor(),self.owner,'wallet.topup_approve',top['id'],str(top['amount_minor']))
            self.api.send(int(top['telegram_id']),f"✅ شارژ کیف پول تأیید شد.\nموجودی جدید: {money(wallet['balance_minor'])}")
            self.api.send(chat_id,'✅ شارژ تأیید شد.');return True
        if data.startswith('utopno:') and self.is_admin(user_id):
            top=self.runtime.customer.reject_topup(self.owner,int(data.split(':',1)[1]))
            self.api.send(int(top['telegram_id']),'❌ رسید شارژ کیف پول تأیید نشد.')
            self.api.send(chat_id,'رسید رد شد.');return True
        if data.startswith('usvc:'):
            self.customer_service_detail(chat_id,user_id,int(data.split(':',1)[1]));return True
        if data.startswith('usvclink:'):
            self.customer_connection(chat_id,user_id,int(data.split(':',1)[1]));return True
        if data.startswith('usvcrenew:'):
            self.customer_renew_options(chat_id,user_id,int(data.split(':',1)[1]));return True
        if data.startswith('urnp:'):
            _,client_row,price_row=data.split(':',2)
            email,_=self._customer_client_row(user_id,int(client_row))
            price=self.price_by_rowid(int(price_row))
            order=self.runtime.customer.create_renewal_order(self.owner,user_id,str(sender.get('username') or ''),
                                                             email,price['id'])
            wallet=self.runtime.customer.wallet(self.owner,user_id)
            enough=int(wallet['balance_minor'])>=int(order['amount_minor'])
            kb=[]
            if enough:kb.append([{'text':'✅ پرداخت و تمدید','callback_data':'urpay:'+order['id']}])
            kb.append([{'text':'💰 شارژ کیف پول','callback_data':'wmenu'}])
            self.api.send(chat_id,f"تمدید {email}\nمبلغ: {money(order['amount_minor'],order['currency'])}\n"
                          f"موجودی: {money(wallet['balance_minor'])}",{'inline_keyboard':kb});return True
        if data.startswith('urpay:'):
            order_id=data.split(':',1)[1]
            try:
                result=self.runtime.customer.pay_renewal(self.owner,order_id)
                self.runtime.manager.audit(self.actor(),self.owner,'commerce.wallet_renewal',order_id,
                                           f"telegram={user_id}; client={result['client_id']}")
                self.api.send(chat_id,'✅ سرویس با موفقیت تمدید و دوره مصرف آن ریست شد.')
            except Exception as ex:self.api.send(chat_id,'تمدید انجام نشد: '+str(ex)[:700])
            return True
        if data=='uref':self.customer_referral_menu(chat_id,user_id);return True
        if data=='supnew':
            self.sessions[user_id]='customer_support_subject';self.session_data[user_id]={}
            self.api.send(chat_id,'موضوع تیکت را بفرست.\nبرای لغو: /cancel');return True
        if data=='suplist':self.customer_support_menu(chat_id,user_id);return True
        if data.startswith('supt:'):
            self.customer_ticket_detail(chat_id,user_id,int(data.split(':',1)[1]));return True
        if data.startswith('supreply:'):
            row_id=int(data.split(':',1)[1]);t=self.runtime.customer.ticket_by_rowid(self.owner,row_id)
            if int(t['telegram_id'])!=int(user_id):raise PolicyError('Ticket does not belong to this Telegram account')
            self.sessions[user_id]='customer_support_message';self.session_data[user_id]={'ticket_id':t['id']}
            self.api.send(chat_id,'پاسخ را بفرست؛ متن، عکس یا فایل قابل قبول است.');return True
        if data.startswith('supclose:'):
            row_id=int(data.split(':',1)[1]);t=self.runtime.customer.ticket_by_rowid(self.owner,row_id)
            if int(t['telegram_id'])!=int(user_id):raise PolicyError('Ticket does not belong to this Telegram account')
            self.runtime.customer.close_ticket(self.owner,t['id']);self.api.send(chat_id,'✅ تیکت بسته شد.');return True
        if data.startswith('asupt:') and self.is_admin(user_id):
            self.admin_support_detail(chat_id,int(data.split(':',1)[1]));return True
        if data.startswith('asupreply:') and self.is_admin(user_id):
            row_id=int(data.split(':',1)[1]);t=self.runtime.customer.ticket_by_rowid(self.owner,row_id)
            self.sessions[user_id]='admin_support_reply';self.session_data[user_id]={'ticket_id':t['id']}
            self.api.send(chat_id,'پاسخ پشتیبانی را بفرست؛ متن، عکس یا فایل قابل قبول است.');return True
        if data.startswith('asupclose:') and self.is_admin(user_id):
            t=self.runtime.customer.ticket_by_rowid(self.owner,int(data.split(':',1)[1]))
            self.runtime.customer.close_ticket(self.owner,t['id'])
            self.api.send(int(t['telegram_id']),'✅ تیکت شما توسط پشتیبانی بسته شد.')
            self.api.send(chat_id,'تیکت بسته شد.');return True
        if data=='refreward' and self.is_admin(user_id):
            self.sessions[user_id]='customer_admin_referral_reward';self.session_data[user_id]={}
            self.api.send(chat_id,'پاداش اولین خرید موفق هر زیرمجموعه را به تومان بفرست. صفر = بدون پاداش.');return True
        return False

    def handle_customer_text(self,chat_id:int,user_id:int,text:str,username:str):
        state=self.sessions.get(user_id,'');value=text.strip()
        if state=='customer_topup_custom':
            try:amount=int(value.replace(',',''))
            except ValueError:self.api.send(chat_id,'مبلغ باید عدد صحیح باشد.');return
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.customer_start_topup(chat_id,user_id,username,amount);return
        if state=='customer_support_subject':
            ticket=self.runtime.customer.create_ticket(self.owner,user_id,username,value)
            self.sessions[user_id]='customer_support_message';self.session_data[user_id]={'ticket_id':ticket['id']}
            self.api.send(chat_id,'متن یا فایل اولین پیام تیکت را بفرست.');return
        if state=='customer_support_message':
            ticket_id=str((self.session_data.get(user_id) or {}).get('ticket_id') or '')
            self.runtime.customer.add_ticket_message(self.owner,ticket_id,'customer',user_id,text=value)
            ticket=self.runtime.customer.ticket(ticket_id,self.owner)
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.notify_admin(f"🎫 تیکت جدید/پاسخ مشتری\n{ticket['subject']}\nکاربر: {user_id}",
                              {'inline_keyboard':[[{'text':'باز کردن تیکت','callback_data':'asupt:'+str(ticket['row_id'])}]]})
            self.api.send(chat_id,'✅ پیام برای پشتیبانی ارسال شد.');return
        if state=='admin_support_reply' and self.is_admin(user_id):
            ticket_id=str((self.session_data.get(user_id) or {}).get('ticket_id') or '')
            self.runtime.customer.add_ticket_message(self.owner,ticket_id,'admin',user_id,text=value)
            ticket=self.runtime.customer.ticket(ticket_id,self.owner)
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.api.send(int(ticket['telegram_id']),f"🎫 پاسخ پشتیبانی\n{ticket['subject']}\n\n{value}")
            self.api.send(chat_id,'✅ پاسخ ارسال شد.');return
        if state=='customer_admin_referral_reward' and self.is_admin(user_id):
            try:reward=int(value.replace(',',''))
            except ValueError:self.api.send(chat_id,'مبلغ باید عدد صحیح باشد.');return
            settings=self.runtime.customer.set_referral_reward(self.owner,reward)
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            self.api.send(chat_id,'✅ پاداش زیرمجموعه روی '+money(settings['referral_reward_minor'])+' تنظیم شد.');return
        raise PolicyError('Unknown customer session')

    def handle_customer_media(self,chat_id:int,user_id:int,msg:dict[str,Any])->bool:
        if msg.get('photo'):
            file_id=str(msg['photo'][-1]['file_id']);kind='photo';method='sendPhoto'
        elif msg.get('document'):
            file_id=str(msg['document']['file_id']);kind='document';method='sendDocument'
        else:return False
        state=self.sessions.get(user_id,'')
        if state in ('customer_support_message','admin_support_reply'):
            ticket_id=str((self.session_data.get(user_id) or {}).get('ticket_id') or '')
            sender='admin' if state=='admin_support_reply' and self.is_admin(user_id) else 'customer'
            self.runtime.customer.add_ticket_message(self.owner,ticket_id,sender,user_id,file_kind=kind,file_id=file_id)
            ticket=self.runtime.customer.ticket(ticket_id,self.owner)
            self.sessions.pop(user_id,None);self.session_data.pop(user_id,None)
            if sender=='customer':
                admin=int(self.bot_config()['admin_telegram_id'])
                markup={'inline_keyboard':[[{'text':'باز کردن تیکت','callback_data':'asupt:'+str(ticket['row_id'])}]]}
                try:self.api.call(method,{'chat_id':admin,kind:file_id,
                                          'caption':f"🎫 {ticket['subject']}\nکاربر: {user_id}",'reply_markup':markup})
                except Exception:self.notify_admin(f"🎫 فایل جدید در تیکت\n{ticket['subject']}\nکاربر: {user_id}",markup)
                self.api.send(chat_id,'✅ فایل برای پشتیبانی ارسال شد.')
            else:
                payload={'chat_id':int(ticket['telegram_id']),kind:file_id,'caption':'🎫 پاسخ پشتیبانی · '+ticket['subject']}
                try:self.api.call(method,payload)
                except Exception:self.api.send(int(ticket['telegram_id']),'پشتیبانی یک فایل برای شما ارسال کرد.')
                self.api.send(chat_id,'✅ فایل برای مشتری ارسال شد.')
            return True
        topup=self.runtime.customer.latest_waiting_topup(self.owner,user_id)
        if not topup:return False
        top=self.runtime.customer.record_topup_receipt(self.owner,user_id,f'telegram:{kind}:{file_id}')
        caption=f"💰 رسید شارژ کیف پول\nکاربر: {user_id}\nمبلغ: {money(top['amount_minor'],top['currency'])}"
        markup={'inline_keyboard':[[{'text':'✅ تأیید شارژ','callback_data':'utopok:'+str(top['row_id'])},
                                    {'text':'❌ رد رسید','callback_data':'utopno:'+str(top['row_id'])}]]}
        routed=False
        try:routed=self.runtime.forum.report_media(self.api,self.owner,'payments',method,kind,file_id,caption,markup)
        except Exception:routed=False
        if not routed:
            admin=int(self.bot_config()['admin_telegram_id'])
            try:self.api.call(method,{'chat_id':admin,kind:file_id,'caption':caption,'reply_markup':markup})
            except Exception:self.api.send(admin,caption,markup)
        self.api.send(chat_id,'✅ رسید شارژ ثبت شد و برای مدیریت ارسال شد.')
        return True