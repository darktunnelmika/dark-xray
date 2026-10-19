"""Exact-payload memoization must never reuse stale security coverage."""
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

from nodes import NodeRegistry


def registry():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE remote_node_desired_state(node_id TEXT PRIMARY KEY,desired_json TEXT)')
    node = NodeRegistry.__new__(NodeRegistry)
    node.store = SimpleNamespace(db=db, lock=threading.RLock())
    return node, db


def payload(shadow=None):
    meta = {} if shadow is None else {'tunnelPorts': {'local': shadow}}
    return json.dumps({'assignments': [{'inbound': {'enable': True, 'panelMeta': meta,
        'settings': {'clients': [{'email': f'test-{i}'} for i in range(500)]}}}]})


def test_repeated_fleet_clients_parse_identical_node_payload_once():
    node, db = registry()
    db.execute('INSERT INTO remote_node_desired_state VALUES(?,?)', ('n1', payload()))
    cache = {}
    try:
        with patch('nodes.json.loads', wraps=json.loads) as decode:
            for _ in range(400):
                assert node._node_source_scope_complete('n1', cache=cache) is True
            assert decode.call_count == 1
            assert node._node_source_scope_complete('n1', cache={}) is True
            assert decode.call_count == 2  # no cache survives a new reconciliation
    finally:
        db.close()


def test_payload_change_without_revision_change_is_seen_immediately():
    node, db = registry()
    db.execute('INSERT INTO remote_node_desired_state VALUES(?,?)', ('n1', payload()))
    cache = {}
    try:
        assert node._node_source_scope_complete('n1', cache=cache) is True
        db.execute('UPDATE remote_node_desired_state SET desired_json=?', (payload(1185),))
        assert node._node_source_scope_complete('n1', cache=cache) is False
        db.execute('UPDATE remote_node_desired_state SET desired_json=?', ('{broken',))
        assert node._node_source_scope_complete('n1', cache=cache) is False
        db.execute('UPDATE remote_node_desired_state SET desired_json=?', (payload(),))
        assert node._node_source_scope_complete('n1', cache=cache) is True
        db.execute('DELETE FROM remote_node_desired_state')
        assert node._node_source_scope_complete('n1', cache=cache) is False
    finally:
        db.close()


def test_cached_and_uncached_results_agree_for_all_existing_coverage_cases():
    node, db = registry()
    db.execute('INSERT INTO remote_node_desired_state VALUES(?,?)', ('n1', payload()))
    cache = {}
    cases = [payload(), payload(1), payload(65535), payload(0), payload(True),
             '{broken', '{}', 'null', '{"assignments":null}',
             '{"assignments":[{"inbound":{"enable":false,"panelMeta":{"tunnelPorts":{"local":1185}}}}]}']
    try:
        for raw in cases:
            db.execute('UPDATE remote_node_desired_state SET desired_json=?', (raw,))
            for _ in range(3):
                assert node._node_source_scope_complete('n1', cache=cache) == node._node_source_scope_complete('n1')
    finally:
        db.close()
