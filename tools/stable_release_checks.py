#!/usr/bin/env python3
"""Fail closed before publishing an exact, green stable main snapshot."""
import argparse
import json
import re
import subprocess
import time


def validate_checks(checks, minimum=28):
    relevant = [c for c in checks if c['name'] != 'publish']
    if len(relevant) < minimum:
        return False
    failed = [c for c in relevant if c['status'] == 'completed' and c.get('conclusion') != 'success']
    if failed:
        raise RuntimeError('A candidate check failed or was cancelled/skipped')
    return all(c['status'] == 'completed' and c.get('conclusion') == 'success' for c in relevant)


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sha', required=True)
    p.add_argument('--repository', required=True)
    p.add_argument('--timeout', type=float, default=600)
    a = p.parse_args()
    if a.repository != 'darktunnelmika/dark-xray' or not re.fullmatch('[0-9a-f]{40}', a.sha):
        raise SystemExit('Unexpected repository or immutable commit')
    deadline = time.monotonic() + a.timeout
    while True:
        if api('repos/' + a.repository + '/git/ref/heads/main')['object']['sha'] != a.sha:
            raise SystemExit('Candidate is no longer the current main commit')
        doc = api('repos/' + a.repository + '/commits/' + a.sha + '/check-runs?per_page=100')
        if doc['total_count'] > len(doc['check_runs']):
            raise SystemExit('Incomplete check pagination; refusing publication')
        if validate_checks(doc['check_runs']):
            return
        if time.monotonic() >= deadline:
            raise SystemExit('Checks are missing or still pending; refusing publication')
        time.sleep(15)


if __name__ == '__main__':
    main()
