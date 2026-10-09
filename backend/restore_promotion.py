"""DARK RESTORE V5 promotion: preserve identity/history while moving a Restore user into a native representative."""
from __future__ import annotations
import json
import time
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from dark_policy import PolicyError


class RestorePromoteBody(BaseModel):
    ids:list[str]=Field(min_length=1,max_length=500)
    representativeId:str=Field(min_length=1,max_length=128)


class RestorePromotionMixin:
    def representative_catalog(self)->list[dict[str,Any]]:
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT a.id,a.disabled,p.name,p.allowed,
              p.max_client_ips,p.max_client_hwid,o.volume_credit_bytes,o.unlimited_credit,o.unlimited_spent,o.max_clients
              FROM api_admins a JOIN owner_profiles p ON p.id=a.id JOIN owners o ON o.id=a.id
              WHERE a.role='reseller' ORDER BY p.name,a.id""")]
            for row in rows:
                row['allowed']=json.loads(row['allowed'])
                row['enabled']=not bool(row.pop('disabled'))
                allocated_volume=int(self.store.db.execute(
                    "SELECT COALESCE(SUM(quota_bytes),0) FROM clients WHERE owner=? AND quota_bytes>0",(row['id'],)).fetchone()[0])
                allocated_unlimited=int(self.store.db.execute(
                    "SELECT COUNT(*) FROM clients WHERE owner=? AND quota_bytes=0",(row['id'],)).fetchone()[0])
                row['remaining_volume_bytes']=max(0,int(row['volume_credit_bytes'])-allocated_volume)
                row['remaining_unlimited']=max(0,int(row['unlimited_credit'])-int(row['unlimited_spent']))
                row['client_count']=int(self.store.db.execute(
                    "SELECT COUNT(*) FROM clients WHERE owner=?",(row['id'],)).fetchone()[0])
        return rows

    def _promote_one(self,restore_id:str,owner_id:str,actor)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM restore_subscriptions WHERE id=?",(restore_id,)).fetchone()
            account=self.store.db.execute("SELECT role,disabled FROM api_admins WHERE id=?",(owner_id,)).fetchone()
            profile=self.store.db.execute("SELECT allowed,prefix,max_client_ips,max_client_hwid FROM owner_profiles WHERE id=?",(owner_id,)).fetchone()
            core=self.store.db.execute("SELECT body FROM core_clients WHERE email=?",(row['core_email'],)).fetchone() if row else None
        if not row:raise PolicyError('Restore user not found')
        if float(row['deleted_at'] or 0)>0:raise PolicyError('Archived Restore user cannot be promoted')
        if float(row['promoted_at'] or 0)>0:raise PolicyError('Restore user was already promoted')
        if not account or account['role']!='reseller' or account['disabled']:raise PolicyError('Representative is unavailable')
        if not profile:raise PolicyError('Representative profile is unavailable')
        if not core:raise PolicyError('Restore Core identity is missing')
        allowed={int(x) for x in json.loads(profile['allowed'])}
        inbound_ids=[int(x) for x in json.loads(row['inbound_ids'])]
        if not set(inbound_ids)<=allowed:raise PolicyError('Representative does not allow all Restore Inbounds')
        body=json.loads(core['body'])
        original_email=str(row['core_email']);native_email=original_email
        prefix=str(profile['prefix'] or '').strip().lower()
        if prefix and not original_email.lower().startswith(prefix):
            stem=(prefix+'restore_'+restore_id.removeprefix('rst_')[:16])[:120]
            with self.store.lock:
                occupied={str(r[0]) for r in self.store.db.execute(
                    "SELECT email FROM core_clients WHERE email<>? UNION SELECT id FROM clients WHERE id<>?",
                    (original_email,original_email))}
            native_email=stem
            suffix=1
            while native_email in occupied:
                tail='_'+str(suffix);native_email=(stem[:128-len(tail)]+tail);suffix+=1
            body['email']=native_email
        legacy_used=int(row['legacy_upload'])+int(row['legacy_download'])
        if int(row['legacy_total'])>0:
            # Native quota=0 means unlimited, so an exhausted limited Restore user
            # must retain a positive limited quota and remain blocked by its baseline usage.
            body['totalGB']=max(1,int(row['legacy_total'])-legacy_used)
        else:
            body['totalGB']=0
        ip_cap=int(profile['max_client_ips'] or 0);hwid_cap=int(profile['max_client_hwid'] or 0)
        current_ip=int(body.get('limitIp') or 0);current_hwid=int(body.get('limitHwid') or 0)
        if ip_cap and (current_ip==0 or current_ip>ip_cap):body['limitIp']=ip_cap
        if hwid_cap and (current_hwid==0 or current_hwid>hwid_cap):body['limitHwid']=hwid_cap
        # The native policy baseline will account for DARK bytes already consumed.
        # A representative prefix is a durable naming policy. Preserve UUID/password
        # while renaming only the internal client label when the imported label does
        # not satisfy that prefix.
        with self.store.transaction() as db:
            if native_email!=original_email:
                db.execute("UPDATE core_clients SET email=?,body=? WHERE email=?",
                           (native_email,json.dumps(body),original_email))
                db.execute("UPDATE restore_subscriptions SET core_email=? WHERE id=?",(native_email,restore_id))
            else:
                db.execute("UPDATE core_clients SET body=? WHERE email=?",(json.dumps(body),original_email))
        with self.store.lock:
            usage_row=self.store.db.execute(
                "SELECT COALESCE(SUM(up+down),0) FROM restore_usage WHERE restore_id=?",(restore_id,)).fetchone()
            dark_used=int(usage_row[0] or 0)
        try:
            result=self.manager.adopt(actor,owner_id,native_email)
            # Manager adoption baselines local Core counters. Restore accounting
            # may also contain already-consumed remote Node bytes, so raise the
            # native used baseline to the complete DARK usage before promotion.
            with self.store.transaction() as db:
                current=db.execute("SELECT used_bytes FROM clients WHERE id=?",(native_email,)).fetchone()
                if not current:raise PolicyError('Native policy row missing after adoption')
                if dark_used>int(current['used_bytes'] or 0):
                    db.execute("UPDATE clients SET used_bytes=? WHERE id=?",(dark_used,native_email))
        except Exception:
            # Restore the original Core label/body if native adoption did not commit.
            with self.store.transaction() as db:
                if native_email!=original_email:
                    db.execute("UPDATE core_clients SET email=?,body=? WHERE email=?",
                               (original_email,core['body'],native_email))
                    db.execute("UPDATE restore_subscriptions SET core_email=? WHERE id=?",(original_email,restore_id))
                else:
                    db.execute("UPDATE core_clients SET body=? WHERE email=?",(core['body'],original_email))
            raise
        now=time.time()
        try:
            with self.store.transaction() as db:
                db.execute("""UPDATE restore_subscriptions SET promoted_owner=?,promoted_at=?,updated_at=?
                  WHERE id=? AND promoted_at=0 AND deleted_at=0""",(owner_id,now,now,restore_id))
                if db.execute("SELECT changes()").fetchone()[0]!=1:
                    raise PolicyError('Restore promotion state changed')
                db.execute("INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)",
                           (restore_id,'promoted.native',json.dumps({'owner':owner_id,'client_id':native_email,'dark_used_baseline':dark_used}),now))
        except Exception:
            # Adoption is durable; fail closed by surfacing the inconsistency instead of deleting a native client.
            raise HTTPException(409,'Native client was adopted but Restore promotion marker could not be saved; inspect before retrying')
        return {'id':restore_id,'client_id':native_email,'representative_id':owner_id,
                'quota_bytes':int((result.get('client') or {}).get('totalGB') or 0),'promoted_at':now}

    def promote(self,ids:list[str],owner_id:str,actor)->dict[str,Any]:
        if actor.role!='owner':raise PolicyError('Only the primary owner can promote Restore users')
        if self.manager is None:raise PolicyError('Restore promotion requires Manager runtime')
        if len(set(ids))!=len(ids):raise PolicyError('Duplicate Restore user IDs')
        items=[];promoted=0
        # Best effort is intentional: representative resource credit may fit only part of a selected batch.
        for rid in ids:
            try:
                with self._import_lock:
                    result=self._promote_one(str(rid),owner_id,actor)
                items.append({'id':rid,'ok':True,'result':result});promoted+=1
            except Exception as ex:
                items.append({'id':rid,'ok':False,'error':str(ex)[:300]})
        return {'requested':len(ids),'promoted':promoted,'failed':len(ids)-promoted,'items':items}

    def install_promotion_routes(self,app,owner,writable,audit):
        @app.get('/api/dark-restore/representatives')
        def representatives(p=Depends(owner)):
            return self.representative_catalog()

        @app.post('/api/dark-restore/promote')
        def promote(body:RestorePromoteBody,p=Depends(owner)):
            writable();result=self.promote(body.ids,body.representativeId,p.actor)
            audit(p.actor,p.actor.id,'dark_restore.promote',body.representativeId,
                  f"requested={result['requested']}; promoted={result['promoted']}; failed={result['failed']}")
            return result
