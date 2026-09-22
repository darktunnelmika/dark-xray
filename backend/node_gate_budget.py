"""Nominal scheduling budget for read-only multi-Node acceptance probes.

Each Node observation uses five sequential HTTPS reads. The watch clock starts
AFTER the initial round and permits one final round to finish after its deadline.
This is an I/O allowance, not a promise about DNS, locks or trickled socket reads.
The parent subprocess timeout remains the hard backstop and must fail closed.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

READS_PER_NODE = 5
PROCESS_ALLOWANCE_SECONDS = 20.0


def node_budget(node_count: int, timeout: float, watch_seconds: float) -> dict:
    if type(node_count) is not int or node_count < 0:
        raise ValueError('invalid_node_count')
    if (not math.isfinite(timeout) or not .2 <= timeout <= 30
            or not math.isfinite(watch_seconds) or watch_seconds < 0):
        raise ValueError('invalid_node_timing')
    per_round = node_count * READS_PER_NODE * timeout
    rounds = 2 if watch_seconds > 0 else 1
    total = max(30.0, PROCESS_ALLOWANCE_SECONDS + watch_seconds + rounds * per_round)
    if not math.isfinite(total):
        raise ValueError('invalid_node_budget')
    return {'enabled_node_count': node_count, 'reads_per_node': READS_PER_NODE,
            'request_timeout_seconds': timeout, 'round_allowance_seconds': per_round,
            'reserved_rounds': rounds, 'watch_seconds': watch_seconds,
            'process_allowance_seconds': PROCESS_ALLOWANCE_SECONDS,
            'subprocess_timeout_seconds': total,
            'basis': 'nominal sequential I/O allowance; not a DNS or per-socket deadline'}


def enabled_node_count(data: Path) -> int:
    """Read only: no Auth/Store initialization, schema migration or DB creation."""
    path = Path(data) / 'dark.sqlite3'
    if path.is_symlink() or not path.is_file():
        raise ValueError('unsafe_or_missing_node_database')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2.0)
    try:
        db.execute('PRAGMA query_only=ON')
        return int(db.execute('SELECT COUNT(*) FROM remote_nodes WHERE enabled != 0').fetchone()[0])
    finally:
        db.close()
