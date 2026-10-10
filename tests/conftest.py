from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from qplace.nodes import NodeModel, normalize_node
from qplace.pods import PodModel, normalize_pod
from qplace.snapshot import InventoryCompleteness

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "m1"
FIXTURE_FILES = sorted(FIXTURES.glob("fixture_*.json"))
MI = 2**20
GI = 2**30

_uids = itertools.count(1)

COMPLETE = InventoryCompleteness("complete", "complete", "complete", None)
PARTIAL_PODS = InventoryCompleteness("complete", "partial", "complete", None)


def container(name: str = "app", requests: dict | None = None, limits: dict | None = None, **extra: Any) -> dict:
    out: dict[str, Any] = {"name": name, "resources": {}}
    if requests is not None:
        out["resources"]["requests"] = requests
    if limits is not None:
        out["resources"]["limits"] = limits
    out.update(extra)
    return out


def raw_pod(
    name: str = "p",
    namespace: str = "ns",
    node: str | None = "n1",
    containers: list | None = None,
    phase: str | None = "Running",
    owner: tuple[str, str] | None = ("apps/v1", "ReplicaSet"),
    metadata: dict | None = None,
    status: dict | None = None,
    **spec: Any,
) -> dict:
    md: dict[str, Any] = {"name": name, "namespace": namespace, "uid": f"uid-{next(_uids)}"}
    if owner:
        md["ownerReferences"] = [{"apiVersion": owner[0], "kind": owner[1], "name": "owner", "controller": True}]
    md.update(metadata or {})
    body: dict[str, Any] = {"containers": containers or [container(requests={"cpu": "100m", "memory": "64Mi"})]}
    if node:
        body["nodeName"] = node
    body.update(spec)
    st: dict[str, Any] = {} if phase is None else {"phase": phase}
    st.update(status or {})
    return {"metadata": md, "spec": body, "status": st}


def make_pod(**kwargs: Any) -> PodModel:
    return normalize_pod(raw_pod(**kwargs))


def raw_node(
    name: str = "n1",
    cpu: str = "4",
    memory: str = "8Gi",
    pods: str = "110",
    labels: dict | None = None,
    taints: list | None = None,
    ready: str | None = "True",
    unschedulable: bool = False,
) -> dict:
    status: dict[str, Any] = {
        "capacity": {"cpu": cpu, "memory": memory, "pods": pods},
        "allocatable": {"cpu": cpu, "memory": memory, "pods": pods},
        "conditions": [] if ready is None else [{"type": "Ready", "status": ready}],
    }
    spec: dict[str, Any] = {"unschedulable": unschedulable}
    if taints:
        spec["taints"] = taints
    return {"metadata": {"name": name, "labels": labels or {}}, "spec": spec, "status": status}


def make_node(**kwargs: Any) -> NodeModel:
    return normalize_node(raw_node(**kwargs))


@pytest.fixture(params=FIXTURE_FILES, ids=lambda p: p.stem)
def fixture_path(request: pytest.FixtureRequest) -> Path:
    return request.param


def snapshot_data(nodes: list, pods: list, target: str, pods_inventory: str = "complete",
                  version: str | None = "v1.34.1") -> dict:
    return {
        "schema_version": "1.0",
        "snapshot": {
            "id": "test", "captured_at": "2026-10-10T00:00:00Z", "source_type": "synthetic_fixture",
            "kubernetes_version": version,
            "inventory": {"nodes": "complete", "pods": pods_inventory, "pod_disruption_budgets": "complete"},
        },
        "target_node": target, "nodes": nodes, "pods": pods, "pod_disruption_budgets": [],
    }


def build_model(nodes: list, pods: list, target: str = "src", **kwargs: Any):
    from qplace import build_cluster_model, load_snapshot_data

    return build_cluster_model(load_snapshot_data(snapshot_data(nodes, pods, target, **kwargs)))


def cand(name: str, cpu: str = "1", mem: str = "1Gi", node: str = "src", **kw: Any) -> dict:
    """A ReplicaSet-managed pod on the drain target (a placement candidate)."""
    return raw_pod(name=name, node=node, containers=[container(requests={"cpu": cpu, "memory": mem})], **kw)
