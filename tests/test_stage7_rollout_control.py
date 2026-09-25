"""Stage 7.3 rollout control and telemetry acceptance."""
from __future__ import annotations

import time

from test_smart_routing_review import review,safety,stage7_env


def _start(client,revision_id,observation=3):
    response=client.post('/api/smart-routing/rollout/start',json={
        'revisionId':revision_id,'confirmation':'START STAGED ROLLOUT',
        'observationSeconds':observation,
    })
    assert response.status_code==202,response.text
    return response.json()['rolloutId']


def _get(client,rollout_id):
    response=client.get('/api/smart-routing/rollout/'+rollout_id)
    assert response.status_code==200,response.text
    return response.json()


def _wait(client,rollout_id,predicate,timeout=10):
    deadline=time.time()+timeout;doc=None
    while time.time()<deadline:
        doc=_get(client,rollout_id)
        if predicate(doc):return doc
        time.sleep(.05)
    raise AssertionError('rollout condition timed out: '+repr(doc))


def test_stage73_pause_resume_preserves_rollout_and_completes(stage7_env):
    _store,_engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    rollout_id=_start(client,reviewed['revisionId'],observation=3)

    pause=client.post('/api/smart-routing/rollout/'+rollout_id+'/pause',
                      json={'confirmation':'PAUSE STAGED ROLLOUT'})
    assert pause.status_code==202,pause.text
    paused=_wait(client,rollout_id,lambda d:d['controlState']=='paused')
    assert paused['state']=='running'
    paused_progress=paused['progressPercent']
    time.sleep(.35)
    still=_get(client,rollout_id)
    assert still['controlState']=='paused' and still['progressPercent']==paused_progress

    resume=client.post('/api/smart-routing/rollout/'+rollout_id+'/resume',
                       json={'confirmation':'RESUME STAGED ROLLOUT'})
    assert resume.status_code==202,resume.text
    done=_wait(client,rollout_id,lambda d:d['state']!='running',timeout=18)
    assert done['state']=='completed',done
    kinds=[x['kind'] for x in done['timeline']]
    assert 'pause_requested' in kinds and 'paused' in kinds
    assert 'resume_requested' in kinds and 'resumed' in kinds
    assert 'completed' in kinds
    assert done['pausedAt']>0 and done['resumedAt']>=done['pausedAt']


def test_stage73_abort_during_observation_rolls_back_changed_nodes_and_keeps_hub_baseline(stage7_env):
    _store,engine,client=stage7_env
    baseline=engine.section('routing')
    reviewed=review(client);safety(client,reviewed['revisionId'])
    rollout_id=_start(client,reviewed['revisionId'],observation=5)

    active=_wait(client,rollout_id,lambda d:any(x['state']=='verifying' for x in d['items']))
    assert active['state']=='running'
    abort=client.post('/api/smart-routing/rollout/'+rollout_id+'/abort',
                      json={'confirmation':'ABORT STAGED ROLLOUT'})
    assert abort.status_code==202,abort.text

    done=_wait(client,rollout_id,lambda d:d['state']!='running',timeout=12)
    assert done['state']=='aborted',done
    assert done['abortedAt']>0
    changed=[x for x in done['items'] if x['state']!='pending']
    assert changed and all(x['state']=='rolled_back' for x in changed)
    assert engine.section('routing')==baseline
    kinds=[x['kind'] for x in done['timeline']]
    assert 'abort_requested' in kinds and 'abort_ack' in kinds and 'rollback_complete' in kinds
    rollback=[x for x in done['timeline'] if x['kind']=='rollback_complete'][-1]
    assert 'Aborted by owner' in rollback['detail']


def test_stage73_timeline_has_health_warp_metrics_and_history_is_bounded_summary(stage7_env):
    _store,_engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    rollout_id=_start(client,reviewed['revisionId'],observation=1)
    done=_wait(client,rollout_id,lambda d:d['state']!='running',timeout=12)
    assert done['state']=='completed',done

    timeline=client.get('/api/smart-routing/rollout/'+rollout_id+'/timeline')
    assert timeline.status_code==200,timeline.text
    events=timeline.json()['items']
    health=[x for x in events if x['kind']=='health_sample']
    warp=[x for x in events if x['kind']=='warp_probe']
    assert health and health[0]['metrics']['coreState']=='running'
    assert warp and warp[0]['metrics']['passed'] is True
    assert warp[0]['metrics']['items'][0]['latencyMs']==80.0

    history=client.get('/api/smart-routing/rollouts')
    assert history.status_code==200,history.text
    item=history.json()['items'][0]
    assert item['rolloutId']==rollout_id and item['timeline']==[]
    assert item['state']=='completed' and item['healthyNodes']==item['totalNodes']
