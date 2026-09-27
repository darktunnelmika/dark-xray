import json
from core import Config,CoreEngine
from dark_policy import Store

IB={
 "remark":"MATRIX","listen":"127.0.0.1","port":8569,"protocol":"vless","enable":True,"tag":"matrix-in",
 "settings":{"decryption":"none"},"streamSettings":{"network":"grpc","security":"none"},"sniffing":{},
 "panelMeta":{"deployLocal":True,"deploymentTargets":["local","node:n1"],
              "tunnelPorts":{"local":1185,"node:n1":2285}},
}

def engine(tmp_path):
 store=Store(tmp_path/"dark.sqlite3")
 eng=CoreEngine(Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test"),store,tmp_path/"runtime")
 iid=eng.save_inbound(IB)["id"]
 return store,eng,iid

def put_matrix(store,scope,iid,path,policy):
 with store.transaction() as db:
  db.execute("INSERT INTO traffic_matrix(scope,inbound_id,access_path,policy,updated_at) VALUES(?,?,?,?,1)",
             (scope,iid,path,policy))

def test_direct_and_tunnel_compile_as_independent_paths(tmp_path):
 store,eng,iid=engine(tmp_path)
 put_matrix(store,"hub",iid,"direct","normal")
 put_matrix(store,"hub",iid,"tunnel","warp_ai_adblock")
 routing=eng.routing_for_scope("hub")
 managed=[r for r in routing["rules"] if r.get("ruleTag","").startswith("dark-matrix-")]
 direct=[r for r in managed if r["inboundTag"]==["matrix-in"]]
 tunnel=[r for r in managed if r["inboundTag"]==["dark-tunnel-%s-1185"%iid]]
 assert len(direct)==1 and direct[0]["outboundTag"]=="direct"
 assert [r["outboundTag"] for r in tunnel]==["block","warp","direct"]
 assert tunnel[1]["domain"] and "domain:openai.com" in tunnel[1]["domain"]
 store.close()

def test_runtime_scope_uses_node_specific_tunnel_port(tmp_path):
 store,eng,iid=engine(tmp_path)
 put_matrix(store,"node:n1",iid,"direct","warp_all")
 put_matrix(store,"node:n1",iid,"tunnel","adblock")
 routing=eng.routing_for_scope("node:n1")
 managed=[r for r in routing["rules"] if r.get("ruleTag","").startswith("dark-matrix-")]
 assert any(r["inboundTag"]==["matrix-in"] and r["outboundTag"]=="warp" for r in managed)
 tunnel=[r for r in managed if r["inboundTag"]==["dark-tunnel-%s-2285"%iid]]
 assert [r["outboundTag"] for r in tunnel]==["block","direct"]
 assert not any("1185" in json.dumps(r) for r in managed)
 store.close()

def test_warp_profile_is_injected_per_runtime(tmp_path):
 store,eng,iid=engine(tmp_path)
 profile={"tag":"warp","protocol":"wireguard","settings":{"secretKey":"secret","address":["172.16.0.2/32"],
          "peers":[{"publicKey":"peer","endpoint":"162.159.192.1:2408"}]}}
 with store.transaction() as db:
  db.execute("INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,1)",
             ("node:n1",json.dumps(profile),"device"))
 hub=eng.runtime_outbounds("hub");node=eng.runtime_outbounds("node:n1")
 assert not any(x.get("tag")=="warp" for x in hub)
 assert any(x.get("tag")=="warp" and x.get("protocol")=="wireguard" for x in node)
 store.close()

def test_matrix_rules_are_not_shadow_expanded(tmp_path):
 store,eng,iid=engine(tmp_path)
 put_matrix(store,"hub",iid,"direct","normal")
 cfg=eng.build_config()
 rules=[r for r in cfg["routing"]["rules"] if r.get("ruleTag","").startswith("dark-matrix-")]
 assert len(rules)==1
 assert rules[0]["inboundTag"]==["matrix-in"]
 assert not any("dark-tunnel-" in tag for tag in rules[0]["inboundTag"])
 store.close()
