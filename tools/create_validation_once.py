#!/usr/bin/env python3
from pathlib import Path
p=Path('backend/manager.py');s=p.read_text(encoding='utf-8')
old="""    def _stage_create_locked(self,actor:Actor,owner:str,client:dict,ids:list[int],
                             existing:set[str],reserved:set[str],*,inbounds_checked:bool=False)->str:
        actor.require('clients','create',owner)
        if not self.engine.config.writes_enabled:raise PolicyError('CoreEngine writes are disabled')
        data=self.validate_client(client);email=data['email'].lower()
"""
new="""    def _stage_create_locked(self,actor:Actor,owner:str,client:dict,ids:list[int],
                             existing:set[str],reserved:set[str],*,inbounds_checked:bool=False,validated:bool=False)->str:
        actor.require('clients','create',owner)
        if not self.engine.config.writes_enabled:raise PolicyError('CoreEngine writes are disabled')
        data=dict(client) if validated else self.validate_client(client);email=data['email'].lower()
"""
if old not in s:raise SystemExit('stage-create anchor changed')
s=s.replace(old,new,1)
old2="""    def create(self, actor: Actor, owner: str, client: dict, ids: list[int]) -> dict:
        with self.lock:
            self.check_inbounds(actor,owner,ids)
            existing,reserved=self._creation_sets_locked()
            email=self._stage_create_locked(actor,owner,client,ids,existing,reserved,inbounds_checked=True)
            self.tick(suppress=True)
            return self.detail(actor,email)
"""
new2="""    def create(self, actor: Actor, owner: str, client: dict, ids: list[int]) -> dict:
        # Preserve the public API contract: malformed client payloads are rejected
        # before owner/inbound authorization is evaluated.
        data=self.validate_client(client)
        with self.lock:
            existing,reserved=self._creation_sets_locked()
            email=self._stage_create_locked(actor,owner,data,ids,existing,reserved,inbounds_checked=False,validated=True)
            self.tick(suppress=True)
            return self.detail(actor,email)
"""
if old2 not in s:raise SystemExit('single-create anchor changed')
s=s.replace(old2,new2,1)
p.write_text(s,encoding='utf-8')
