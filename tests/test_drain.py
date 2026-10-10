"""Drain classification follows kubectl drain filters
(https://kubernetes.io/docs/reference/kubectl/generated/kubectl_drain/) using owner metadata only."""

from qplace.diagnostics import Reason
from qplace.drain import DrainClass, Replacement, classify_drain_pods, selector_matches
from tests.conftest import COMPLETE, make_pod

T = "target"


def classify(*pods, pdbs=()):
    inv = classify_drain_pods(pods, T, pdbs, COMPLETE)
    return inv, {r.pod: r for r in inv.pods}


def test_replicaset_pod_is_candidate_with_assumed_replacement():
    _, r = classify(make_pod(name="web", node=T, owner=("apps/v1", "ReplicaSet")))
    rec = r["ns/web"]
    assert rec.drain_class is DrainClass.CONTROLLER_MANAGED
    assert rec.replacement is Replacement.ASSUMED and rec.placement_candidate and rec.controller_known
    assert Reason.REPLACEMENT_TEMPLATE_UNKNOWN in {f.code for f in rec.findings}


def test_statefulset_pod_is_candidate_with_identity_flag():
    _, r = classify(make_pod(name="db-0", node=T, owner=("apps/v1", "StatefulSet")))
    assert r["ns/db-0"].placement_candidate
    assert Reason.STATEFUL_IDENTITY in {f.code for f in r["ns/db-0"].findings}


def test_daemonset_pod_is_excluded_and_needs_ignore_daemonsets():
    inv, r = classify(make_pod(name="ds", node=T, owner=("apps/v1", "DaemonSet")))
    assert r["ns/ds"].drain_class is DrainClass.DAEMONSET and not r["ns/ds"].placement_candidate
    assert inv.kubectl_drain_flags == ("--ignore-daemonsets",)


def test_mirror_pod_detected_by_annotation_not_name():
    pod = make_pod(name="looks-normal", node=T, owner=("v1", "Node"),
                   metadata={"annotations": {"kubernetes.io/config.mirror": "abc"}})
    _, r = classify(pod)
    assert r["ns/looks-normal"].drain_class is DrainClass.MIRROR
    assert not r["ns/looks-normal"].placement_candidate


def test_standalone_pod_requires_force_and_is_not_a_candidate():
    inv, r = classify(make_pod(name="debug", node=T, owner=None))
    assert r["ns/debug"].drain_class is DrainClass.UNMANAGED
    assert r["ns/debug"].replacement is Replacement.NOT_EXPECTED
    assert "--force" in inv.kubectl_drain_flags


def test_owner_reference_without_controller_flag_is_unmanaged():
    pod = make_pod(name="p", node=T, owner=None,
                   metadata={"ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "x"}]})
    _, r = classify(pod)
    assert r["ns/p"].drain_class is DrainClass.UNMANAGED


def test_terminating_and_terminal_pods_are_not_candidates():
    terminating = make_pod(name="t", node=T, metadata={"deletionTimestamp": "2026-10-01T00:00:00Z"})
    done = make_pod(name="d", node=T, phase="Succeeded")
    _, r = classify(terminating, done)
    assert r["ns/t"].drain_class is DrainClass.TERMINATING and not r["ns/t"].placement_candidate
    assert r["ns/d"].drain_class is DrainClass.TERMINAL and not r["ns/d"].placement_candidate


def test_unknown_controller_kind_or_group():
    crd = make_pod(name="c", node=T, owner=("example.com/v1", "Widget"))
    fake_rs = make_pod(name="f", node=T, owner=("example.com/v1", "ReplicaSet"))  # same kind, wrong API group
    _, r = classify(crd, fake_rs)
    for key in ("ns/c", "ns/f"):
        assert r[key].drain_class is DrainClass.UNKNOWN_CONTROLLER
        assert r[key].replacement is Replacement.UNKNOWN and not r[key].controller_known
        assert r[key].placement_candidate  # conservative: still counted as demand


def test_job_replacement_is_conditional():
    _, r = classify(make_pod(name="j", node=T, owner=("batch/v1", "Job")))
    assert r["ns/j"].replacement is Replacement.CONDITIONAL


def test_emptydir_requires_delete_emptydir_data():
    inv, r = classify(make_pod(name="e", node=T, volumes=[{"name": "s", "emptyDir": {}}]))
    assert "--delete-emptydir-data" in inv.kubectl_drain_flags
    assert Reason.LOCAL_EMPTYDIR_DATA in {f.code for f in r["ns/e"].findings}


def test_pods_on_other_nodes_are_ignored():
    inv, _ = classify(make_pod(name="elsewhere", node="other"))
    assert inv.pods == ()


def pdb(name, selector, allowed):
    return {"metadata": {"namespace": "ns", "name": name}, "spec": {"selector": selector},
            "status": {"disruptionsAllowed": allowed}}


def test_pdb_assessments_are_advisory():
    pods = [make_pod(name=f"w{i}", node=T, metadata={"labels": {"app": "w"}}) for i in range(3)]
    inv, r = classify(*pods, pdbs=[pdb("zero", {"matchLabels": {"app": "w"}}, 0)])
    assert inv.pdbs[0].assessment is Reason.PDB_EVICTION_BLOCKED_AT_SNAPSHOT

    inv, _ = classify(*pods, pdbs=[pdb("two", {"matchLabels": {"app": "w"}}, 2)])
    # disruptionsAllowed > 0 must not be read as "the whole drain is permitted".
    assert inv.pdbs[0].assessment is Reason.PDB_MAY_BLOCK_SEQUENTIAL_EVICTION

    inv, _ = classify(*pods, pdbs=[pdb("many", {"matchLabels": {"app": "w"}}, 5)])
    assert inv.pdbs[0].assessment is Reason.PDB_ADVISORY_ONLY


def test_pdb_selector_semantics_and_multiple_matches():
    assert selector_matches({}, {"a": "b"}) is True  # policy/v1: empty selector matches every pod
    assert selector_matches(None, {"a": "b"}) is False
    assert selector_matches({"matchExpressions": [{"key": "a", "operator": "NotIn", "values": ["b"]}]}, {}) is True
    assert selector_matches({"matchExpressions": [{"key": "a", "operator": "Weird"}]}, {"a": "b"}) is None
    pod = make_pod(name="p", node=T, metadata={"labels": {"app": "w"}})
    inv, r = classify(pod, pdbs=[pdb("one", {}, 1), pdb("two", {"matchLabels": {"app": "w"}}, 1)])
    assert Reason.MULTIPLE_PDBS_MATCH in {f.code for f in r["ns/p"].findings}


def test_terminating_daemonset_pod_still_needs_ignore_daemonsets():
    # kubectl's daemonSetFilter does not look at deletionTimestamp.
    pod = make_pod(name="ds", node=T, owner=("apps/v1", "DaemonSet"),
                   metadata={"deletionTimestamp": "2026-10-01T00:00:00Z"})
    inv, r = classify(pod)
    assert r["ns/ds"].drain_class is DrainClass.DAEMONSET
    assert Reason.ALREADY_TERMINATING in {f.code for f in r["ns/ds"].findings}
    assert inv.kubectl_drain_flags == ("--ignore-daemonsets",)


def test_pending_pods_do_not_count_against_pdbs():
    # The eviction API ignores PDBs for Pending pods (canIgnorePDB).
    pending = make_pod(name="p", node=T, phase="Pending", metadata={"labels": {"app": "w"}})
    inv, r = classify(pending, pdbs=[pdb("zero", {"matchLabels": {"app": "w"}}, 0)])
    assert inv.pdbs == () and r["ns/p"].matching_pdbs == ()
