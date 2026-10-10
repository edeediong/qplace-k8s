"""Per-node reserved/remaining requested-resource accounting."""

from qplace.diagnostics import Reason
from qplace.nodes import normalize_node
from qplace.resources import AccountingStatus, ReservationPolicy, build_resource_inventory
from tests.conftest import COMPLETE, GI, MI, PARTIAL_PODS, container, make_node, make_pod, raw_node


def pod_on(name, node="n1", cpu="1", mem="1Gi", **kw):
    return make_pod(name=name, node=node, containers=[container(requests={"cpu": cpu, "memory": mem})], **kw)


def account(pods, nodes=None, inventory=COMPLETE):
    nodes = nodes or [make_node(name="n1", cpu="4", memory="8Gi", pods="10")]
    inv = build_resource_inventory(nodes, pods, inventory, target_node="target")
    return inv, inv.nodes["n1"]


def test_empty_node_has_full_headroom():
    _, a = account([])
    assert (a.reserved_cpu_millicores, a.reserved_memory_bytes, a.reserved_pod_count) == (0, 0, 0)
    assert (a.remaining_cpu_millicores, a.remaining_memory_bytes, a.remaining_pod_slots) == (4000, 8 * GI, 10)
    assert a.status is AccountingStatus.EXACT


def test_multiple_pods_are_summed():
    _, a = account([pod_on("a", cpu="1", mem="1Gi"), pod_on("b", cpu="500m", mem="512Mi")])
    assert (a.reserved_cpu_millicores, a.reserved_memory_bytes) == (1500, GI + 512 * MI)
    assert (a.remaining_cpu_millicores, a.remaining_memory_bytes, a.remaining_pod_slots) == (2500, 7 * GI - 512 * MI, 8)


def test_pending_pod_bound_to_node_reserves_capacity():
    inv, a = account([pod_on("pending", phase="Pending")])
    assert inv.reservation["ns/pending"] is ReservationPolicy.RESERVED
    assert a.reserved_cpu_millicores == 1000


def test_terminating_pod_still_reserves_capacity():
    inv, a = account([pod_on("t", metadata={"deletionTimestamp": "2026-10-01T00:00:00Z"})])
    assert inv.reservation["ns/t"] is ReservationPolicy.RESERVED_TERMINATING
    assert a.reserved_cpu_millicores == 1000


def test_terminal_pods_do_not_reserve_capacity():
    _, a = account([pod_on("done", phase="Succeeded"), pod_on("failed", phase="Failed"), pod_on("live")])
    assert a.reserved_cpu_millicores == 1000
    assert Reason.TERMINAL_POD_NOT_RESERVED in a.reasons


def test_unbound_pod_reserves_nothing_but_is_reported():
    inv, a = account([pod_on("unbound", node=None, phase="Pending")])
    assert a.reserved_cpu_millicores == 0
    assert any(d.code is Reason.UNBOUND_PENDING_PODS_PRESENT for d in inv.diagnostics)


def test_overcommitted_node_keeps_negative_headroom():
    _, a = account([pod_on("a", cpu="3", mem="6Gi"), pod_on("b", cpu="2", mem="4Gi")])
    assert a.remaining_cpu_millicores == -1000
    assert a.remaining_memory_bytes == -2 * GI
    assert set(a.overcommitted) == {"cpu", "memory"}
    assert Reason.OVERCOMMITTED in a.reasons


def test_incomplete_pod_inventory_yields_bounds_not_values():
    _, a = account([pod_on("a")], inventory=PARTIAL_PODS)
    assert a.status is AccountingStatus.INCOMPLETE
    assert a.reserved_cpu_millicores is None and a.remaining_cpu_millicores is None
    assert a.reserved_cpu_millicores_lower_bound == 1000
    assert a.remaining_cpu_millicores_upper_bound == 3000
    assert Reason.INVENTORY_INCOMPLETE in a.reasons


def test_unknown_pod_request_makes_node_headroom_unknown():
    bad = make_pod(name="bad", containers=[container(requests={"cpu": "x", "memory": "1Gi"})])
    _, a = account([bad, pod_on("ok")])
    assert a.status is AccountingStatus.UNKNOWN
    assert a.remaining_cpu_millicores is None
    assert a.remaining_memory_bytes == 6 * GI  # memory is still fully known
    assert a.reserved_cpu_millicores_lower_bound == 1000


def test_missing_allocatable_is_unknown_not_zero():
    raw = raw_node(name="n1")
    del raw["status"]["allocatable"]["cpu"]
    node = normalize_node(raw)
    _, a = account([pod_on("a")], nodes=[node])
    assert a.allocatable_cpu_millicores is None and a.remaining_cpu_millicores is None
    assert a.status is AccountingStatus.UNKNOWN
    assert Reason.ALLOCATABLE_MISSING in a.reasons
