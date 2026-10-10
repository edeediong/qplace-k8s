"""Independent verifier: every hard check, aggregate limits checked after collecting all pairs."""

from qplace.placement_verify import Violation, verify_placement
from tests.conftest import build_model, cand, container, raw_node, raw_pod


def model(**kw):
    nodes = [raw_node("src"), raw_node("n1", cpu="2", memory="2Gi", pods="3"),
             raw_node("n2", labels={"disk": "hdd"}), raw_node("ssd", labels={"disk": "ssd"})]
    pods = [cand("a", "1", "1Gi"), cand("b", "1", "1Gi"), cand("c", "1", "1Gi"),
            cand("sel", "100m", "64Mi", nodeSelector={"disk": "ssd"}),
            raw_pod(name="standalone", node="src", owner=None)]
    return build_model(nodes, pods, **kw)


def codes(result):
    return {e.code for e in result.errors}


def test_valid_assignment_passes_with_usage():
    r = verify_placement(model(), [("ns/a", "n1"), ("ns/b", "n1"), ("ns/sel", "ssd")])
    assert r.valid and not r.errors
    n1 = next(u for u in r.usage if u.node == "n1")
    assert n1.remaining_after == {"cpu_millicores": 0, "memory_bytes": 0, "pods": 1}  # exact equality is allowed


def test_duplicate_assignment_rejected():
    assert Violation.DUPLICATE_ASSIGNMENT in codes(verify_placement(model(), [("ns/a", "n1"), ("ns/a", "n2")]))


def test_source_node_rejected():
    assert Violation.DESTINATION_IS_DRAIN_TARGET in codes(verify_placement(model(), [("ns/a", "src")]))


def test_ineligible_node_rejected():
    assert Violation.DESTINATION_NOT_ELIGIBLE in codes(verify_placement(model(), [("ns/sel", "n2")]))


def test_unknown_node_and_non_candidate_rejected():
    r = verify_placement(model(), [("ns/a", "ghost"), ("ns/standalone", "n2")])
    assert {Violation.UNKNOWN_DESTINATION, Violation.NOT_A_PLACEMENT_CANDIDATE} <= codes(r)


def test_aggregate_cpu_overflow_rejected_even_though_each_pod_fits_alone():
    r = verify_placement(model(), [("ns/a", "n1"), ("ns/b", "n1"), ("ns/c", "n1")])
    assert Violation.CPU_CAPACITY_EXCEEDED in codes(r) and not r.valid


def test_aggregate_memory_overflow_rejected():
    nodes = [raw_node("src"), raw_node("n1", cpu="8", memory="1Gi")]
    m = build_model(nodes, [cand("a", "100m", "600Mi"), cand("b", "100m", "600Mi")])
    r = verify_placement(m, [("ns/a", "n1"), ("ns/b", "n1")])
    assert codes(r) == {Violation.MEMORY_CAPACITY_EXCEEDED}


def test_aggregate_pod_slot_overflow_rejected():
    nodes = [raw_node("src"), raw_node("n1", cpu="8", memory="8Gi", pods="1")]
    m = build_model(nodes, [cand("a", "1m", "1Mi"), cand("b", "1m", "1Mi")])
    r = verify_placement(m, [("ns/a", "n1"), ("ns/b", "n1")])
    assert codes(r) == {Violation.POD_SLOTS_EXCEEDED}


def test_unknown_request_rejected():
    bad = raw_pod(name="bad", node="src", containers=[container(requests={"cpu": "?", "memory": "1Gi"})])
    r = verify_placement(build_model([raw_node("src"), raw_node("n1")], [bad]), [("ns/bad", "n1")])
    assert Violation.CPU_REQUEST_UNKNOWN in codes(r)


def test_unknown_headroom_rejected_for_partial_inventory():
    m = build_model([raw_node("src"), raw_node("n1")], [cand("a")], pods_inventory="partial")
    r = verify_placement(m, [("ns/a", "n1")])
    assert Violation.HEADROOM_UNKNOWN in codes(r)


def test_negative_headroom_rejects_even_zero_request_pod():
    hog = raw_pod(name="hog", node="n1", containers=[container(requests={"cpu": "5", "memory": "1Gi"})])
    m = build_model([raw_node("src"), raw_node("n1", cpu="4")], [hog, cand("z", "0", "0")])
    r = verify_placement(m, [("ns/z", "n1")])
    assert not r.valid and Violation.CPU_CAPACITY_EXCEEDED in codes(r)


def test_review_required_values_are_flagged():
    pod = cand("a", "200m", "64Mi", resources={"requests": {"cpu": "1"}})
    m = build_model([raw_node("src"), raw_node("n1")], [pod], version="v1.33.0")
    r = verify_placement(m, [("ns/a", "n1")])
    assert r.valid and r.review_required == ("pod/ns/a",)
