"""End-to-end processing of the reference fixtures."""

import json
import random

import jsonschema
import pytest

from qplace import build_cluster_model, load_snapshot, load_snapshot_data
from qplace.eligibility import CapacityFit, Eligibility
from qplace.resources import AccountingStatus
from qplace.serialize import cluster_model_dict, dumps, resource_inventory_dict, write_artifacts
from qplace.snapshot import load_schema
from tests.conftest import FIXTURES, GI, MI, ROOT


def artifacts_for(path):
    model = build_cluster_model(load_snapshot(path))
    return model, cluster_model_dict(model), resource_inventory_dict(model)


def test_fixture_produces_schema_valid_artifacts(fixture_path, tmp_path):
    model, cm, ri = artifacts_for(fixture_path)
    jsonschema.Draft202012Validator(load_schema("cluster-model-1.0.schema.json")).validate(cm)
    jsonschema.Draft202012Validator(load_schema("resource-inventory-1.0.schema.json")).validate(ri)
    paths = write_artifacts(model, tmp_path)
    assert json.loads(paths["cluster_model"].read_text()) == cm
    assert json.loads(paths["resource_inventory"].read_text()) == ri


def test_output_is_byte_for_byte_deterministic(fixture_path):
    _, cm1, ri1 = artifacts_for(fixture_path)
    _, cm2, ri2 = artifacts_for(fixture_path)
    assert dumps(cm1) == dumps(cm2) and dumps(ri1) == dumps(ri2)


def test_output_independent_of_input_object_order(fixture_path):
    data = json.loads(fixture_path.read_text())
    rng = random.Random(1234)
    for key in ("nodes", "pods", "pod_disruption_budgets"):
        rng.shuffle(data[key])
    sha = load_snapshot(fixture_path).provenance.source_sha256
    shuffled = build_cluster_model(load_snapshot_data(data, source_file=fixture_path.name, source_sha256=sha))
    _, cm, ri = artifacts_for(fixture_path)
    assert dumps(cluster_model_dict(shuffled)) == dumps(cm)
    assert dumps(resource_inventory_dict(shuffled)) == dumps(ri)


def test_committed_reference_artifacts_are_current(fixture_path):
    _, cm, ri = artifacts_for(fixture_path)
    out = ROOT / "artifacts" / "m1" / fixture_path.stem
    assert (out / "cluster_model.json").read_text() == dumps(cm)
    assert (out / "resource_inventory.json").read_text() == dumps(ri)


def test_no_unsupported_condition_is_lost_in_serialization(fixture_path):
    model, cm, _ = artifacts_for(fixture_path)
    serialized = {(u["pod"], u["code"], u["detail"]) for u in cm["unsupported_constraints"]}
    for pod in model.pods:
        for c in pod.unsupported_constraints:
            assert (pod.key, str(c.code), c.detail) in serialized
    rows = {(r["pod"], r["node"]): r for r in cm["eligibility"]}
    pods = {p.key: p for p in model.pods}
    for (pod_key, node), row in rows.items():
        unknown_codes = {u["code"] for u in row["unknowns"]}
        for c in pods[pod_key].unsupported_constraints:
            assert str(c.code) in unknown_codes, (pod_key, node, c)
        if row["state"] == "ELIGIBLE":
            assert not pods[pod_key].unsupported_constraints
    assert len(cm["diagnostics"]) == len(model.diagnostics)


def _rows(cm, pod):
    return {r["node"]: r for r in cm["eligibility"] if r["pod"] == pod}


def test_fixture_a_healthy():
    _, cm, ri = artifacts_for(FIXTURES / "fixture_a_healthy.json")
    assert cm["data_completeness"]["capacity_assessment_complete"] is True
    assert cm["drain_inventory"]["placement_candidates"] == [
        "shop/api-5c6b7d-aaaaa", "shop/cart-0", "shop/web-7d9f8c-aaaaa", "shop/web-7d9f8c-bbbbb"]
    assert cm["summary"]["eligibility_states"] == {"ELIGIBLE": 8, "INELIGIBLE": 4, "UNKNOWN": 0}
    assert cm["summary"]["placement_decision"] is None
    pods = {p["pod"]: p for p in ri["pods"]}
    assert (pods["shop/api-5c6b7d-aaaaa"]["cpu_millicores"], pods["shop/api-5c6b7d-aaaaa"]["memory_bytes"]) == (
        1000, 256 * MI)  # init container peak
    assert pods["shop/web-7d9f8c-bbbbb"]["memory_bytes"] == 640 * MI  # app + native sidecar
    assert pods["shop/nightly-report-29012345-abcde"]["reservation"] == "NOT_RESERVED_TERMINAL"
    nodes = {n["node"]: n for n in ri["nodes"]}
    assert nodes["worker-01"]["reserved"] == {"cpu_millicores": 600, "memory_bytes": 576 * MI, "pods": 2}
    assert all(n["status"] == "EXACT" for n in ri["nodes"])
    assert cm["drain_inventory"]["pdb_assessments"][0]["assessment"] == "PDB_ADVISORY_ONLY"


def test_fixture_b_insufficient_capacity():
    model, cm, ri = artifacts_for(FIXTURES / "fixture_b_insufficient.json")
    nodes = {n["node"]: n for n in ri["nodes"]}
    assert nodes["worker-01"]["remaining"] == {"cpu_millicores": 300, "memory_bytes": GI, "pods": 107}
    assert nodes["worker-03"]["remaining"]["memory_bytes"] == -512 * MI  # negative, not clamped
    assert nodes["worker-03"]["overcommitted"] == ["memory"]
    big = _rows(cm, "analytics/batch-worker-8c9d0e-aaaaa")
    assert {n: r["capacity_fit"]["fit"] for n, r in big.items() if n != "worker-02"} == {
        "worker-01": "DOES_NOT_FIT", "worker-03": "DOES_NOT_FIT"}
    assert _rows(cm, "analytics/ingest-1a2b3c-aaaaa")["worker-01"]["capacity_fit"]["fit"] == "FITS"
    reporter = model.resources.pod_requests["analytics/reporter-9e8d7c-aaaaa"]
    assert (reporter.cpu_millicores, reporter.status) == (1000, AccountingStatus.REVIEW_REQUIRED)
    assert cm["summary"]["placement_decision"] is None


def test_fixture_c_constraints():
    model, cm, _ = artifacts_for(FIXTURES / "fixture_c_constraints.json")
    classes = {r["pod"]: r["drain_class"] for r in cm["drain_inventory"]["pods"]}
    assert classes["kube-system/fluent-bit-q8w7e"] == "DAEMONSET"
    assert classes["kube-system/kube-proxy-worker-03"] == "MIRROR"
    assert classes["default/debug-shell"] == "UNMANAGED"
    assert classes["payments/api-old-3c2b1a-aaaaa"] == "TERMINATING"
    assert classes["ops/agent-x7k2m"] == "UNKNOWN_CONTROLLER"
    assert cm["drain_inventory"]["kubectl_drain_flags_required"] == [
        "--delete-emptydir-data", "--force", "--ignore-daemonsets"]

    api = _rows(cm, "payments/api-6d5c4b-aaaaa")
    assert {n for n, r in api.items() if r["state"] == "ELIGIBLE"} == {"worker-01"}
    assert {b["code"] for b in api["worker-02"]["blockers"]} == {"NODE_UNSCHEDULABLE", "UNTOLERATED_TAINT"}
    assert {b["code"] for b in api["worker-05"]["blockers"]} >= {"NODE_NOT_READY", "UNTOLERATED_TAINT"}
    assert {b["code"] for b in api["worker-04"]["blockers"]} == {"NODE_SELECTOR_MISMATCH"}

    report = _rows(cm, "batch/report-28999-xk2lp")
    assert report["worker-04"]["state"] == "ELIGIBLE" and report["worker-04"]["soft"] == []  # soft taint tolerated

    trainer = _rows(cm, "ml/trainer-7e6f5a-aaaaa")
    assert trainer["gpu-01"]["state"] == "UNKNOWN"  # taint tolerated, GPU resource not modeled
    assert "EXTENDED_RESOURCE_NOT_MODELED" in {u["code"] for u in trainer["gpu-01"]["unknowns"]}

    # No candidate carrying an unsupported hard constraint is ever reported ELIGIBLE.
    for key in ("payments/ledger-0", "analytics/etl-2b3c4d-aaaaa", "ops/agent-x7k2m",
                "secure/sandbox-5f4e3d-aaaaa", "edge/proxy-8a7b6c-aaaaa", "ml/trainer-7e6f5a-aaaaa"):
        assert model.eligible_destinations(key) == ()

    pdbs = {a["pdb"]: a["assessment"] for a in cm["drain_inventory"]["pdb_assessments"]}
    assert pdbs == {"payments/ledger-pdb": "PDB_EVICTION_BLOCKED_AT_SNAPSHOT",
                    "payments/payments-all": "PDB_MAY_BLOCK_SEQUENTIAL_EVICTION"}
    ledger = next(r for r in cm["drain_inventory"]["pods"] if r["pod"] == "payments/ledger-0")
    assert "MULTIPLE_PDBS_MATCH" in {f["code"] for f in ledger["findings"]}


def test_eligible_never_hides_capacity_or_unknowns(fixture_path):
    model, _, _ = artifacts_for(fixture_path)
    for r in model.eligibility:
        if r.eligibility.state is Eligibility.ELIGIBLE:
            assert not r.eligibility.unknowns and not r.eligibility.blockers
        if r.capacity.fit is CapacityFit.UNKNOWN:
            assert r.capacity.reasons
