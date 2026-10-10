"""Node-level eligibility (taints/tolerations semantics per
https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/)."""

from qplace.diagnostics import Reason
from qplace.eligibility import CapacityFit, Eligibility, evaluate_capacity_fit, evaluate_node_eligibility
from qplace.resources import build_resource_inventory
from tests.conftest import COMPLETE, container, make_node, make_pod

TARGET = "drain-me"


def codes(findings):
    return {f.code for f in findings}


def test_plain_pod_on_healthy_node_is_eligible():
    r = evaluate_node_eligibility(make_pod(), make_node(), TARGET)
    assert r.state is Eligibility.ELIGIBLE and not r.blockers and not r.unknowns


def test_drain_target_is_never_a_destination():
    r = evaluate_node_eligibility(make_pod(node=TARGET), make_node(name=TARGET), TARGET)
    assert r.state is Eligibility.INELIGIBLE
    assert Reason.DRAIN_TARGET in codes(r.blockers)


def test_current_node_binding_is_not_a_constraint():
    # spec.nodeName of the existing pod must not pin its hypothetical replacement.
    r = evaluate_node_eligibility(make_pod(node=TARGET), make_node(name="other"), TARGET)
    assert r.state is Eligibility.ELIGIBLE


def test_matching_node_selector():
    pod = make_pod(nodeSelector={"disktype": "ssd"})
    assert evaluate_node_eligibility(pod, make_node(labels={"disktype": "ssd"}), TARGET).state is Eligibility.ELIGIBLE


def test_nonmatching_and_missing_node_selector_label():
    pod = make_pod(nodeSelector={"disktype": "ssd"})
    for labels in ({"disktype": "hdd"}, {}):
        r = evaluate_node_eligibility(pod, make_node(labels=labels), TARGET)
        assert r.state is Eligibility.INELIGIBLE
        assert Reason.NODE_SELECTOR_MISMATCH in codes(r.blockers)


def test_matching_toleration_equal_and_exists():
    taint = [{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]
    node = make_node(taints=taint)
    equal = make_pod(tolerations=[{"key": "dedicated", "operator": "Equal", "value": "db", "effect": "NoSchedule"}])
    exists = make_pod(tolerations=[{"key": "dedicated", "operator": "Exists"}])
    wildcard = make_pod(tolerations=[{"operator": "Exists"}])  # empty key + Exists tolerates everything
    for pod in (equal, exists, wildcard):
        assert evaluate_node_eligibility(pod, node, TARGET).state is Eligibility.ELIGIBLE


def test_unmatched_hard_taints_block():
    for effect in ("NoSchedule", "NoExecute"):
        node = make_node(taints=[{"key": "dedicated", "value": "db", "effect": effect}])
        wrong_value = make_pod(tolerations=[{"key": "dedicated", "value": "web", "effect": effect}])
        wrong_effect = make_pod(
            tolerations=[{"key": "dedicated", "value": "db", "effect": "NoExecute" if effect == "NoSchedule" else "NoSchedule"}]
        )
        for pod in (make_pod(), wrong_value, wrong_effect):
            r = evaluate_node_eligibility(pod, node, TARGET)
            assert r.state is Eligibility.INELIGIBLE
            assert Reason.UNTOLERATED_TAINT in codes(r.blockers)


def test_soft_taint_is_recorded_not_blocking():
    node = make_node(taints=[{"key": "dedicated", "value": "batch", "effect": "PreferNoSchedule"}])
    r = evaluate_node_eligibility(make_pod(), node, TARGET)
    assert r.state is Eligibility.ELIGIBLE
    assert Reason.UNTOLERATED_PREFER_NO_SCHEDULE_TAINT in codes(r.soft)


def test_unsupported_toleration_operator_is_unknown():
    node = make_node(taints=[{"key": "tier", "value": "3", "effect": "NoSchedule"}])
    pod = make_pod(tolerations=[{"key": "tier", "operator": "Lt", "value": "5", "effect": "NoSchedule"}])
    r = evaluate_node_eligibility(pod, node, TARGET)
    assert r.state is Eligibility.UNKNOWN
    assert Reason.UNSUPPORTED_TOLERATION_OPERATOR in codes(r.unknowns)


def test_cordoned_node_blocks_unless_unschedulable_taint_tolerated():
    node = make_node(unschedulable=True)
    assert evaluate_node_eligibility(make_pod(), node, TARGET).state is Eligibility.INELIGIBLE
    tolerant = make_pod(tolerations=[{"key": "node.kubernetes.io/unschedulable", "operator": "Exists",
                                      "effect": "NoSchedule"}])
    assert evaluate_node_eligibility(tolerant, node, TARGET).state is Eligibility.ELIGIBLE


def test_unready_node_blocks_and_missing_ready_is_unknown():
    for ready in ("False", "Unknown"):
        r = evaluate_node_eligibility(make_pod(), make_node(ready=ready), TARGET)
        assert r.state is Eligibility.INELIGIBLE and Reason.NODE_NOT_READY in codes(r.blockers)
    r = evaluate_node_eligibility(make_pod(), make_node(ready=None), TARGET)
    assert r.state is Eligibility.UNKNOWN and Reason.NODE_READY_CONDITION_MISSING in codes(r.unknowns)


def test_unsupported_hard_affinity_is_unknown_not_eligible():
    pod = make_pod(affinity={"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [{"key": "zone", "operator": "In", "values": ["a"]}]}]}}})
    r = evaluate_node_eligibility(pod, make_node(labels={"zone": "a"}), TARGET)
    assert r.state is Eligibility.UNKNOWN
    assert Reason.REQUIRED_NODE_AFFINITY_NOT_MODELED in codes(r.unknowns)


def test_unsupported_constraint_catalogue():
    pod = make_pod(
        containers=[container(requests={"cpu": "1", "nvidia.com/gpu": "1"},
                              ports=[{"containerPort": 80, "hostPort": 80}])],
        affinity={"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{"topologyKey": "x"}]}},
        topologySpreadConstraints=[{"maxSkew": 1, "topologyKey": "zone", "whenUnsatisfiable": "DoNotSchedule"}],
        volumes=[{"name": "d", "persistentVolumeClaim": {"claimName": "c"}}],
        resourceClaims=[{"name": "gpu"}],
        schedulerName="custom",
        runtimeClassName="kata",
    )
    r = evaluate_node_eligibility(pod, make_node(), TARGET)
    assert r.state is Eligibility.UNKNOWN
    assert codes(r.unknowns) >= {
        Reason.EXTENDED_RESOURCE_NOT_MODELED, Reason.HOST_PORT_NOT_MODELED, Reason.REQUIRED_POD_ANTI_AFFINITY_NOT_MODELED,
        Reason.TOPOLOGY_SPREAD_NOT_MODELED, Reason.PERSISTENT_VOLUME_TOPOLOGY_NOT_MODELED,
        Reason.DYNAMIC_RESOURCE_CLAIM_NOT_MODELED, Reason.CUSTOM_SCHEDULER, Reason.RUNTIME_CLASS_NOT_MODELED,
    }


def test_known_blocker_wins_over_unknowns():
    pod = make_pod(nodeSelector={"disktype": "ssd"}, schedulerName="custom")
    assert evaluate_node_eligibility(pod, make_node(), TARGET).state is Eligibility.INELIGIBLE


def test_capacity_fit_is_independent_of_eligibility():
    nodes = [make_node(name="n1", cpu="2", memory="2Gi")]
    big = make_pod(name="big", node=TARGET, containers=[container(requests={"cpu": "3", "memory": "1Gi"})])
    small = make_pod(name="small", node=TARGET, containers=[container(requests={"cpu": "1", "memory": "1Gi"})])
    inv = build_resource_inventory(nodes, [big, small], COMPLETE, TARGET)
    big_fit = evaluate_capacity_fit(inv.pod_requests["ns/big"], inv.nodes["n1"])
    assert big_fit.fit is CapacityFit.DOES_NOT_FIT and Reason.INSUFFICIENT_CPU in codes(big_fit.reasons)
    assert evaluate_capacity_fit(inv.pod_requests["ns/small"], inv.nodes["n1"]).fit is CapacityFit.FITS


def test_zero_request_pod_fits_overcommitted_node():
    # NodeResourcesFit skips cpu/memory checks for resources the pod does not request;
    # only the pod-count check applies.
    nodes = [make_node(name="n1", cpu="1", memory="1Gi")]
    hog = make_pod(name="hog", node="n1", containers=[container(requests={"cpu": "2", "memory": "2Gi"})])
    empty = make_pod(name="empty", node=TARGET, containers=[container(requests={})])
    inv = build_resource_inventory(nodes, [hog, empty], COMPLETE, TARGET)
    assert inv.nodes["n1"].remaining_cpu_millicores == -1000
    assert evaluate_capacity_fit(inv.pod_requests["ns/empty"], inv.nodes["n1"]).fit is CapacityFit.FITS
