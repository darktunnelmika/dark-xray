import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from dark_policy import Store, Actor, PolicyError
from unlimited_credit import DAY, MONTH, units_for_days, change, plan_change

OWNER=Actor('dark','owner')

@pytest.fixture
def wallet(tmp_path):
    store=Store(tmp_path/'credit.sqlite3')
    store.db.execute("INSERT INTO api_admins(id,role,password_hash,permissions) VALUES('rep','reseller','','{}')")
    store.register_owner(OWNER,'rep',unlimited_credit=500,volume_credit_bytes=10000)
    yield store
    store.close()

def balance(store):
    return store.owner_stats(OWNER,'rep')['unlimited_credit_remaining']

def make(store,name='service',ips=5,days=60):
    end=int(time.time())+days*DAY
    store.register_client(OWNER,name,'rep',ips,0,expires_at=end)
    return end

@pytest.mark.parametrize('ips',range(1,6))
@pytest.mark.parametrize('months',range(1,4))
def test_user_month_matrix(wallet,ips,months):
    make(wallet,ips=ips,days=months*30)
    assert balance(wallet)==500-ips*months

def test_delete_disable_reset_do_not_refund(wallet):
    make(wallet)
    wallet.edit_client(OWNER,'service',manual=True)
    wallet.reset_client_usage(OWNER,'service')
    wallet.reset_owner_period(OWNER,'rep')
    assert balance(wallet)==490
    wallet.delete_client(OWNER,'service')
    assert balance(wallet)==490

def test_renewal_and_repeat_patch(wallet):
    end=make(wallet)
    wallet.edit_client(OWNER,'service',expires_at=end+30*DAY)
    assert balance(wallet)==485
    wallet.edit_client(OWNER,'service',expires_at=end+30*DAY)
    assert balance(wallet)==485

def test_first_connection_prepaid_once(wallet):
    wallet.register_client(OWNER,'waiting','rep',5,0,duration_days=60)
    assert balance(wallet)==490
    wallet.edit_client(OWNER,'waiting',expires_at=int(time.time())+60*DAY)
    assert balance(wallet)==490

def test_quote_is_read_only(wallet):
    with wallet.lock:
        quote=change(wallet,wallet.db,client_id='',owner='rep',quota_bytes=0,
                     ip_limit=5,expires_at=int(time.time())+60*DAY,creation=True,preview=True)
    assert quote['units']==10 and quote['after']==490
    assert balance(wallet)==500
    assert wallet.db.execute('SELECT COUNT(*) FROM unlimited_entitlements').fetchone()[0]==0

def test_insufficient_credit_rolls_back(wallet):
    wallet.register_owner(OWNER,'rep',unlimited_credit=9)
    with pytest.raises(PolicyError,match='Insufficient'):
        make(wallet)
    assert balance(wallet)==9
    assert wallet.db.execute('SELECT COUNT(*) FROM clients').fetchone()[0]==0

def test_failed_insert_rolls_back_charge(wallet):
    wallet.db.execute("CREATE TRIGGER reject_test BEFORE INSERT ON clients BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        make(wallet)
    assert balance(wallet)==500
    assert wallet.db.execute("SELECT COUNT(*) FROM resource_credit_ledger WHERE kind IN ('unlimited_create','unlimited_change')").fetchone()[0]==0

def test_outer_transaction_rolls_back_all(wallet):
    with pytest.raises(RuntimeError):
        with wallet.transaction():
            make(wallet)
            raise RuntimeError('abort desired-state write')
    assert balance(wallet)==500
    assert wallet.db.execute('SELECT COUNT(*) FROM clients').fetchone()[0]==0

@pytest.mark.parametrize('ips,days',[(0,30),(1,0)])
def test_new_unbounded_unlimited_rejected(wallet,ips,days):
    with pytest.raises(PolicyError):
        wallet.register_client(OWNER,'invalid','rep',ips,0,expires_at=int(time.time())+days*DAY if days else 0)
    assert balance(wallet)==500

def test_volume_unlimited_ip_stays_separate(wallet):
    wallet.register_client(OWNER,'vol','rep',0,1000)
    assert balance(wallet)==500
    assert wallet.owner_stats(OWNER,'rep')['volume_credit_remaining_bytes']==9000

def test_tiered_extension_cannot_create_free_high_tier(wallet):
    end=make(wallet,days=30)
    assert balance(wallet)==495
    wallet.edit_client(OWNER,'service',limit_ip=1)
    wallet.edit_client(OWNER,'service',expires_at=end+30*DAY)
    assert balance(wallet)==494
    wallet.edit_client(OWNER,'service',limit_ip=5)
    assert balance(wallet)==490

def test_change_to_volume_does_not_refund(wallet):
    end=make(wallet)
    wallet.edit_client(OWNER,'service',quota_bytes=1000)
    assert balance(wallet)==490
    wallet.edit_client(OWNER,'service',quota_bytes=0)
    assert balance(wallet)==480

def test_custom_days_round_up_cumulatively():
    assert units_for_days(5,60)==10
    assert units_for_days(1,31)==2
    state,cost=plan_change(None,quota_bytes=0,ip_limit=1,expires_at=100+MONTH,now=100,creation=True)
    assert cost==1
    for extra in range(1,100):
        state,cost=plan_change(state,quota_bytes=0,ip_limit=1,expires_at=100+MONTH+extra,now=100)
    assert state['units']==2

def test_legacy_migration_preserves_free_balance(wallet):
    make(wallet)
    path=wallet.path
    # Recreate v3 accounting shape only inside this isolated temporary test DB.
    wallet.db.execute('DROP TABLE unlimited_entitlements')
    wallet.db.execute('DELETE FROM resource_credit_ledger')
    wallet.db.execute('ALTER TABLE owners DROP COLUMN unlimited_spent')
    wallet.db.execute('PRAGMA user_version=3')
    wallet.close()
    migrated=Store(path)
    assert balance(migrated)==499
    migrated.delete_client(OWNER,'service')
    assert balance(migrated)==499
    migrated.close()
    reopened=Store(path)
    assert balance(reopened)==499
    reopened.close()

def test_parallel_sales_cannot_overspend(wallet):
    from threading import Barrier
    wallet.register_owner(OWNER,'rep',unlimited_credit=15)
    barrier=Barrier(2)
    def sell(name):
        other=Store(wallet.path)
        try:
            barrier.wait(timeout=10)
            make(other,name=name)
            return True
        except PolicyError:return False
        finally:other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(sell,['sale1','sale2']))==[False,True]
    assert balance(wallet)==5
