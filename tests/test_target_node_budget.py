"""Deterministic budget model plus real subprocess timeout cleanup.

The scheduling model is not a network benchmark. HTTPS integration is separate.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from node_gate_budget import enabled_node_count, node_budget
from test_node_wan_readiness import gate_module, Registry

ROOT = Path(__file__).resolve().parents[1]


def parent_gate():
    path = Path(os.environ.get('DARK_BUDGET_BASELINE', ROOT/'tools/target-vps-gate.py'))
    spec = importlib.util.spec_from_file_location('target_budget_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def database(root, count=8):
    root.mkdir(exist_ok=True)
    with sqlite3.connect(root/'dark.sqlite3') as db:
        db.execute('CREATE TABLE remote_nodes(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
        db.executemany('INSERT INTO remote_nodes VALUES(?,?)', [(str(i), 1) for i in range(count)] + [('disabled', 0)])
    return root


@pytest.mark.parametrize('count,watch', [(8, 0), (3, 10), (20, 5)])
def test_parent_allows_all_five_reads_for_actual_fleet_not_minimum(tmp_path, monkeypatch, count, watch):
    gate = parent_gate(); data = database(tmp_path/'data', count); recorded = []
    # Each request takes 90% of its own one-second allowance. There are count*5
    # reads in the first round and another full possible finishing round in watch.
    required = watch + count * 5 * .9 * (2 if watch else 1)
    def child(args, timeout):
        recorded.append((args, timeout))
        if timeout < required:
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, 0, json.dumps({'passed': True,
            'configured_enabled_nodes': count, 'observation_complete': True}), '')
    monkeypatch.setattr(gate, '_child', child)
    result = gate.node_phase(tmp_path/'config', data, 2, 1.0, watch, [])
    assert result['passed'] is True, result
    args, allowance = recorded[0]
    assert args[args.index('--expected-node-count') + 1] == str(count)
    assert allowance >= required
    assert result['timing_budget']['enabled_node_count'] == count


@pytest.mark.parametrize('count', [0, 1, 8, 100])
@pytest.mark.parametrize('watch', [0, .1, 60])
def test_budget_reserves_initial_and_finishing_rounds(count, watch):
    result = node_budget(count, 8.0, watch)
    rounds = 2 if watch else 1
    assert result['reserved_rounds'] == rounds
    assert result['round_allowance_seconds'] == count*5*8
    assert result['subprocess_timeout_seconds'] == max(30.0, 20+watch+rounds*count*5*8)


@pytest.mark.parametrize('count,timeout,watch', [(-1, 1, 0), (True, 1, 0), (1, .1, 0),
    (1, 31, 0), (1, float('nan'), 0), (1, float('inf'), 0),
    (1, 1, -1), (1, 1, float('nan')), (1, 1, float('inf'))])
def test_invalid_budget_is_rejected(count, timeout, watch):
    with pytest.raises(ValueError): node_budget(count, timeout, watch)


def test_count_reads_committed_wal_without_migrating_or_decrypting(tmp_path):
    data = database(tmp_path, 4)
    with sqlite3.connect(data/'dark.sqlite3') as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO remote_nodes VALUES('pending',1)")
        assert enabled_node_count(data) == 4
        writer.commit()
        assert enabled_node_count(data) == 5
        assert [r[0] for r in writer.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['remote_nodes']
    assert not (data/'secret.key').exists()


@pytest.mark.parametrize('mode', ['missing', 'symlink', 'missing-table', 'corrupt'])
def test_unreadable_database_never_starts_a_child(tmp_path, monkeypatch, mode):
    gate = parent_gate(); data = tmp_path/'data'; data.mkdir(); path = data/'dark.sqlite3'
    if mode == 'symlink':
        target = tmp_path/'original'; target.write_text('preserve'); path.symlink_to(target)
    elif mode == 'missing-table':
        with sqlite3.connect(path) as db: db.execute('CREATE TABLE unrelated(x)')
    elif mode == 'corrupt': path.write_text('invalid sqlite')
    before = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(gate, '_child', lambda *a: pytest.fail('must not run'))
    result = gate.node_phase(tmp_path/'config', data, 2, 1, 0, [])
    assert result['passed'] is False and result['observation_complete'] is False
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize('mode', ['timeout', 'launch', 'invalid-json', 'non-object', 'wrong-count', 'incomplete', 'nonzero', 'not-bool'])
def test_parent_never_promotes_incomplete_or_stale_results(tmp_path, monkeypatch, mode):
    gate = parent_gate(); data = database(tmp_path/'data', 2)
    report = data/'qa/target-node-wan-gate.json'; report.parent.mkdir(); report.write_text('{"passed":true}')
    def child(args, timeout):
        if mode == 'timeout': raise subprocess.TimeoutExpired(args, timeout, output=b'{"passed":true}', stderr=b'dkn_SECRET')
        if mode == 'launch': raise OSError('dkn_SECRET')
        doc = {'passed': True, 'configured_enabled_nodes': 2, 'observation_complete': True}
        if mode == 'wrong-count': doc['configured_enabled_nodes'] = 3
        if mode == 'incomplete': doc['observation_complete'] = False
        if mode == 'not-bool': doc['passed'] = 1
        text = 'invalid' if mode == 'invalid-json' else ('[]' if mode == 'non-object' else json.dumps(doc))
        return subprocess.CompletedProcess(args, 1 if mode == 'nonzero' else 0, text, 'dkn_SECRET')
    monkeypatch.setattr(gate, '_child', child)
    result = gate.node_phase(tmp_path/'config', data, 2, 1, 0, [])
    assert result['passed'] is False and 'dkn_SECRET' not in str(result)


def test_real_timed_out_child_is_reaped_and_partial_success_is_not_trusted(tmp_path, monkeypatch):
    gate = parent_gate(); data = database(tmp_path/'data', 2)
    real = gate._child; real_popen = subprocess.Popen; pids = []
    class TrackedProcess(real_popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pids.append(self.pid)
    # Capture PID at creation: do not require the child to be scheduled quickly
    # enough to write a PID file before the deadline on a loaded runner.
    monkeypatch.setattr(gate.subprocess, 'Popen', TrackedProcess)
    script = 'import time;print(\'{"passed":true}\',flush=True);time.sleep(60)'
    monkeypatch.setattr(gate, '_child', lambda args, timeout: real([sys.executable, '-u', '-c', script], 1.0))
    result = gate.node_phase(tmp_path/'config', data, 2, 1, 0, [])
    assert result['passed'] is False and result['exit_code'] == 124
    assert len(pids) == 1 and not psutil.pid_exists(pids[0])


@pytest.mark.parametrize('expected', [0, 2])
def test_child_count_race_fails_before_any_network_read(tmp_path, monkeypatch, capsys, expected):
    gate = gate_module(); reg = Registry(); reg.store.close = reg.close = lambda: None
    data = database(tmp_path/'data', 1); (data/'secret.key').touch()
    monkeypatch.setattr(gate.Config, 'load', lambda p: None)
    monkeypatch.setattr(gate, 'Store', lambda p: reg.store)
    monkeypatch.setattr(gate, 'Auth', lambda *a: type('Auth', (), {'cipher': None})())
    monkeypatch.setattr(gate, 'NodeRegistry', lambda *a: reg)
    monkeypatch.setattr(sys, 'argv', ['gate', '--config', '/fixture', '--data', str(data),
        '--expected-node-count', str(expected), '--json-only'])
    with pytest.raises(SystemExit) as done: gate.main()
    result = json.loads(capsys.readouterr().out)
    assert done.value.code == 1 and not reg.calls
    assert result['passed'] is False and result['observation_complete'] is False
    assert result['real_wan_requests'] is False


def test_optional_node_phase_does_not_open_db(tmp_path, monkeypatch):
    gate = parent_gate()
    monkeypatch.setattr(gate, '_child', lambda *a: pytest.fail('optional phase ran'))
    assert gate.node_phase(tmp_path/'config', tmp_path, 0, 8, 0, [])['skipped'] is True
    assert not (tmp_path/'dark.sqlite3').exists()


@pytest.mark.parametrize('flag', ['--node-timeout', '--node-watch-seconds', '--production-timeout'])
@pytest.mark.parametrize('value', ['nan', 'inf'])
def test_parent_invalid_time_rejected_before_collection(monkeypatch, flag, value):
    gate = parent_gate(); monkeypatch.setattr(gate.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(gate, 'boot_identity', lambda: pytest.fail('collection before validation'))
    monkeypatch.setattr(sys, 'argv', ['gate', '--config', '/fixture', '--data', '/fixture', flag, value])
    with pytest.raises(SystemExit): gate.main()
