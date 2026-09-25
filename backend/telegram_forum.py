from __future__ import annotations
import time
from datetime import datetime,timedelta,time as dt_time
from zoneinfo import ZoneInfo
from typing import Any

from dark_policy import PolicyError

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
        start=datetime.combine(day,dt_time.min,tzinfo=tz).timestamp()
        end=datetime.combine(now.date(),dt_time.min,tzinfo=tz).timestamp()
        where='created_at>=? AND created_at<?';args:list[Any]=[start,end]
        if role!='owner':where+=' AND owner=?';args.append(owner)
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute(
                f"SELECT currency,COUNT(*) orders,COALESCE(SUM(amount_minor),0) amount FROM commerce_orders WHERE {where} GROUP BY currency ORDER BY currency",tuple(args))]
            status=[dict(r) for r in self.store.db.execute(
                f"SELECT status,COUNT(*) count FROM commerce_orders WHERE {where} GROUP BY status ORDER BY status",tuple(args))]
        total=sum(int(x['orders']) for x in rows)
        money=' · '.join(f"{x['amount']:,} {x['currency']}" for x in rows) or '0'
        states=' · '.join(f"{x['status']}: {x['count']}" for x in status) or 'بدون سفارش'
        text=(f"📊 گزارش روزانه DARK · {day.isoformat()}\n"
              f"سفارش‌ها: {total}\nمبلغ ثبت‌شده: {money}\nوضعیت‌ها: {states}")
        sent=self.report(api,owner,'daily',text)
        if sent:
            with self.store.transaction() as db:
                db.execute('UPDATE telegram_forums SET last_daily_key=?,updated_at=? WHERE owner=?',
                           (current_key,time.time(),owner))
        return sent

    def poll_audits(self,api,owner:str,role:str,limit:int=40)->int:
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
            text=(f"#{row['id']} · {row.get('action','')}\n"
                  f"Actor: {row.get('actor','')}\nOwner: {row.get('owner','')}\n"
                  f"Target: {row.get('target','')}\n{row.get('detail','')}")
            self.report(api,owner,kind,text);cursor=max(cursor,int(row['id']));sent+=1
        if sent:
            with self.store.transaction() as db:
                db.execute('UPDATE telegram_forums SET last_audit_id=?,updated_at=? WHERE owner=?',
                           (cursor,time.time(),owner))
        return sent