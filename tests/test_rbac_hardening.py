import json

import pytest

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,PermissionDenied,PolicyError,Store
from manager import Manager

OWNER=Actor('dark','owner',{})


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime')
    manager=Manager(store,engine)
    auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','OwnerPass88')
    manager.owner_put(OWNER,'seller',name='Seller',allowed=[])
    yield store,engine,manager,auth
    manager.close();engine.close();store.close()


def test_finance_mutation_permissions_cannot_be_delegated(env):
    _,_,_,auth=env
    with pytest.raises(PolicyError,match='owner-only'):
        auth.admin_create(OWNER,'seller','SellerPass88','reseller',{'finance.credit':'own'})
    auth.admin_create(OWNER,'seller','SellerPass88','reseller',{'finance.read':'own','api.manage':'own'})
    with pytest.raises(PolicyError,match='owner-only'):
        auth.admin_edit(OWNER,'seller',permissions={'finance.read':'own','finance.refund':'own'})


def test_legacy_finance_grants_are_stripped_from_reseller_sessions(env):
    store,_,_,auth=env
    auth.admin_create(OWNER,'seller','SellerPass88','reseller',{'finance.read':'own','api.manage':'own'})
    with store.transaction() as db:
        db.execute('UPDATE api_admins SET permissions=? WHERE id=?',
                   (json.dumps({'finance.read':'own','finance.credit':'own','finance.refund':'own','api.manage':'own'}),'seller'))
    token,p=auth.login('seller','SellerPass88','','127.0.0.2',3600,'test')
    assert p.actor.permissions.get('finance.read')=='own'
    assert 'finance.credit' not in p.actor.permissions and 'finance.refund' not in p.actor.permissions
    current=auth.current(token,None)
    assert 'finance.credit' not in current.actor.permissions and 'finance.refund' not in current.actor.permissions
    with pytest.raises(PermissionDenied):
        store.credit(current.actor,'seller',1,'seller-self-credit-0001')


def test_robot_keys_cannot_mutate_money_or_manage_key_lifecycle(env):
    _,_,_,auth=env
    token,p=auth.login('dark','OwnerPass88','','127.0.0.1',3600,'test')
    with pytest.raises(PolicyError,match='cannot manage key lifecycle'):
        auth.new_key(p,'bad-credit',{'finance.credit':'all'},30)
    with pytest.raises(PolicyError,match='cannot manage key lifecycle'):
        auth.new_key(p,'bad-refund',{'finance.refund':'all'},30)
    with pytest.raises(PolicyError,match='cannot manage key lifecycle'):
        auth.new_key(p,'bad-key-admin',{'api.manage':'all'},30)


def test_owner_can_still_credit_reseller(env):
    store,_,_,auth=env
    token,p=auth.login('dark','OwnerPass88','','127.0.0.1',3600,'test')
    assert p.actor.role=='owner'
    assert store.credit(p.actor,'seller',25,'owner-credit-event-0001') is True
    assert store.owner_stats(p.actor,'seller')['credit']==25
