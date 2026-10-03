from __future__ import annotations
import time
from datetime import datetime,timedelta,time as dt_time
from zoneinfo import ZoneInfo
from typing import Any

from dark_policy import PolicyError
from telegram_presentation import money,date_time,ORDER_STATES
from telegram_reporting import ORDERS_CTE,sales_summary

FORUM_REQUEST_ID=730001
TOPICS={
    'sales':'💰 فروش',
    'payments':'💳 پرداخت‌ها',
    'services':'📦 سرویس‌ها',
    'errors':'🚨 خطاها',
    'system':'🖥 سیستم و نودها',
    'backups':'💾 بکاپ‌ها',
    'security':'🔐 امنیت',
    'daily':'📊 گزارش روزانه',
}

def admin_rights(*,user:bool=False)->dict[str,bool]:
    # ChatAdministratorRights currently requires the base boolean fields.
    # The user must have a superset of the rights requested for the bot.
    return {
        'is_anonymous':False,
        'can_manage_chat':True,
        'can_delete_messages':False,
        'can_manage_video_chats':False,
        'can_restrict_members':False,
        'can_promote_members':False,
        'can_change_info':False,
        'can_invite_users':False,
        'can_post_stories':False,
        'can_edit_stories':False,
        'can_delete_stories':False,
        'can_pin_messages':False,
        'can_manage_topics':True,
        'can_manage_tags':False,
        'can_send_welcome_messages':False,
    }

class TelegramForumCenter:
    def __init__(self,store):
        self.store=store
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS telegram_forums(
              owner TEXT PRIMARY KEY,
              chat_id INTEGER NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1,
              configured_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              last_audit_id INTEGER NOT NULL DEFAULT 0,
              last_daily_key TEXT NOT NULL DEFAULT '',
              rebind_required INTEGER NOT NULL DEFAULT 0,
              rebind_reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS telegram_forum_topics(
              owner TEXT NOT NULL,
              kind TEXT NOT NULL,
              name TEXT NOT NULL,
              thread_id INTEGER NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY(owner,kind));
            """)
            columns={r[1] for r in store.db.execute('PRAGMA table_info(telegram_forums)')}
            if 'rebind_required' not in columns:
                store.db.execute("ALTER TABLE telegram_forums ADD COLUMN rebind_required INTEGER NOT NULL DEFAULT 0")
            if 'rebind_reason' not in columns:
                store.db.execute("ALTER TABLE telegram_forums ADD COLUMN rebind_reason TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def request_keyboard()->dict[str,Any]:
        return {'keyboard':[[
            {'text':'🛡 اتصال انجمن مدیریت DARK','request_chat':{
                'request_id':FORUM_REQUEST_ID,
                'chat_is_channel':False,
                'chat_is_forum':True,
                'user_administrator_rights':admin_rights(user=True),
                'bot_administrator_rights':admin_rights(),
                'request_title':True,
                'request_username':True,
            }}
        ]],'resize_keyboard':True,'one_time_keyboard':True}

    def status(self,owner:str)->dict[str,Any]:
        with self.store.lock:
            forum=self.store.db.execute('SELECT * FROM telegram_forums WHERE owner=?',(owner,)).fetchone()
            topics=[dict(r) for r in self.store.db.execute(
                'SELECT kind,name,thread_id,updated_at FROM telegram_forum_topics WHERE owner=? ORDER BY kind',(owner,))]
        if not forum:return {'configured':False,'preserved':False,'rebind_required':False,
                             'chat_id':None,'title':'','topics':[]}
        rebind=bool(forum['rebind_required'])
        return {'configured':bool(forum['enabled']) and not rebind,'preserved':True,
                'rebind_required':rebind,'rebind_reason':forum['rebind_reason'],
                'chat_id':int(forum['chat_id']),'title':forum['title'],
                'enabled':bool(forum['enabled']),'topics':topics,'updated_at':forum['updated_at']}

    def _verify(self,api,chat_id:int,bot_user_id:int)->dict[str,Any]:
        chat=api.call('getChat',{'chat_id':int(chat_id)})
        if not chat.get('is_forum'):raise PolicyError('Selected chat must be a Telegram forum supergroup')
        member=api.call('getChatMember',{'chat_id':int(chat_id),'user_id':int(bot_user_id)})
        status=str(member.get('status') or '')
        if status not in ('administrator','creator'):
            raise PolicyError('DARK Bot must be an administrator in the selected forum')
        if status!='creator' and not member.get('can_manage_topics'):
            raise PolicyError('DARK Bot needs can_manage_topics in the forum')
        return chat

    def setup(self,api,owner:str,chat_shared:dict[str,Any],bot_user_id:int)->dict[str,Any]:
        if int(chat_shared.get('request_id') or 0)!=FORUM_REQUEST_ID:
            raise PolicyError('Unexpected Telegram chat selection request')
        chat_id=int(chat_shared.get('chat_id') or 0)
        if not chat_id:raise PolicyError('Telegram did not return a forum chat ID')
        chat=self._verify(api,chat_id,bot_user_id)
        now=time.time()
        with self.store.lock:
            max_audit=int(self.store.db.execute('SELECT COALESCE(MAX(id),0) FROM live_audit').fetchone()[0])
            old=self.store.db.execute('SELECT chat_id FROM telegram_forums WHERE owner=?',(owner,)).fetchone()
            existing={r['kind']:dict(r) for r in self.store.db.execute(
                'SELECT kind,name,thread_id FROM telegram_forum_topics WHERE owner=?',(owner,))}
        if not old or int(old['chat_id'])!=chat_id:
            existing={}
        created={}
        for kind,name in TOPICS.items():
            if kind in existing:
                created[kind]=int(existing[kind]['thread_id']);continue
            topic=api.call('createForumTopic',{'chat_id':chat_id,'name':name})
            created[kind]=int(topic['message_thread_id'])
        with self.store.transaction() as db:
            db.execute("""INSERT INTO telegram_forums(owner,chat_id,title,enabled,configured_at,updated_at,last_audit_id,last_daily_key,
              rebind_required,rebind_reason)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET chat_id=excluded.chat_id,title=excluded.title,
              enabled=1,updated_at=excluded.updated_at,last_audit_id=excluded.last_audit_id,
              last_daily_key=excluded.last_daily_key,rebind_required=0,rebind_reason=''""",
              (owner,chat_id,str(chat.get('title') or chat_shared.get('title') or ''),1,now,now,max_audit,
               time.strftime('%Y-%m-%d',time.localtime(now)),0,''))
            if not old or int(old['chat_id'])!=chat_id:
                db.execute('DELETE FROM telegram_forum_topics WHERE owner=?',(owner,))
            for kind,name in TOPICS.items():
                db.execute("""INSERT INTO telegram_forum_topics(owner,kind,name,thread_id,updated_at)
                  VALUES(?,?,?,?,?) ON CONFLICT(owner,kind) DO UPDATE SET name=excluded.name,
                  thread_id=excluded.thread_id,updated_at=excluded.updated_at""",
                  (owner,kind,name,created[kind],now))
        self.report(api,owner,'system','✅ DARK Report Center connected. Topic routing is active.')
        return self.status(owner)

    def repair(self,api,owner:str,bot_user_id:int)->dict[str,Any]:
        st=self.status(owner)
        if st.get('rebind_required'):raise PolicyError('Recovered forum must be rebound to the new bot first')
        if not st['configured']:raise PolicyError('Forum report center is not configured')
        chat_id=int(st['chat_id']);self._verify(api,chat_id,bot_user_id)
        known={x['kind']:x for x in st['topics']}
        now=time.time()
        for kind,name in TOPICS.items():
            row=known.get(kind);valid=False
            if row:
                try:
                    api.call('sendMessage',{'chat_id':chat_id,'message_thread_id':int(row['thread_id']),
                                            'text':'🧪 DARK topic health check','disable_notification':True})
                    valid=True
                except Exception:valid=False
            if not valid:
                topic=api.call('createForumTopic',{'chat_id':chat_id,'name':name})
                with self.store.transaction() as db:
                    db.execute("""INSERT INTO telegram_forum_topics(owner,kind,name,thread_id,updated_at)
                      VALUES(?,?,?,?,?) ON CONFLICT(owner,kind) DO UPDATE SET name=excluded.name,
                      thread_id=excluded.thread_id,updated_at=excluded.updated_at""",
                      (owner,kind,name,int(topic['message_thread_id']),now))
        with self.store.transaction() as db:
            db.execute('UPDATE telegram_forums SET updated_at=? WHERE owner=?',(now,owner))
        return self.status(owner)

    def rebind_existing(self,api,owner:str,bot_user_id:int)->dict[str,Any]:
        st=self.status(owner)
        if not st.get('preserved'):raise PolicyError('No recovered forum is available to rebind')
        chat_id=int(st['chat_id'])
        chat=self._verify(api,chat_id,bot_user_id)
        known={x['kind']:x for x in st['topics']}
        now=time.time();created={}
        for kind,name in TOPICS.items():
            row=known.get(kind);valid=False
            if row:
                try:
                    api.call('sendMessage',{'chat_id':chat_id,'message_thread_id':int(row['thread_id']),
                                            'text':'🧪 DARK recovery topic verification','disable_notification':True})
                    valid=True;created[kind]=int(row['thread_id'])
                except Exception:valid=False
            if not valid:
                topic=api.call('createForumTopic',{'chat_id':chat_id,'name':name})
                created[kind]=int(topic['message_thread_id'])
        with self.store.transaction() as db:
            db.execute("""UPDATE telegram_forums SET title=?,enabled=1,rebind_required=0,rebind_reason='',updated_at=?
              WHERE owner=?""",(str(chat.get('title') or st.get('title') or ''),now,owner))
            for kind,name in TOPICS.items():
                db.execute("""INSERT INTO telegram_forum_topics(owner,kind,name,thread_id,updated_at)
                  VALUES(?,?,?,?,?) ON CONFLICT(owner,kind) DO UPDATE SET name=excluded.name,
                  thread_id=excluded.thread_id,updated_at=excluded.updated_at""",
                  (owner,kind,name,created[kind],now))
        self.report(api,owner,'system','✅ DARK Report Center rebound after disaster recovery. Topic routing is active.')
        return self.status(owner)

    def _target(self,owner:str,kind:str)->tuple[int,int]|None:
        with self.store.lock:
            row=self.store.db.execute("""SELECT f.chat_id,t.thread_id FROM telegram_forums f
              JOIN telegram_forum_topics t ON t.owner=f.owner
              WHERE f.owner=? AND f.enabled=1 AND COALESCE(f.rebind_required,0)=0 AND t.kind=?""",(owner,kind)).fetchone()
        return (int(row['chat_id']),int(row['thread_id'])) if row else None

    def report(self,api,owner:str,kind:str,text:str,reply_markup:dict|None=None)->bool:
        target=self._target(owner,kind)
        if not target:return False
        chat_id,thread_id=target
        body={'chat_id':chat_id,'message_thread_id':thread_id,'text':str(text)[:4000]}
        if reply_markup is not None:body['reply_markup']=reply_markup
        api.call('sendMessage',body);return True

    def report_media(self,api,owner:str,kind:str,method:str,key:str,file_id:str,caption:str,
                     reply_markup:dict|None=None)->bool:
        target=self._target(owner,kind)
        if not target:return False
        chat_id,thread_id=target
        body={'chat_id':chat_id,'message_thread_id':thread_id,key:file_id,'caption':caption[:1000]}
        if reply_markup is not None:body['reply_markup']=reply_markup
        api.call(method,body);return True

    @staticmethod
    def audit_kind(action:str)->str:
        a=str(action or '').lower()
        if a.startswith('backup.'):return 'backups'
        if a.startswith('auth.') or a.startswith('telegram.'):return 'security'
        if a.startswith('wallet.'):return 'payments'
        if a=='commerce.first_connection_activate':return 'services'
        if a.startswith('commerce.payment'):return 'payments'
        if a.startswith('commerce.') or a.startswith('representative.'):return 'sales'
        if a.startswith('client.'):return 'services'
        if 'error' in a or 'fail' in a:return 'errors'
        if a.startswith('node.') or a.startswith('system.'):return 'system'
        return 'system'

    def maybe_daily_summary(self,api,owner:str,role:str,timezone_name:str='UTC')->bool:
        try:tz=ZoneInfo(str(timezone_name or 'UTC'))
        except Exception:tz=ZoneInfo('UTC')
        now=datetime.now(tz);current_key=now.strftime('%Y-%m-%d')
        with self.store.lock:
            forum=self.store.db.execute('SELECT last_daily_key FROM telegram_forums WHERE owner=? AND enabled=1 AND COALESCE(rebind_required,0)=0',(owner,)).fetchone()
        if not forum or str(forum['last_daily_key'] or '')==current_key:return False
        day=now.date()-timedelta(days=1)
        text=self.daily_summary_text(owner,day,timezone_name)
        sent=self.report(api,owner,'daily',text)
        if sent:
            with self.store.transaction() as db:
                db.execute('UPDATE telegram_forums SET last_daily_key=?,updated_at=? WHERE owner=?',
                           (current_key,time.time(),owner))
        return sent

    def daily_summary_text(self,owner:str,day,timezone_name:str='UTC')->str:
        try:tz=ZoneInfo(str(timezone_name or 'UTC'))
        except Exception:tz=ZoneInfo('UTC')
        start=datetime.combine(day,dt_time.min,tzinfo=tz).timestamp()
        end=datetime.combine(day+timedelta(days=1),dt_time.min,tzinfo=tz).timestamp()
        with self.store.lock:
            db=self.store.db
            sales=sales_summary(db,owner,start,end)
            status=[dict(r) for r in db.execute(ORDERS_CTE+"""SELECT status,COUNT(*) count FROM orders
              WHERE owner=? AND created_at>=? AND created_at<? GROUP BY status ORDER BY status""",(owner,start,end))]
            unpaid=[dict(r) for r in db.execute(ORDERS_CTE+"""SELECT currency,SUM(amount_minor) amount FROM orders
              WHERE owner=? AND created_at>=? AND created_at<?
              AND status IN ('pending','awaiting_payment','payment_review','payment_rejected')
              GROUP BY currency ORDER BY currency""",(owner,start,end))]
            topups=[dict(r) for r in db.execute("""SELECT currency,SUM(delta_minor) amount FROM customer_wallet_ledger
              WHERE owner=? AND kind='topup' AND delta_minor>0 AND created_at>=? AND created_at<?
              GROUP BY currency ORDER BY currency""",(owner,start,end))]
        totals=lambda rows:' · '.join(money(r['amount'],r['currency']) for r in rows) or money(0)
        revenue=totals([{'currency':c,'amount':v} for c,v in sales['amounts'].items()])
        lines=[f'📊 DARK | گزارش روزانه',f'فروشگاه: {owner}',f'تاریخ: {day.isoformat()} ({tz})','',
               f"🧾 سفارش‌ها: {sum(r['count'] for r in status)}",
               f"✅ پرداخت‌های موفق این روز: {sales['count']}",f'💰 فروش قطعی: {revenue}',
               f'⏳ مبلغ سفارش‌های پرداخت‌نشدهٔ این روز: {totals(unpaid)}',
               f'💳 شارژ کیف پول این روز: {totals(topups)}',
               'شارژ کیف پول و سفارش پرداخت‌نشده در فروش قطعی حساب نمی‌شوند.','',
               '📦 وضعیت سفارش‌های ایجادشده در این روز:']
        lines.extend(f"• {ORDER_STATES.get(r['status'],'نیازمند بررسی')}: {r['count']}" for r in status)
        if not status:lines.append('بدون سفارش جدید')
        return '\n'.join(lines)

    def audit_text(self,row:dict,timezone_name:str='UTC')->str:
        action=str(row.get('action') or '');owner=str(row.get('owner') or '')
        target=str(row.get('target') or '');actor=str(row.get('actor') or '')
        titles={
            'auth.login':'🔐 ورود به پنل','auth.logout':'🔐 خروج از پنل',
            'client.create':'📦 سرویس جدید ساخته شد','client.update':'📝 مشخصات سرویس تغییر کرد',
            'client.delete':'🗑 سرویس حذف شد','commerce.payment_confirm':'✅ پرداخت سفارش تأیید شد',
            'commerce.wallet_purchase':'💰 خرید موفق از کیف پول','commerce.wallet_renewal':'🔄 تمدید موفق از کیف پول',
            'commerce.order_create':'🧾 سفارش جدید ثبت شد','commerce.order_provision':'📦 سرویس سفارش تحویل شد',
            'commerce.payment_start':'💳 پرداخت سفارش آغاز شد','wallet.topup_approve':'✅ شارژ کیف پول تأیید شد',
            'wallet.topup_reject':'⛔ رسید شارژ رد شد','backup.full':'💾 بکاپ کامل ساخته شد',
            'backup.telegram_sent':'💾 بکاپ در تلگرام تحویل شد','representative.bot_create':'🤝 نماینده جدید ساخته شد',
            'telegram.forum_setup':'📊 مرکز گزارش متصل شد','telegram.forum_repair':'📊 مرکز گزارش بررسی و ترمیم شد',
            'telegram.bot_surface_repair':'🤖 تنظیمات و منوی ربات ترمیم شد',
        }
        family={'telegram':'🤖 تنظیمات یا عملیات ربات','commerce':'🛍 عملیات فروشگاه',
                'auth':'🔐 رویداد امنیت حساب','client':'📦 عملیات سرویس','wallet':'💳 عملیات کیف پول',
                'representative':'🤝 عملیات نماینده','node':'🌍 عملیات نود','system':'🖥 عملیات سیستم','backup':'💾 عملیات بکاپ'}
        title=titles.get(action,family.get(action.split('.')[0],'🖥 عملیات پنل'))
        lines=[title,f"شماره رویداد: #{row['id']}",f'مدیر: {actor}',f'پنل: {owner}']
        if action.startswith('client.'):lines.append('سرویس: '+target)
        elif action.startswith('auth.'):lines.append('حساب: '+target)
        elif target:lines.append('شناسه: '+target)
        with self.store.lock:
            order=self.store.db.execute("""SELECT o.*,p.name product_name FROM commerce_orders o
              LEFT JOIN commerce_products p ON p.owner=o.owner AND p.id=o.product_id
              WHERE o.owner=? AND o.id=?""",(owner,target)).fetchone()
            topup=self.store.db.execute('SELECT * FROM customer_topups WHERE owner=? AND id=?',(owner,target)).fetchone() if action.startswith('wallet.') else None
        if order:
            lines+=['پلن: '+str(order['product_name'] or order['product_id']),
                    'مبلغ سفارش: '+money(order['amount_minor'],order['currency']),
                    'مشتری: '+(('@'+order['buyer_username']+' · ') if order['buyer_username'] else '')+str(order['buyer_telegram_id']),
                    'وضعیت: '+ORDER_STATES.get(order['status'],'نیازمند بررسی')]
        if topup:lines+=['مبلغ شارژ: '+money(topup['amount_minor'],topup['currency']),'مشتری: '+str(topup['telegram_id'])]
        if action=='client.create':lines.append('سرویس در پنل ثبت شد؛ اطلاعات اتصال از بخش سرویس‌ها در دسترس است.')
        if action=='client.update':
            names={'expiryTime':'زمان انقضا','totalGB':'سقف حجم','enable':'وضعیت فعال بودن','limitIp':'تعداد IP','limitHwid':'تعداد دستگاه'}
            changed=[names[k] for k in str(row.get('detail') or '').split(',') if k in names]
            if changed:lines.append('تغییر: '+'، '.join(changed))
        if row.get('at'):lines.append('زمان: '+date_time(row['at'],timezone_name))
        return '\n'.join(lines)

    def poll_audits(self,api,owner:str,role:str,limit:int=40,timezone_name:str='UTC')->int:
        with self.store.lock:
            forum=self.store.db.execute('SELECT last_audit_id FROM telegram_forums WHERE owner=? AND enabled=1 AND COALESCE(rebind_required,0)=0',(owner,)).fetchone()
            if not forum:return 0
            cursor=int(forum['last_audit_id'] or 0)
            if role=='owner':
                rows=[dict(r) for r in self.store.db.execute(
                    'SELECT * FROM live_audit WHERE id>? ORDER BY id LIMIT ?',(cursor,limit))]
            else:
                rows=[dict(r) for r in self.store.db.execute(
                    'SELECT * FROM live_audit WHERE id>? AND owner=? ORDER BY id LIMIT ?',(cursor,owner,limit))]
        sent=0
        for row in rows:
            kind=self.audit_kind(row.get('action',''))
            # Activation is already delivered with the customer's complete activation message.
            if row.get('action')!='commerce.first_connection_activate':
                self.report(api,owner,kind,self.audit_text(row,timezone_name));sent+=1
            cursor=max(cursor,int(row['id']))
            # Commit each completed delivery, so a later network failure cannot replay it.
            with self.store.transaction() as db:
                db.execute('UPDATE telegram_forums SET last_audit_id=?,updated_at=? WHERE owner=?',
                           (cursor,time.time(),owner))
        return sent
