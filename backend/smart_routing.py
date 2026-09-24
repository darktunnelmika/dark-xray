#!/usr/bin/env python3
"""Safe Smart WARP / Adblock routing planner for DARK XRAY.

Stage 7 deliberately starts with preview-only helpers.  The functions below do
not touch a live Xray process, do not create WireGuard credentials, and do not
make network calls.  They prepare deterministic routing patches that can be
shown in the UI and reviewed before a later apply step.
"""
from __future__ import annotations

import copy
import statistics
from typing import Any


class SmartRoutingError(ValueError):
    """Raised when a requested smart-routing preview would be unsafe."""


WARP_AI_REGION_HINTS = {
    "us", "usa", "united states", "america", "united-states", "united_states",
    "de", "deu", "germany", "deutschland",
}
ADBLOCK_REGION_HINTS = {
    "fr", "fra", "france",
    "gb", "gbr", "uk", "united kingdom", "united-kingdom", "united_kingdom", "england",
}

AI_DOMAIN_MATCHERS = [
    "domain:openai.com",
    "domain:chatgpt.com",
    "domain:oaiusercontent.com",
    "domain:oaistatic.com",
    "domain:openaiapi-site.azureedge.net",
]

ADBLOCK_DOMAIN_MATCHERS = [
    "geosite:category-ads-all",
    "domain:doubleclick.net",
    "domain:googleadservices.com",
    "domain:googlesyndication.com",
    "domain:adservice.google.com",
    "domain:ads-twitter.com",
]

STAGE7_RULE_TAGS = {"dark-smart-adblock", "dark-smart-warp-ai"}
STAGE7_BALANCER_TAG = "dark-smart-warp-ai-balancer"
DEFAULT_WARP_SCAN_TARGETS = ["1.1.1.1:443", "1.0.0.1:443", "www.gstatic.com:443"]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).lower()
    return ""


def _node_text(node: dict[str, Any]) -> str:
    fields = [
        "id", "name", "remark", "region", "country", "country_code", "countryCode",
        "location", "data_address", "dataAddress", "origin",
    ]
    return " ".join(_as_text(node.get(k)) for k in fields)


def _matches_hint(text: str, hints: set[str]) -> list[str]:
    normalized = text.replace("_", " ").replace("-", " ")
    matched = []
    for hint in sorted(hints):
        h = hint.replace("_", " ").replace("-", " ")
        if f" {h} " in f" {normalized} " or normalized.startswith(h + " ") or normalized.endswith(" " + h):
            matched.append(hint)
    return matched


def classify_nodes(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify existing nodes into Smart WARP and Smart Adblock candidate groups."""
    warp_nodes: list[dict[str, Any]] = []
    adblock_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("enabled") is False or node.get("online") is False:
            continue
        text = _node_text(node)
        warp_hits = _matches_hint(text, WARP_AI_REGION_HINTS)
        ad_hits = _matches_hint(text, ADBLOCK_REGION_HINTS)
        row = {
            "id": str(node.get("id", "")),
            "name": str(node.get("name") or node.get("id") or ""),
            "data_address": str(node.get("data_address") or node.get("dataAddress") or ""),
        }
        if warp_hits:
            warp_nodes.append({**row, "matched": warp_hits})
        if ad_hits:
            adblock_nodes.append({**row, "matched": ad_hits})
    return {
        "warp_ai": {"ready": bool(warp_nodes), "nodes": warp_nodes, "region_hints": ["US", "DE"]},
        "adblock": {"ready": bool(adblock_nodes), "nodes": adblock_nodes, "region_hints": ["FR", "UK"]},
    }


def _known_tags(outbounds: list[dict[str, Any]]) -> set[str]:
    return {str(o.get("tag")) for o in outbounds if isinstance(o, dict) and o.get("tag")}


def _dedupe_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str) or not tag:
            raise SmartRoutingError("Outbound tag must be a nonempty string")
        if tag not in seen:
            out.append(tag)
            seen.add(tag)
    return out


def _clean_stage7_routing(routing: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(routing) if isinstance(routing, dict) else {"domainStrategy": "AsIs", "rules": []}
    rules = result.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    result["rules"] = [r for r in rules if not (isinstance(r, dict) and r.get("ruleTag") in STAGE7_RULE_TAGS)]
    balancers = result.get("balancers", [])
    if isinstance(balancers, list):
        result["balancers"] = [b for b in balancers if not (isinstance(b, dict) and b.get("tag") == STAGE7_BALANCER_TAG)]
    return result


def build_stage7_patch(
    outbounds: list[dict[str, Any]],
    routing: dict[str, Any],
    *,
    warp_outbound_tags: list[str] | None = None,
    enable_warp_ai: bool = True,
    enable_adblock: bool = True,
) -> dict[str, Any]:
    """Return preview-only Xray settings for Stage 7 smart routing.

    The returned outbounds/routing can be validated by CoreEngine.save_section in
    a later apply flow.  This function never mutates input values.
    """
    if not isinstance(outbounds, list) or not all(isinstance(o, dict) for o in outbounds):
        raise SmartRoutingError("outbounds must be a list of objects")
    known = _known_tags(outbounds)
    if "block" not in known and enable_adblock:
        raise SmartRoutingError("Adblock requires an existing blackhole outbound tagged 'block'")

    routing_patch = _clean_stage7_routing(routing)
    rules_to_prepend: list[dict[str, Any]] = []
    warnings: list[str] = []
    balancer: dict[str, Any] | None = None
    warp_tags = _dedupe_tags(warp_outbound_tags or [])

    if enable_adblock:
        rules_to_prepend.append({
            "type": "field",
            "ruleTag": "dark-smart-adblock",
            "domain": ADBLOCK_DOMAIN_MATCHERS[:],
            "outboundTag": "block",
        })

    if enable_warp_ai:
        if not warp_tags:
            warnings.append("Smart WARP AI is enabled in preview but no WARP outbound tags were selected yet.")
        else:
            missing = [tag for tag in warp_tags if tag not in known]
            if missing:
                raise SmartRoutingError("Unknown WARP outbound tag(s): " + ", ".join(missing))
            if len(warp_tags) == 1:
                target = {"outboundTag": warp_tags[0]}
            else:
                balancer = {
                    "tag": STAGE7_BALANCER_TAG,
                    "selector": warp_tags[:],
                    "fallbackTag": warp_tags[0],
                    "strategy": {"type": "leastPing"},
                }
                target = {"balancerTag": STAGE7_BALANCER_TAG}
            rules_to_prepend.append({
                "type": "field",
                "ruleTag": "dark-smart-warp-ai",
                "domain": AI_DOMAIN_MATCHERS[:],
                **target,
            })

    if balancer:
        routing_patch.setdefault("balancers", [])
        routing_patch["balancers"].append(balancer)
    routing_patch["rules"] = rules_to_prepend + list(routing_patch.get("rules", []))

    observatory_patch = None
    if enable_warp_ai and len(warp_tags) > 1:
        observatory_patch = {
            "subjectSelector": warp_tags[:],
            "probeURL": "https://www.gstatic.com/generate_204",
            "probeInterval": "1m",
            "enableConcurrency": True,
        }

    return {
        "previewOnly": True,
        "changed": bool(rules_to_prepend or balancer),
        "warnings": warnings,
        "outbounds": copy.deepcopy(outbounds),
        "routing": routing_patch,
        "observatory": observatory_patch,
        "scanPlan": {"targets": DEFAULT_WARP_SCAN_TARGETS[:], "mode": "review_before_apply"},
        "notes": [
            "Preview only: no live Xray restart and no customer traffic change.",
            "WARP outbounds must already exist before Smart WARP AI can be applied.",
            "Adblock uses the existing blackhole outbound tagged block.",
        ],
    }


def rank_warp_paths(observations: list[dict[str, Any]], *, max_results: int = 8) -> list[dict[str, Any]]:
    """Rank pre-collected WARP path observations by health, latency and loss."""
    ranked: list[dict[str, Any]] = []
    for raw in observations:
        if not isinstance(raw, dict):
            continue
        tag = str(raw.get("tag") or raw.get("outboundTag") or raw.get("nodeId") or "")
        if not tag:
            continue
        samples = raw.get("latencyMs", raw.get("latenciesMs", []))
        if isinstance(samples, (int, float)):
            samples = [float(samples)]
        if not isinstance(samples, list):
            samples = []
        nums = [float(x) for x in samples if isinstance(x, (int, float)) and x >= 0]
        ok = bool(raw.get("ok", bool(nums)))
        loss = float(raw.get("lossPercent", 0 if nums else 100))
        latency = statistics.median(nums) if nums else 999999.0
        jitter = (max(nums) - min(nums)) if len(nums) > 1 else 0.0
        score = (0 if ok else 1, loss, latency, jitter, tag)
        ranked.append({
            "tag": tag,
            "ok": ok,
            "latencyMs": round(latency, 3) if latency < 999999 else None,
            "jitterMs": round(jitter, 3),
            "lossPercent": loss,
            "score": list(score[:-1]),
        })
    ranked.sort(key=lambda x: (0 if x["ok"] else 1, x["lossPercent"], x["latencyMs"] if x["latencyMs"] is not None else 999999, x["jitterMs"], x["tag"]))
    return ranked[:max(1, min(max_results, 50))]


def build_stage7_plan(nodes: list[dict[str, Any]], outbounds: list[dict[str, Any]], routing: dict[str, Any]) -> dict[str, Any]:
    tags = sorted(_known_tags(outbounds))
    stage7_rules = [r for r in routing.get("rules", []) if isinstance(r, dict) and r.get("ruleTag") in STAGE7_RULE_TAGS]
    return {
        "stage": "stage7-smart-routing",
        "safeDefault": "preview_only",
        "nodes": classify_nodes(nodes),
        "outboundTags": tags,
        "configuredWarpCandidates": [t for t in tags if t.startswith(("warp", "wg-warp", "dark-warp"))],
        "activeStage7Rules": stage7_rules,
        "scanPlan": {"targets": DEFAULT_WARP_SCAN_TARGETS[:], "mode": "manual_or_scheduled_probe"},
        "capabilities": {
            "smartWarpAiPreview": True,
            "smartAdblockPreview": True,
            "automaticApply": False,
            "productionTrafficMutation": False,
        },
    }
