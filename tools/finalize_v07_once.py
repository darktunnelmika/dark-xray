#!/usr/bin/env python3
from pathlib import Path
import json

# Old contract test: Clash is now a supported Subscription V2 format.
p=Path('tests/test_standalone.py');s=p.read_text();old="assert c.get(r['subscription_url']+'?format=clash').status_code==400";new="assert c.get(r['subscription_url']+'?format=singbox').status_code==400"
if old in s:s=s.replace(old,new,1)
elif new not in s:raise RuntimeError('standalone subscription contract anchor missing')
p.write_text(s)

# Version bump for the completed panel-workspace milestone; still explicitly LAB.
Path('VERSION').write_text('0.7.0-standalone-lab\n')
p=Path('backend/server.py');s=p.read_text();s=s.replace("VERSION='0.6.0-standalone-lab'","VERSION='0.7.0-standalone-lab'",1);p.write_text(s)

# Rename the pure inbounds-model test so it no longer suggests the dead V2 UI is tested.
old_test=Path('tests/inbounds-v2.test.cjs');new_test=Path('tests/inbounds-models.test.cjs')
if old_test.exists() and not new_test.exists():old_test.rename(new_test)
p=Path('tests/run-tests.sh');s=p.read_text().replace('tests/inbounds-v2.test.cjs','tests/inbounds-models.test.cjs');p.write_text(s)

# Dead browser implementation: V3 is the only loaded inbound UI.
for name in ('web/inbounds-v2.js','web/inbounds-v2.css'):
    Path(name).unlink(missing_ok=True)

# Publication status reflects the new LAB milestone without claiming production readiness.
p=Path('PUBLISH-STATUS.json');doc=json.loads(p.read_text())
doc['baseline_version']='0.7.0-standalone-lab'
features=doc.setdefault('current_main_features',{})
features.pop('browser_policy_bridge',None)
features.update({
    'inbounds_workspace':'V3',
    'settings_workspace':'V2',
    'clients_groups_workspace':'V2',
    'subscription_workspace':'V2 raw/base64/json/clash',
    'xray_control_workspace':'V2 DNS/outbounds/routing/balancers/observatory',
    'hosts_workspace':'V2 client-facing endpoint overrides',
    'operations_workspace':'V2 dashboard/logs/backup',
    'nodes_workspace':'V2 HTTPS agent health/inventory/core-control',
    'node_agent_token_prefix':'dkn_',
    'safe_update_real_source_rollback':True,
    'pinned_installer_source_ref_env':'DARK_XRAY_REF',
})
doc.setdefault('live_validation',{})['distributed_client_traffic_convergence_complete']=False
doc['live_validation']['remote_node_https_probe_verified_on_real_vps']=False
doc['production_ready']=False
p.write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n')

# Add a concise current-state section once.
p=Path('README.md');s=p.read_text()
marker='## DARK XRAY 0.7 Lab workspace map'
if marker not in s:
    s += '''\n\n## DARK XRAY 0.7 Lab workspace map\n\nThe current standalone LAB line includes structured **Settings V2**, **Clients + Groups V2**, **Subscription V2** (Raw/Base64/DARK JSON/Clash-Mihomo), **Xray Control V2** (DNS, Outbounds, Routing, Balancers, Observatory), **Hosts V2**, **Operations V2** (real dashboard/logs/backup status), **Nodes V2** with dedicated HTTPS agent tokens, and **Inbounds V3** with the REALITY workflow.\n\nRuntime-sensitive panel changes are staged first and require the root-owned `darkxray settings-apply` boundary. Safe Update validates candidate source before service interruption and restores the previous source snapshot if activation fails. The online installer accepts `DARK_XRAY_REF` to pin a branch, tag, or commit.\n\nThis repository is still a **LAB build**. CI uses isolated Xray/firewall doubles. Real VPS client connectivity, certificate renewal across providers, reboot recovery, nftables packet enforcement, production load, and distributed multi-node customer/traffic convergence require live validation before any production-ready claim.\n'''
p.write_text(s)

# Remove temporary migration helpers. They have already been applied to normal source.
for name in (
 'tools/patch_clients_v2_once.py','tools/run_clients_v2_patch.py',
 'tools/patch_subscription_v2_once.py','tools/run_subscription_v2_patch.py',
 'tools/patch_ops_v2_once.py','tools/patch_nodes_v2_once.py',
 'tools/patch_installer_hardening_once.py'):
    Path(name).unlink(missing_ok=True)

print('DARK XRAY v0.7 lab cleanup prepared')
