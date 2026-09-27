"""DARK XRAY Traffic Matrix helpers.

The matrix is the owner-facing routing layer for one logical inbound deployed
across Hub/Nodes. Each runtime + access path (direct/tunnel) has one policy.
Generated rules are deterministic and stay outside the Advanced Routing editor.
"""
from __future__ import annotations
import hashlib

AI_DOMAIN_MATCHERS=[
    "domain:openai.com","domain:chatgpt.com","domain:oaiusercontent.com",
    "domain:oaistatic.com","domain:openaiapi-site.azureedge.net",
]
ADBLOCK_DOMAIN_MATCHERS=[
    "geosite:category-ads-all","domain:doubleclick.net","domain:googleadservices.com",
    "domain:googlesyndication.com","domain:adservice.google.com","domain:ads-twitter.com",
]
POLICIES={"normal","warp_ai","warp_all","adblock","warp_ai_adblock","warp_all_adblock","custom"}
ACCESS_PATHS={"direct","tunnel"}

def normalize_scope(value:str)->str:
    value=str(value or "hub").strip()
    return value or "hub"

def matrix_key(scope:str,inbound_id:int,access_path:str)->str:
    return f"{normalize_scope(scope)}:{int(inbound_id)}:{access_path}"

def managed_rule_tag(scope:str,inbound_id:int,access_path:str,kind:str)->str:
    raw=matrix_key(scope,inbound_id,access_path)+"|"+str(kind)
    return "dark-matrix-"+hashlib.sha256(raw.encode()).hexdigest()[:16]+"-"+str(kind)

def tunnel_tag(inbound_id:int,port:int)->str:
    return f"dark-tunnel-{int(inbound_id)}-{int(port)}"

def policy_parts(policy:str)->tuple[bool,str]:
    value=str(policy or "normal")
    if value not in POLICIES: raise ValueError("Unsupported Traffic Matrix policy")
    adblock="adblock" in value
    if value.startswith("warp_ai"):warp="ai"
    elif value.startswith("warp_all"):warp="all"
    else:warp="off"
    return adblock,warp

def compile_rules(*,scope:str,inbound_id:int,access_path:str,policy:str,inbound_tag:str,tunnel_port:int=0)->list[dict]:
    scope=normalize_scope(scope);access_path=str(access_path)
    if access_path not in ACCESS_PATHS: raise ValueError("Unsupported Traffic Matrix access path")
    if str(policy or "normal")=="custom":return []
    adblock,warp=policy_parts(policy)
    tag=str(inbound_tag or "").strip()
    if not tag: raise ValueError("Traffic Matrix inbound tag is missing")
    if access_path=="tunnel":
        if not 1<=int(tunnel_port or 0)<=65535: raise ValueError("Traffic Matrix tunnel port is missing")
        tag=tunnel_tag(inbound_id,int(tunnel_port))
    match={"type":"field","inboundTag":[tag]}
    rules=[]
    if adblock:
        rules.append({**match,"ruleTag":managed_rule_tag(scope,inbound_id,access_path,"adblock"),
                      "domain":ADBLOCK_DOMAIN_MATCHERS[:],"outboundTag":"block"})
    if warp=="ai":
        rules.append({**match,"ruleTag":managed_rule_tag(scope,inbound_id,access_path,"warp-ai"),
                      "domain":AI_DOMAIN_MATCHERS[:],"outboundTag":"warp"})
        rules.append({**match,"ruleTag":managed_rule_tag(scope,inbound_id,access_path,"direct"),"outboundTag":"direct"})
    elif warp=="all":
        rules.append({**match,"ruleTag":managed_rule_tag(scope,inbound_id,access_path,"warp-all"),
                      "network":"tcp,udp","outboundTag":"warp"})
    else:
        rules.append({**match,"ruleTag":managed_rule_tag(scope,inbound_id,access_path,"direct"),"outboundTag":"direct"})
    return rules
