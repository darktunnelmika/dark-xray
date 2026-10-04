import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('stable_checks', Path(__file__).resolve().parents[1] / 'tools/stable_release_checks.py')
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


def green():
    return [{'name': 'gate-' + str(i), 'status': 'completed', 'conclusion': 'success'} for i in range(30)]


def test_publish_requires_complete_green_set_and_ignores_its_own_running_job():
    assert checks.validate_checks(green() + [{'name': 'publish', 'status': 'in_progress'}])
    assert not checks.validate_checks(green()[:-1])
    pending = green(); pending[0] = {'name': 'gate-0', 'status': 'in_progress'}
    assert not checks.validate_checks(pending)


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'skipped', 'timed_out', None])
def test_unhealthy_terminal_check_refuses_publication(conclusion):
    rows = green(); rows[0]['conclusion'] = conclusion
    with pytest.raises(RuntimeError):
        checks.validate_checks(rows)
