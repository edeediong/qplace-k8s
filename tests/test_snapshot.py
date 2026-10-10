"""Snapshot loading and integrity validation."""

import copy
import json

import pytest

from qplace.diagnostics import Reason, SnapshotValidationError
from qplace.snapshot import load_snapshot, load_snapshot_data
from tests.conftest import FIXTURES


@pytest.fixture
def base():
    return json.loads((FIXTURES / "fixture_a_healthy.json").read_text())


def test_fixture_loads_with_provenance(base):
    snap = load_snapshot(FIXTURES / "fixture_a_healthy.json")
    assert snap.provenance.snapshot_id == "fixture-a-healthy"
    assert snap.provenance.source_type == "synthetic_fixture"
    assert snap.provenance.kubernetes_minor == (1, 34)
    assert len(snap.provenance.source_sha256) == 64
    assert snap.inventory.pods_complete and snap.target_node == "worker-03"


@pytest.mark.parametrize("version", ["2.0", "0.9", None, 1.0])
def test_unsupported_schema_version_rejected(base, version):
    base["schema_version"] = version
    with pytest.raises(SnapshotValidationError, match="schema_version"):
        load_snapshot_data(base)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.pop("target_node"), "schema validation"),
        (lambda d: d["snapshot"]["inventory"].pop("pods"), "schema validation"),
        (lambda d: d["snapshot"].update(captured_at="2026-10-01 09:00"), "schema validation"),
        (lambda d: d["pods"][0]["metadata"].pop("uid"), "schema validation"),
        (lambda d: d.update(target_node="nope"), "not present in nodes"),
        (lambda d: d["pods"].append(copy.deepcopy(d["pods"][0])), "duplicate pod"),
        (lambda d: d["pods"][0]["spec"].update(nodeName="ghost"), "absent from a complete node inventory"),
        (lambda d: d["snapshot"]["inventory"].update(pod_disruption_budgets="not_collected"), "not_collected"),
    ],
)
def test_malformed_or_inconsistent_snapshot_rejected(base, mutate, match):
    mutate(base)
    with pytest.raises(SnapshotValidationError, match=match):
        load_snapshot_data(base)


def test_partial_node_inventory_downgrades_unknown_binding_to_warning(base):
    base["snapshot"]["inventory"]["nodes"] = "partial"
    base["pods"][0]["spec"]["nodeName"] = "ghost"
    snap = load_snapshot_data(base)
    assert [d.code for d in snap.diagnostics] == [Reason.POD_BOUND_TO_UNKNOWN_NODE]


def test_loader_does_not_alias_input(base):
    snap = load_snapshot_data(base)
    base["nodes"][0]["metadata"]["name"] = "mutated"
    assert snap.nodes[0]["metadata"]["name"] != "mutated"
