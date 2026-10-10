"""End-to-end M2 behavior on the benchmark fixtures (six M2 scenarios plus the three M1 fixtures)."""

import json
from pathlib import Path

import jsonschema
import pytest

from qplace import build_cluster_model, load_snapshot
from qplace.placement_assess import Assessment, Finding, Outcome, assess_placement
from qplace.placement_serialize import benchmark_results_dict, placement_problem_dict, placement_results_dict
from qplace.serialize import cluster_model_dict, dumps
from qplace.snapshot import load_schema
from tests.conftest import ROOT, build_model, cand, raw_node

M2_FIXTURES = sorted((ROOT / "fixtures" / "m2").glob("scenario_*.json"))
ALL_FIXTURES = M2_FIXTURES + sorted((ROOT / "fixtures" / "m1").glob("fixture_*.json"))


def run(name):
    path = next(p for p in ALL_FIXTURES if p.stem == name)
    model = build_cluster_model(load_snapshot(path))
    return model, assess_placement(model)


@pytest.fixture(params=ALL_FIXTURES, ids=lambda p: p.stem)
def bench(request):
    model = build_cluster_model(load_snapshot(request.param))
    return request.param, model, assess_placement(model)


def test_scenario_a_complete_feasible():
    _, r = run("scenario_a_feasible")
    assert r.assessment is Assessment.MODELED_FEASIBLE
    assert r.greedy.outcome is Outcome.COMPLETE and r.exact.outcome is Outcome.COMPLETE
    assert r.exact.result.optimality_proven and not r.greedy.result.optimality_proven


def test_scenario_b_greedy_failure_is_not_infeasibility():
    _, r = run("scenario_b_greedy_trap")
    assert r.greedy.outcome is Outcome.PARTIAL and r.exact.outcome is Outcome.COMPLETE
    assert r.assessment is Assessment.MODELED_FEASIBLE and r.greedy_gap == 1
    assert Finding.GREEDY_MISSED_FEASIBLE_ASSIGNMENT in r.findings
    assert r.exact.result.assignment_map() == {"apps/batch-7a6b5c-aaaaa": "node-b",
                                               "apps/indexer-8b7c6d-aaaaa": "node-a"}


def test_scenario_c_best_partial_and_proven_blocked():
    _, r = run("scenario_c_aggregate_shortage")
    assert r.exact.outcome is Outcome.PARTIAL and len(r.exact.result.assignments) == 2
    assert r.exact.result.optimality_proven and r.assessment is Assessment.MODELED_BLOCKED
    assert Finding.COMPLETE_ASSIGNMENT_IMPOSSIBLE_IN_MODEL in r.findings


def test_scenario_d_eligibility_respected():
    model, r = run("scenario_d_eligibility")
    for ev in (r.greedy, r.exact):
        for a in ev.result.assignments:
            assert a.node in model.eligible_destinations(a.pod)
        assert ev.outcome is Outcome.COMPLETE
    assert r.exact.result.assignment_map()["data/db-1a2b3c-aaaaa"] == "node-db"


def test_scenario_e_uncertainty_preserved():
    _, r = run("scenario_e_uncertain")
    assert r.assessment is Assessment.REVIEW_REQUIRED
    assert r.exact.outcome is Outcome.PARTIAL  # the unknown pods are never placed
    placed = r.exact.result.assignment_map()
    assert "apps/spread-5e6f7a-aaaaa" not in placed and "apps/resizer-4d5e6f-aaaaa" not in placed
    codes = {str(c.code) for c in r.problem.conditions}
    assert {"ELIGIBILITY_UNKNOWN", "CANDIDATE_REQUEST_UNKNOWN"} <= codes


def test_scenario_f_pod_slots_block():
    model, r = run("scenario_f_pod_slots")
    assert len(r.exact.result.assignments) == 1 and r.assessment is Assessment.MODELED_BLOCKED
    assert model.resources.nodes["node-1"].remaining_pod_slots == 1


def test_m1_review_required_values_stay_conditional():
    _, r = run("fixture_b_insufficient")  # pod-level requests on 1.33 -> REVIEW_REQUIRED
    assert r.assessment is Assessment.REVIEW_REQUIRED
    assert Finding.KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES in r.findings  # shortage kept alongside uncertainty
    assert "worker-03" not in {a.node for a in r.exact.result.assignments}  # overcommitted, excluded


def test_incomplete_inventory_never_modeled_feasible():
    m = build_model([raw_node("src"), raw_node("n1")], [cand("a", "1m", "1Mi")], pods_inventory="partial")
    r = assess_placement(m)
    assert r.assessment is Assessment.REVIEW_REQUIRED and not r.exact.result.assignments


def test_empty_candidate_set_assessment():
    r = assess_placement(build_model([raw_node("src"), raw_node("n1")], []))
    assert r.exact.outcome is Outcome.COMPLETE and r.assessment is Assessment.MODELED_FEASIBLE


def test_every_solver_result_is_verified(bench):
    _, model, r = bench
    for ev in (r.greedy, r.exact):
        assert ev.verification.valid, ev.verification.errors
        placed = [a.pod for a in ev.result.assignments]
        assert len(placed) == len(set(placed))
        assert set(placed) | {u.pod for u in ev.result.unplaced} == set(model.drain.placement_candidates)
    if r.exact.result.optimality_proven:
        assert len(r.exact.result.assignments) >= len(r.greedy.result.assignments)


def test_assessment_invariants(bench):
    _, _, r = bench
    if r.assessment is Assessment.MODELED_FEASIBLE:
        assert not r.problem.conditions and r.exact.outcome is Outcome.COMPLETE
    if r.assessment is Assessment.MODELED_BLOCKED:
        assert not r.problem.conditions and r.exact.result.optimality_proven


def test_artifacts_schema_valid_deterministic_and_committed(bench):
    path, model, r = bench
    docs = {
        "placement_problem": placement_problem_dict(r.problem),
        "placement_results": placement_results_dict(r),
        "benchmark_results": benchmark_results_dict(path.stem, model, repeats=1),
    }
    for kind, doc in docs.items():
        jsonschema.Draft202012Validator(load_schema(kind.replace("_", "-") + "-1.0.schema.json")).validate(doc)
    again = assess_placement(build_cluster_model(load_snapshot(path)))
    assert dumps(placement_results_dict(again)) == dumps(docs["placement_results"])
    out = ROOT / "artifacts" / "m2" / path.stem
    assert (out / "placement_problem.json").read_text() == dumps(docs["placement_problem"])
    assert (out / "placement_results.json").read_text() == dumps(docs["placement_results"])
    committed = json.loads((out / "benchmark_results.json").read_text())
    assert committed["deterministic"] == docs["benchmark_results"]["deterministic"]


def test_m2_does_not_mutate_m1_model(bench):
    path, _, _ = bench
    model = build_cluster_model(load_snapshot(path))
    before = dumps(cluster_model_dict(model))
    assess_placement(model)
    assert dumps(cluster_model_dict(model)) == before


def test_results_never_claim_drain_safety(bench):
    _, _, r = bench
    text = dumps(placement_results_dict(r))
    assert "SAFE_TO_DRAIN\"" not in text.replace("is not SAFE_TO_DRAIN", "")


# --- regression tests for the independent review findings -------------------------------


def test_review_f1_policy_exclusion_cannot_produce_modeled_blocked():
    # d0 is overcommitted on CPU; M1 says the zero-CPU candidate is ELIGIBLE there and FITS
    # (NodeResourcesFit skips unrequested resources). The M2 policy excludes d0, so the shortage
    # is a policy outcome, not a proof.
    from tests.conftest import container, raw_pod
    hog = raw_pod(name="hog", node="d0", containers=[container(requests={"cpu": "2", "memory": "1Gi"})])
    m = build_model([raw_node("src"), raw_node("d0", cpu="1")], [hog, cand("c0", "0", "1Gi")])
    r = assess_placement(m)
    assert r.problem.policy_excluded_pairs == (("ns/c0", "d0"),)
    assert r.assessment is Assessment.REVIEW_REQUIRED
    assert Finding.POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE in r.findings


def test_review_f1_policy_exclusion_does_not_block_feasibility_elsewhere():
    from tests.conftest import container, raw_pod
    hog = raw_pod(name="hog", node="d0", containers=[container(requests={"cpu": "2", "memory": "1Gi"})])
    m = build_model([raw_node("src"), raw_node("d0", cpu="1"), raw_node("d1")], [hog, cand("c0", "1", "1Gi")])
    r = assess_placement(m)
    assert r.assessment is Assessment.MODELED_FEASIBLE and r.exact.result.assignment_map() == {"ns/c0": "d1"}


def test_review_f3_unbound_pending_pods_make_result_conditional():
    from tests.conftest import raw_pod
    pending = raw_pod(name="waiting", node=None, phase="Pending")
    r = assess_placement(build_model([raw_node("src"), raw_node("d0")], [pending, cand("c0")]))
    assert "COMPETING_DEMAND_UNBOUND_PODS" in {str(c.code) for c in r.problem.conditions}
    assert r.assessment is Assessment.REVIEW_REQUIRED


def test_review_f3_terminating_target_pod_makes_result_conditional():
    term = cand("old", "3", "7Gi", metadata={"deletionTimestamp": "2026-10-10T00:00:00Z"})
    r = assess_placement(build_model([raw_node("src"), raw_node("d0")], [term, cand("c0")]))
    assert "TERMINATING_TARGET_POD" in {str(c.code) for c in r.problem.conditions}
    assert r.assessment is Assessment.REVIEW_REQUIRED


def test_review_f4_zero_allocatable_destination_counts_as_full():
    from qplace.placement_problem import DestinationSpec, free_fraction
    from qplace.resources import AccountingStatus
    d = DestinationSpec("z", 0, 0, 0, 0, 0, 0, AccountingStatus.EXACT, ())
    assert free_fraction(d, 0, 0, 0) == 0


def test_review_generator_script_rejects_unknown_arguments():
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}  # do not depend on an installed package
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_m2_artifacts.py"), "--help"],
                          capture_output=True, text=True, cwd=ROOT, env=env)
    assert proc.returncode == 2 and "unknown argument" in proc.stderr
