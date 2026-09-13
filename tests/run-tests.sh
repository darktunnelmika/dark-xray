#!/usr/bin/env bash
# Separate processes make report scope explicit; no duplicate or deselected test is counted twice.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
mkdir -p qa/junit
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m pytest tests/legacy -q --junitxml=qa/junit/legacy.xml
python -m pytest tests/test_standalone.py -q --junitxml=qa/junit/standalone.xml
python -m pytest tests/test_supervisor.py -k 'not explicit_restart and not restart_failure' -q --junitxml=qa/junit/supervisor.xml
python -m pytest tests/test_supervisor.py -k 'explicit_restart or restart_failure' -q --junitxml=qa/junit/restart.xml
python -m pytest tests/test_v06.py -q --junitxml=qa/junit/v06.xml
python -m pytest tests/test_settings_v2.py -q --junitxml=qa/junit/settings-v2.xml
python -m pytest tests/test_clients_v2.py -q --junitxml=qa/junit/clients-v2.xml
python -m pytest tests/test_reality_scan.py -q --junitxml=qa/junit/reality.xml
node --test tests/form-models.test.cjs
node --test tests/inbounds-v2.test.cjs
