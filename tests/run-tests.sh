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
python -m pytest tests/test_runtime_apply.py -q --junitxml=qa/junit/runtime-apply.xml
python -m pytest tests/test_domain_tool.py -q --junitxml=qa/junit/domain-tool.xml
python -m pytest tests/test_clients_v2.py -q --junitxml=qa/junit/clients-v2.xml
python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
python -m pytest tests/test_accounting_stability.py -q --junitxml=qa/junit/accounting-stability.xml
python -m pytest tests/test_destructive_recovery.py -q --junitxml=qa/junit/destructive-recovery.xml
python -m pytest tests/test_ops_v2.py -q --junitxml=qa/junit/ops-v2.xml
python -m pytest tests/test_nodes_v2.py -q --junitxml=qa/junit/nodes-v2.xml
python -m pytest tests/test_owner_recovery.py -q --junitxml=qa/junit/owner-recovery.xml
python -m pytest tests/test_sessions.py -q --junitxml=qa/junit/sessions.xml
python -m pytest tests/test_update_permissions.py -q --junitxml=qa/junit/update-permissions.xml
python -m pytest tests/test_update_transaction.py -q --junitxml=qa/junit/update-transaction.xml
python -m pytest tests/test_web_contract.py -q --junitxml=qa/junit/web-contract.xml
python -m pytest tests/test_reality_scan.py -q --junitxml=qa/junit/reality.xml
node --test tests/form-models.test.cjs
node --test tests/inbounds-models.test.cjs
