import copy
import pytest

from smart_routing import SmartRoutingError, build_stage7_patch, build_stage7_plan, classify_nodes, rank_warp_paths


def base_outbounds():
    return [
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'block','protocol':'blackhole','settings':{}},
        {'tag':'warp-us','protocol':'wireguard','settings':{'secretKey':'x','peers':[{'publicKey':'y','endpoint':'1.1.1.1:2408'}],'address':['172.16.0.2/32']}},
        {'tag':'warp-de','protocol':'wireguard','settings':{'secretKey':'x','peers':[{'publicKey':'y','endpoint':'1.1.1.1:2408'}],'address':['172.16.0.3/32']}},
    ]


def test_classifies_warp_and_adblock_nodes_by_region_hints():
    nodes=[
        {'id':'n1','name':'USA AI node','enabled':True,'online':True},
        {'id':'n2','name':'Germany WARP','enabled':True,'online':True},
        {'id':'n3','name':'France Ads','enabled':True,'online':True},
        {'id':'n4','name':'UK Adblock','enabled':True,'online':True},
        {'id':'n5','name':'offline usa','enabled':True,'online':False},
    ]
    plan=classify_nodes(nodes)
    assert [x['id'] for x in plan['warp_ai']['nodes']]==['n1','n2']
    assert [x['id'] for x in plan['adblock']['nodes']]==['n3','n4']


def test_stage7_patch_prepends_adblock_and_warp_balancer_without_mutating_input():
    out=base_outbounds();routing={'domainStrategy':'AsIs','rules':[{'type':'field','domain':['domain:example.com'],'outboundTag':'direct'}]}
    original=copy.deepcopy(routing)
    patch=build_stage7_patch(out,routing,warp_outbound_tags=['warp-us','warp-de'])
    assert routing==original
    assert patch['previewOnly'] is True
    assert [r['ruleTag'] for r in patch['routing']['rules'][:2]]==['dark-smart-adblock','dark-smart-warp-ai']
    assert patch['routing']['rules'][1]['balancerTag']=='dark-smart-warp-ai-balancer'
    assert patch['routing']['balancers'][0]['selector']==['warp-us','warp-de']
    assert patch['observatory']['subjectSelector']==['warp-us','warp-de']


def test_stage7_patch_rejects_unknown_warp_outbound():
    with pytest.raises(SmartRoutingError,match='Unknown WARP outbound'):
        build_stage7_patch(base_outbounds(),{'rules':[]},warp_outbound_tags=['warp-missing'])


def test_stage7_patch_can_preview_adblock_only():
    patch=build_stage7_patch(base_outbounds(),{'rules':[]},enable_warp_ai=False,enable_adblock=True)
    assert patch['routing']['rules'][0]['ruleTag']=='dark-smart-adblock'
    assert patch['routing']['rules'][0]['outboundTag']=='block'
    assert patch['observatory'] is None


def test_rank_warp_paths_prefers_low_loss_then_latency():
    ranked=rank_warp_paths([
        {'tag':'warp-a','latenciesMs':[20,21,22],'lossPercent':5},
        {'tag':'warp-b','latenciesMs':[60,50,55],'lossPercent':0},
        {'tag':'warp-c','latenciesMs':[30,31,32],'lossPercent':0},
        {'tag':'warp-down','ok':False},
    ])
    assert [x['tag'] for x in ranked]==['warp-c','warp-b','warp-a','warp-down']


def test_build_stage7_plan_is_preview_only_and_lists_candidates():
    plan=build_stage7_plan([{'id':'us1','name':'USA','enabled':True,'online':True}],base_outbounds(),{'rules':[]})
    assert plan['safeDefault']=='preview_only'
    assert plan['capabilities']['automaticApply'] is False
    assert plan['configuredWarpCandidates']==['warp-de','warp-us']

def test_stage7_plan_exposes_sanitized_wireguard_candidate_metadata_only():
    out=base_outbounds()
    out[2]['panelMeta']={'region':'USA','nodeName':'us-ai-node','smartWarp':True}
    plan=build_stage7_plan([],out,{'rules':[]})
    row=next(x for x in plan['warpCandidates'] if x['tag']=='warp-us')
    assert row['region']=='USA'
    assert row['node']=='us-ai-node'
    assert row['likelyWarp'] is True
    assert 'settings' not in row
    assert 'secretKey' not in row
    assert 'secret-do-not-return' not in str(row)

def test_stage7_plan_infers_common_warp_regions_without_exposing_settings():
    plan=build_stage7_plan([],base_outbounds(),{'rules':[]})
    rows={x['tag']:x for x in plan['warpCandidates']}
    assert rows['warp-us']['region']=='USA'
    assert rows['warp-de']['region']=='Germany'
    assert rows['warp-us']['node']=='warp-us'
    assert all('settings' not in row for row in rows.values())
