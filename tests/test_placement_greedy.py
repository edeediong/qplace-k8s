"""Deterministic first-fit-decreasing greedy solver."""

from qplace.placement_greedy import solve_greedy
from qplace.placement_problem import Code, build_placement_problem
from qplace.placement_result import Termination
from tests.conftest import build_model, cand, raw_node


def solve(nodes, pods, **kw):
    return solve_greedy(build_placement_problem(build_model(nodes, pods, **kw)))


def test_first_fit_uses_first_destination_by_name():
    r = solve([raw_node("src"), raw_node("b-node"), raw_node("a-node")], [cand("p")])
    assert r.assignment_map() == {"ns/p": "a-node"}
    assert r.optimality_proven is False and r.termination is Termination.COMPLETED


def test_capacity_is_updated_after_each_assignment():
    # a-node fits one 3-CPU pod; the second must spill to b-node.
    r = solve([raw_node("src", cpu="8"), raw_node("a-node", cpu="4"), raw_node("b-node", cpu="4")],
              [cand("x", "3", "1Gi"), cand("y", "3", "1Gi")])
    assert r.assignment_map() == {"ns/x": "a-node", "ns/y": "b-node"}


def test_exact_capacity_equality_fits():
    r = solve([raw_node("src"), raw_node("n1", cpu="1", memory="2Gi", pods="2")],
              [cand("x", "500m", "1Gi"), cand("y", "500m", "1Gi")])
    assert len(r.assignments) == 2 and not r.unplaced


def test_unplaced_pod_records_every_rejection():
    r = solve([raw_node("src"), raw_node("n1", cpu="1"), raw_node("n2", labels={"t": "x"},
               taints=[{"key": "t", "value": "x", "effect": "NoSchedule"}])], [cand("big", "2")])
    (u,) = r.unplaced
    assert u.reasons == (Code.NOT_SELECTED,)
    assert {(x.node, x.code) for x in u.rejections} == {("n1", Code.INSUFFICIENT_CPU), ("n2", Code.NOT_ELIGIBLE)}


def test_zero_request_pod_is_placed():
    r = solve([raw_node("src"), raw_node("n1")], [cand("z", "0", "0")])
    assert r.assignment_map() == {"ns/z": "n1"}


def test_no_eligible_destination_leaves_pod_unplaced():
    r = solve([raw_node("src"), raw_node("n1", ready="False")], [cand("p")])
    assert not r.assignments and r.unplaced[0].reasons == (Code.NO_ALLOWED_DESTINATION,)


def test_empty_candidate_set():
    r = solve([raw_node("src"), raw_node("n1")], [])
    assert r.assignments == () and r.unplaced == () and r.objective.assigned == 0


def test_greedy_ordering_trap():
    # FFD places the 2-CPU pod first on node-a (first by name); the selector-bound pod then has nowhere to go.
    nodes = [raw_node("src", labels={"disk": "ssd"}), raw_node("node-a", cpu="2500m", labels={"disk": "ssd"}),
             raw_node("node-b", cpu="2500m", labels={"disk": "hdd"})]
    r = solve(nodes, [cand("big", "2"), cand("ssd", "1500m", nodeSelector={"disk": "ssd"})])
    assert r.assignment_map() == {"ns/big": "node-a"}
    assert {(x.node, x.code) for x in r.unplaced[0].rejections} == {
        ("node-a", Code.INSUFFICIENT_CPU), ("node-b", Code.NOT_ELIGIBLE)}
