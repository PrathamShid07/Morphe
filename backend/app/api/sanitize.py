"""Sanitised views for ordinary visitors. Administrators keep the existing, complete responses.

Nothing here invents data: fields are only dropped or neutralised, so a visitor can never see operational internals
(capacity thresholds, access counters, adaptation history, edge evidence, memory text outside their own query results).
"""
from __future__ import annotations

from typing import Any

_TRACE_DROP = {"guided_result_ids", "architecture_version"}


def public_query_response(resp: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in resp.items() if k not in {"architecture_version", "trace", "results"}}
    out["architecture_version"] = None
    out["results"] = [{k: r.get(k) for k in ("memory_id", "text", "score", "module_id", "module_name", "source_name", "section")}
                      for r in resp.get("results", [])]
    if resp.get("trace"):
        out["trace"] = {k: v for k, v in resp["trace"].items() if k not in _TRACE_DROP}
    return out


def public_graph(g: dict[str, Any]) -> dict[str, Any]:
    nodes = [{**n, "data": {"label": n["data"]["label"], "recordCount": n["data"]["recordCount"], "parentId": n["data"]["parentId"],
                            "isContainer": n["data"]["isContainer"], "lastChangeOp": None, "lastChangeAt": None, "utilization": None}}
             for n in g["nodes"]]
    edges = [{**e, "data": {"relationType": e["data"]["relationType"], "weight": e["data"]["weight"],
                            "coAccessCount": e["data"]["coAccessCount"], "createdAt": None, "evidence": {}, "operationId": None}}
             for e in g["edges"]]
    return {"nodes": nodes, "edges": edges, "architecture_version": None, "counts": g["counts"]}


_MODULE_KEYS = ("module_id", "name", "parent_id", "children", "record_count", "is_container", "keywords", "description", "shortcut_edges")


def public_module(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in _MODULE_KEYS}


def public_module_detail(d: dict[str, Any]) -> dict[str, Any]:
    out = public_module(d)
    out["parent"] = public_module(d["parent"]) if d.get("parent") else None
    out["neighbors"] = [{k: n.get(k) for k in ("module_id", "name", "relation_type", "weight", "co_access_count", "direction", "record_count")}
                        for n in d.get("neighbors", [])]
    return out
