"""Placement problem construction from an M1 model."""

import copy
import json
import random

from qplace import build_cluster_model, load_snapshot_data
from qplace.placement_problem import Code, build_placement_problem, placement_order
from qplace.placement_serialize import placement_problem_dict
from qplace.resources import AccountingStatus
from qplace.serialize import cluster_model_dict, dumps
from tests.conftest import GI, build_model, cand, raw_node, raw_pod, container, snapshot_data


def codes(conds):
    return {c.code for c in conds}


def test_basic_problem_shape_and_units():
    m = build_model([raw_node("src"), raw_node("n1", cpu="4", memory="8Gi")], [cand("a", "1500m", "1Gi")])
    p = build_placement_problem(m)
    assert [d.node for d in p.destinations] == ["n1"]  # drain target is never a destination
    (c,) = p.candidates
    assert (c.pod, c.cpu_millicores, c.memory_bytes, c.allowed_nodes) == ("ns/a", 1500, GI, ("n1",))
    assert p.variables == (("ns/a", "n1"),)
    assert not p.conditions
    assert Code.REPLACEMENT_TEMPLATE_UNKNOWN in codes(p.limitations)


def test_only_eligible_pairs_become_variables():
    nodes = [raw_node("src"), raw_node("ssd", labels={"disk": "ssd"}), raw_node("hdd", labels={"disk": "hdd"})]
    m = build_model(nodes, [cand("a", nodeSelector={"disk": "ssd"})])
    c = build_placement_problem(m).candidates[0]
    assert c.allowed_nodes == ("ssd",) and c.ineligible_nodes == ("hdd",)


def test_overcommitted_destination_excluded_with_negative_values_preserved():
    hog = raw_pod(name="hog", node="n1", containers=[container(requests={"cpu": "6", "memory": "1Gi"})])
    m = build_model([raw_node("src"), raw_node("n1", cpu="4")], [hog, cand("a", "0", "0")])
    p = build_placement_problem(m)
    d = p.destination("n1")
    assert not d.usable and Code.BASELINE_OVERCOMMITTED in d.exclusion_reasons
    assert d.cpu_millicores == -2000  # not clamped
    assert p.candidates[0].allowed_nodes == ()  # even a zero-request pod: deliberate planning restriction
    assert Code.OVERCOMMITTED_DESTINATION_EXCLUDED in codes(p.limitations)


def test_unknown_request_excludes_candidate_and_is_a_condition():
    bad = raw_pod(name="bad", node="src", containers=[container(requests={"cpu": "lots"})])
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1")], [bad]))
    c = p.candidates[0]
    assert not c.usable and Code.REQUEST_UNKNOWN in c.exclusion_reasons
    assert c.cpu_millicores is None  # never coerced to zero
    assert Code.CANDIDATE_REQUEST_UNKNOWN in codes(p.conditions)
    assert p.variables == ()


def test_unknown_eligibility_is_kept_as_condition_not_variable():
    pod = cand("a", schedulerName="custom")
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1")], [pod]))
    c = p.candidates[0]
    assert c.unknown_nodes == ("n1",) and c.allowed_nodes == ()
    assert Code.ELIGIBILITY_UNKNOWN in codes(p.conditions)


def test_review_required_request_is_usable_but_conditional():
    pod = cand("a", "200m", "64Mi", resources={"requests": {"cpu": "1"}})
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1")], [pod], version="v1.33.0"))
    c = p.candidates[0]
    assert c.usable and c.request_status is AccountingStatus.REVIEW_REQUIRED and c.cpu_millicores == 1000
    assert Code.CANDIDATE_REQUEST_REVIEW_REQUIRED in codes(p.conditions)


def test_incomplete_inventory_makes_problem_conditional_and_headroom_unknown():
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1")], [cand("a")],
                                            pods_inventory="partial"))
    assert Code.CAPACITY_ASSESSMENT_INCOMPLETE in codes(p.conditions)
    assert not p.destination("n1").usable and p.candidates[0].unknown_nodes == ("n1",)
    assert Code.DESTINATION_HEADROOM_UNKNOWN in codes(p.conditions)


def test_job_and_unknown_controller_replacements_are_conditions():
    pods = [cand("j", owner=("batch/v1", "Job")), cand("w", owner=("example.com/v1", "Widget"))]
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1")], pods))
    assert {Code.REPLACEMENT_CONDITIONAL, Code.REPLACEMENT_UNKNOWN} <= codes(p.conditions)


def test_placement_order_is_cpu_then_memory_then_key():
    pods = [cand("c", "1", "1Gi"), cand("b", "1", "2Gi"), cand("a", "2", "1Gi"), cand("d", "1", "1Gi")]
    p = build_placement_problem(build_model([raw_node("src", cpu="8"), raw_node("n1")], pods))
    assert [c.pod for c in placement_order(p)] == ["ns/a", "ns/b", "ns/c", "ns/d"]


def test_problem_independent_of_input_order_and_does_not_mutate_model():
    nodes = [raw_node("src", cpu="16"), raw_node("n1"), raw_node("n2", labels={"x": "y"})]
    pods = [cand(n, f"{i + 1}00m") for i, n in enumerate("abcde")]
    data = snapshot_data(nodes, pods, "src")
    shuffled = copy.deepcopy(data)
    random.Random(7).shuffle(shuffled["nodes"])
    random.Random(7).shuffle(shuffled["pods"])
    m1 = build_cluster_model(load_snapshot_data(data))
    before = dumps(cluster_model_dict(m1))
    p1 = build_placement_problem(m1)
    p2 = build_placement_problem(build_cluster_model(load_snapshot_data(shuffled)))
    assert dumps(placement_problem_dict(p1)) == dumps(placement_problem_dict(p2))
    assert dumps(cluster_model_dict(m1)) == before
    assert json.loads(dumps(placement_problem_dict(p1)))["summary"]["variables"] == 10
