"""Graph manager, architecture state integrity, splitter acceptance rules."""
import numpy as np

from app.adaptation.module_splitter import plan_split
from app.adaptation.edge_manager import plan_shortcut
from app.graph.manager import GraphManager, graph_from_state
from app.versioning.state import (ArchitectureState, EdgeSpec, ModuleSpec, centroid, edge_id_for,
                                  hierarchy_edges_from_parents)
from tests.conftest import make_settings


def state_with(mods):
    s = ArchitectureState()
    for m, ids in mods.items():
        s.modules[m] = ModuleSpec(m, m.title(), profile_vector=[0.1] * 384)
        s.assignments[m] = ids
    return s


def test_state_validation_catches_dangling_references_duplicates_and_cycles():
    s = state_with({"a": ["m1", "m2"], "b": ["m3"]})
    assert s.validate(384) == []
    s.assignments["a"].append("m3")
    s.edges["edge_x"] = EdgeSpec("edge_x", "a", "ghost", "co_access")
    s.modules["a"].parent_id, s.modules["b"].parent_id = "b", "a"
    problems = " ".join(s.validate(384))
    assert "assigned to both" in problems and "missing module" in problems and "parent cycle" in problems


def test_checksum_changes_with_structure_but_not_with_timestamps():
    s = state_with({"a": ["m1"], "b": ["m2"]})
    c1 = s.checksum()
    s.modules["a"].last_change_at = "2026-01-01"
    assert s.checksum() == c1
    s.assignments["b"].append("m9")
    assert s.checksum() != c1


def test_typed_multigraph_keeps_hierarchy_and_co_access_between_same_pair():
    s = state_with({"a": ["m1"], "b": ["m2"]})
    s.modules["b"].parent_id = "a"
    hierarchy_edges_from_parents(s)
    eid = edge_id_for("co_access", "a", "b")
    s.edges[eid] = EdgeSpec(eid, "a", "b", "co_access", 0.3, 9)
    g = graph_from_state(s)
    assert g.number_of_edges() == 2 and {k for *_, k in g.edges(keys=True)} == {"hierarchy", "co_access"}
    gm = GraphManager()
    gm.replace_from_state(s)
    ex = gm.export()
    assert {e["data"]["relationType"] for e in ex["edges"]} == {"hierarchy", "co_access"}
    assert all("position" in n and n["data"]["recordCount"] >= 0 for n in ex["nodes"])
    assert gm.neighbors("b")[0]["relation_type"] in ("co_access", "hierarchy")


def test_graph_is_rebuilt_from_durable_documents(seeded):
    ctx = seeded
    ctx.graph.replace_from_state(ArchitectureState())  # lose the in-process graph
    assert ctx.graph.counts() == (0, 0)
    ctx.persistence.reload_graph()
    assert ctx.graph.counts() == (ctx.persistence.export_live_state().modules.__len__(),
                                  len(ctx.persistence.export_live_state().edges))
    assert ctx.validator.check()["passed"]


# ------------------------------------------------------------------ splitter
def clusters(rng, k, per, dim=384, noise=0.05):
    centers = rng.normal(size=(k, dim))
    out = {}
    for c in range(k):
        for i in range(per):
            v = centers[c] + rng.normal(size=dim) * noise * np.abs(centers[c]).mean()
            out[f"m{c}_{i}"] = (v / np.linalg.norm(v)).astype(np.float32)
    return out


def test_splitter_accepts_separated_clusters_and_rejects_random_vectors(tmp_path):
    rng = np.random.default_rng(0)
    s = make_settings(tmp_path, split_min_silhouette=0.25, split_min_group_size=4, split_max_groups=4)
    vecs = clusters(rng, 3, 6)
    meta = {m: {"source_id": "src", "source_name": "one.md", "section": None, "text": f"alpha{m[1]} word"} for m in vecs}
    st = state_with({"big": sorted(vecs)})
    plan, diag = plan_split(st, "big", vecs, meta, s, "op1", "now")
    assert plan is not None and plan.strategy.startswith("kmeans") and plan.silhouette > 0.25
    assert sorted(g["records"] for g in plan.groups) == [6, 6, 6]
    assert plan.new_state.validate(384) == [] and plan.new_state.assignments["big"] == []
    assert sum(len(v) for k, v in plan.new_state.assignments.items()) == 18  # every record keeps exactly one module
    # random vectors have no structure -> no split is proposed
    rnd = {f"r{i}": (lambda v: (v / np.linalg.norm(v)).astype(np.float32))(rng.normal(size=384)) for i in range(18)}
    meta2 = {m: {"source_id": "src", "source_name": "one.md", "section": None, "text": "x"} for m in rnd}
    plan2, diag2 = plan_split(state_with({"big": sorted(rnd)}), "big", rnd, meta2, s, "op", "now")
    assert plan2 is None and "coherent" in diag2["reason"]


def test_splitter_prefers_metadata_boundaries_when_they_are_coherent(tmp_path):
    rng = np.random.default_rng(1)
    s = make_settings(tmp_path, split_min_silhouette=0.25, split_min_group_size=4)
    vecs = clusters(rng, 2, 8)
    meta = {m: {"source_id": f"src{m[1]}", "source_name": f"topic{m[1]}.md", "section": "S", "text": f"kw{m[1]}"} for m in vecs}
    plan, _ = plan_split(state_with({"big": sorted(vecs)}), "big", vecs, meta, s, "op", "now")
    assert plan.strategy == "by_source" and {g["name"] for g in plan.groups} == {"Topic0", "Topic1"}


def test_splitter_enforces_minimum_group_size(tmp_path):
    rng = np.random.default_rng(2)
    s = make_settings(tmp_path, split_min_group_size=5)
    vecs = clusters(rng, 2, 3)
    meta = {m: {"source_id": f"s{m[1]}", "source_name": "a", "section": None, "text": "t"} for m in vecs}
    plan, diag = plan_split(state_with({"big": sorted(vecs)}), "big", vecs, meta, s, "op", "now")
    assert plan is None and "need at least" in diag["reason"]


def test_shortcut_planner_enforces_thresholds_duplicates_and_edge_budget(tmp_path):
    s = make_settings(tmp_path, coaccess_min_count=10, coaccess_min_window_queries=20, max_edges_total=1,
                      max_coaccess_per_node=1)
    st = state_with({"a": ["1"], "b": ["2"], "c": ["3"]})
    plan, ev = plan_shortcut(st, "a", "b", 9, 50, s, "op", "now")
    assert plan is None and "co-access count 9 < 10" in ev["blockers"][0]
    plan, ev = plan_shortcut(st, "a", "b", 10, 12, s, "op", "now")
    assert plan is None and "only 12 queries" in " ".join(ev["blockers"])
    plan, ev = plan_shortcut(st, "a", "b", 12, 50, s, "op", "now")
    assert plan and plan.new_state.edges[edge_id_for("co_access", "a", "b")].weight == 0.24
    plan2, ev2 = plan_shortcut(plan.new_state, "a", "b", 12, 50, s, "op", "now")
    assert plan2 is None and "already exists" in ev2["blockers"][0]
    plan3, ev3 = plan_shortcut(plan.new_state, "b", "c", 12, 50, s, "op", "now")
    assert plan3 is None and any("edge budget" in b for b in ev3["blockers"])
