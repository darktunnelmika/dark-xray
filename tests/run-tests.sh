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
python -m pytest tests/test_runtime_path_safety.py -q --junitxml=qa/junit/runtime-path-safety.xml
python -m pytest tests/test_live_check.py -q --junitxml=qa/junit/live-check.xml
python -m pytest tests/test_production_gate.py -q --junitxml=qa/junit/production-gate.xml
python -m pytest tests/test_target_vps_gate.py -q --junitxml=qa/junit/target-vps-gate.xml
python -m pytest tests/test_domain_tool.py -q --junitxml=qa/junit/domain-tool.xml
python -m pytest tests/test_clients_v2.py -q --junitxml=qa/junit/clients-v2.xml
python -m pytest tests/test_client_presence.py -q --junitxml=qa/junit/client-presence.xml
python -m pytest tests/test_bulk_batching.py -q --junitxml=qa/junit/bulk-batching.xml
python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
python -m pytest tests/test_subscription_path.py -q --junitxml=qa/junit/subscription-path.xml
python -m pytest tests/test_hosts_v3.py -q --junitxml=qa/junit/hosts-v3.xml
python -m pytest tests/test_accounting_stability.py -q --junitxml=qa/junit/accounting-stability.xml
python -m pytest tests/test_finance_hardening.py -q --junitxml=qa/junit/finance-hardening.xml
python -m pytest tests/test_destructive_recovery.py -q --junitxml=qa/junit/destructive-recovery.xml
python -m pytest tests/test_policy_consistency.py -q --junitxml=qa/junit/policy-consistency.xml
python -m pytest tests/test_reset_scheduler_v2.py -q --junitxml=qa/junit/reset-scheduler-v2.xml
python -m pytest tests/test_ops_v2.py -q --junitxml=qa/junit/ops-v2.xml
python -m pytest tests/test_nodes_v2.py -q --junitxml=qa/junit/nodes-v2.xml
python -m pytest tests/test_node_agent_architecture.py -q --junitxml=qa/junit/node-agent-architecture.xml
python -m pytest tests/test_preupdate_node_hardening.py -q --junitxml=qa/junit/preupdate-node-hardening.xml
python -m pytest tests/test_node_guard_recovery.py -q --junitxml=qa/junit/node-guard-recovery.xml
python -m pytest tests/test_hub_node_control_store.py -q --junitxml=qa/junit/hub-node-control-store.xml
python -m pytest tests/test_node_control_lifecycle.py -q --junitxml=qa/junit/node-control-lifecycle.xml
python -m pytest tests/test_core_api_time_wait.py -q --junitxml=qa/junit/core-api-time-wait.xml
python -m pytest tests/test_node_ordered_control.py -q --junitxml=qa/junit/node-ordered-control.xml
python -m pytest tests/test_hub_node_control_live.py -q --junitxml=qa/junit/hub-node-control-live.xml
python -m pytest tests/test_node_installations.py -q --junitxml=qa/junit/node-installations.xml
python -m pytest tests/test_node_replacement_prepare.py -q --junitxml=qa/junit/node-replacement-prepare.xml
python -m pytest tests/test_node_replacement_resolution.py -q --junitxml=qa/junit/node-replacement-resolution.xml
python -m pytest tests/test_node_token_handoff.py -q --junitxml=qa/junit/node-token-handoff.xml
python -m pytest tests/test_node_monitor.py -q --junitxml=qa/junit/node-monitor.xml
python -m pytest tests/test_owner_recovery.py -q --junitxml=qa/junit/owner-recovery.xml
python -m pytest tests/test_menu_owner_selection.py -q --junitxml=qa/junit/menu-owner-selection.xml
python -m pytest tests/test_control_center_v2.py -q --junitxml=qa/junit/control-center-v2.xml
python -m pytest tests/test_overview_v4_system.py -q --junitxml=qa/junit/overview-v4-system.xml
python -m pytest tests/test_backup_cli.py -q --junitxml=qa/junit/backup-cli.xml
python -m pytest tests/test_sessions.py -q --junitxml=qa/junit/sessions.xml
python -m pytest tests/test_api_key_boundaries.py -q --junitxml=qa/junit/api-key-boundaries.xml
python -m pytest tests/test_totp_replay.py -q --junitxml=qa/junit/totp-replay.xml
python -m pytest tests/test_rbac_hardening.py -q --junitxml=qa/junit/rbac-hardening.xml
python -m pytest tests/test_representatives_v2.py -q --junitxml=qa/junit/representatives-v2.xml
python -m pytest tests/test_sync_redaction.py -q --junitxml=qa/junit/sync-redaction.xml
python -m pytest tests/test_backup_hardening.py -q --junitxml=qa/junit/backup-hardening.xml
python -m pytest tests/test_update_permissions.py -q --junitxml=qa/junit/update-permissions.xml
python -m pytest tests/test_update_transaction.py -q --junitxml=qa/junit/update-transaction.xml
python -m pytest tests/test_update_broker.py -q --junitxml=qa/junit/update-broker.xml
python -m pytest tests/test_update_api.py -q --junitxml=qa/junit/update-api.xml
python -m pytest tests/test_web_contract.py -q --junitxml=qa/junit/web-contract.xml
python -m pytest tests/test_reality_scan.py -q --junitxml=qa/junit/reality.xml
node --test tests/form-models.test.cjs
node --test tests/inbounds-models.test.cjs
node --test tests/inbounds-v3-regression.test.cjs
node --test tests/clients-v2-filter.test.cjs
node --test tests/clients-v3.test.cjs
node --test tests/clients-v4.test.cjs
node --test tests/ops-v2-cache.test.cjs
node --test tests/finance-v2.test.cjs
node --test tests/ui-stability.test.cjs
node --test tests/update-center.test.cjs
node --test tests/nodes-v3.test.cjs
node --test tests/hosts-v3.test.cjs
node --test tests/traffic-engine-v4.test.cjs
node --test tests/representatives-v2.test.cjs
node --test tests/cyber-classic.test.cjs
node --test tests/overview-v4.test.cjs
node --test tests/i18n-cpu-branding.test.cjs
node --test tests/xray-guided-v3.test.cjs
node --test tests/settings-operations.test.cjs
node --test tests/node-control-live.test.cjs
