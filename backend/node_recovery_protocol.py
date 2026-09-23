"""Conditional Stop protocol for explicit stale-Hub recovery; no resume/reset.

A read checkpoint and a compare-before-Stop share the Agent engine lock. Neither
an old Hub counter nor a browser-supplied health document grants authority.
"""
from __future__ import annotations
import re
from dark_policy import PolicyError
from fastapi import Depends, HTTPException, Request

LIMIT = 2**63 - 1


def valid_hex(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def validate_checkpoint(value):
    if not isinstance(value, dict) or set(value) != {'command', 'configuration'}:
        raise PolicyError('invalid_recovery_checkpoint')
    command, config = value['command'], value['configuration']
    if (not isinstance(command, dict) or set(command) != {'revision', 'commandId', 'action'}
            or not isinstance(config, dict) or set(config) != {'revision', 'hash'}):
        raise PolicyError('invalid_recovery_checkpoint')
    for record in (command, config):
        if type(record['revision']) is not int or not 0 <= record['revision'] <= LIMIT:
            raise PolicyError('invalid_recovery_checkpoint')
    if command['revision'] == 0:
        if command['commandId'] != '' or command['action'] != '':
            raise PolicyError('invalid_recovery_checkpoint')
    elif not valid_hex(command['commandId'], 32) or command['action'] not in ('start', 'stop', 'restart'):
        raise PolicyError('invalid_recovery_checkpoint')
    if (config['revision'] == 0 and config['hash'] != '') or (config['revision'] > 0 and not valid_hex(config['hash'], 64)):
        raise PolicyError('invalid_recovery_checkpoint')
    return value


def checkpoint(runtime):
    command, config = runtime.command_status(), runtime.status()
    return validate_checkpoint({
        'command': {'revision': command['revision'], 'commandId': command['command_id'], 'action': command['action']},
        'configuration': {'revision': config['appliedRevision'], 'hash': config['appliedHash']}})


def install_agent_recovery(app, runtime, auth, current_mutation):
    from core import CoreError

    @app.get('/node/api/v1/recovery/state')
    def recovery_state(_scope=Depends(auth)):
        with runtime.engine.lock:
            return {'service': 'DARK XRAY NODE', 'protocol': 1, 'node_id': runtime.scope,
                    'installation_id': runtime.installation_id, 'checkpoint': checkpoint(runtime),
                    'core': {'state': runtime.engine.runtime_state()['state']},
                    'run_control': runtime.control_status(), 'writes_enabled': runtime.engine.config.writes_enabled}

    @app.post('/node/api/v1/recovery/stop')
    @current_mutation
    def recovery_stop(body: dict, request: Request, _scope=Depends(auth)):
        # current_mutation performs final token authentication AND holds engine.lock.
        # A pending/failed exact Stop may retry, but no later command may be undone.
        if (set(body) != {'nodeId', 'installationId', 'revision', 'commandId', 'expected'}
                or body.get('nodeId') != runtime.scope or body.get('installationId') != runtime.installation_id
                or type(body.get('revision')) is not int or not 0 < body['revision'] <= LIMIT
                or not valid_hex(body.get('commandId'), 32)):
            raise HTTPException(422, 'invalid_recovery_stop')
        try:
            expected = validate_checkpoint(body['expected'])
            if body['revision'] <= expected['command']['revision']:
                raise PolicyError('recovery_revision_must_advance')
            current = checkpoint(runtime)
            exact = current['command'] == {'revision': body['revision'], 'commandId': body['commandId'], 'action': 'stop'}
            if (current['configuration'] != expected['configuration']
                    or (not exact and current['command'] != expected['command'])):
                raise HTTPException(409, 'recovery_checkpoint_changed')
            return runtime.ordered_command({'nodeId': runtime.scope, 'revision': body['revision'],
                                            'commandId': body['commandId'], 'action': 'stop'})
        except CoreError as exc:
            raise HTTPException(exc.status, 'recovery_stop_not_confirmed') from exc
        except PolicyError as exc:
            raise HTTPException(422, 'invalid_recovery_stop') from exc
