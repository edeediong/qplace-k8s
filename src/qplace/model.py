"""Compose the M1 cluster model from a validated snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qplace.diagnostics import Diagnostic, Reason, dedupe_diagnostics
from qplace.drain import DrainInventory, classify_drain_pods
from qplace.eligibility import (
    CapacityFit,
    CapacityFitResult,
    Eligibility,
    EligibilityResult,
    evaluate_capacity_fit,
    evaluate_node_eligibility,
)
from qplace.nodes import NodeModel, normalize_node
from qplace.pods import PodModel, normalize_pod
from qplace.resources import AccountingStatus, ResourceInventory, build_resource_inventory
from qplace.snapshot import Snapshot


@dataclass(frozen=True)
class EligibilityRecord:
    pod: str
    node: str
    eligibility: EligibilityResult
    capacity: CapacityFitResult

    def to_dict(self) -> dict[str, Any]:
        out = self.eligibility.to_dict()
        out["capacity_fit"] = self.capacity.to_dict()
        return out


@dataclass(frozen=True)
class DataCompleteness:
    nodes_inventory: str
    pods_inventory: str
    pdb_inventory: str
    capacity_assessment_complete: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes_inventory": self.nodes_inventory,
            "pods_inventory": self.pods_inventory,
            "pod_disruption_budgets_inventory": self.pdb_inventory,
            "capacity_assessment_complete": self.capacity_assessment_complete,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ClusterModel:
    snapshot: Snapshot
    nodes: tuple[NodeModel, ...]
    pods: tuple[PodModel, ...]
    resources: ResourceInventory
    drain: DrainInventory
    eligibility: tuple[EligibilityRecord, ...]
    completeness: DataCompleteness
    diagnostics: tuple[Diagnostic, ...]

    @property
    def target_node(self) -> str:
        return self.snapshot.target_node

    @property
    def destination_nodes(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes if n.name != self.target_node)

    def eligible_destinations(self, pod_key: str) -> tuple[str, ...]:
        """Destinations whose modeled node-level constraints are all ELIGIBLE for this pod."""
        return tuple(
            r.node for r in self.eligibility if r.pod == pod_key and r.eligibility.state is Eligibility.ELIGIBLE
        )


def build_cluster_model(snapshot: Snapshot) -> ClusterModel:
    minor = snapshot.provenance.kubernetes_minor
    nodes = tuple(sorted((normalize_node(n) for n in snapshot.nodes), key=lambda n: n.name))
    pods = tuple(sorted((normalize_pod(p) for p in snapshot.pods), key=lambda p: p.key))
    resources = build_resource_inventory(nodes, pods, snapshot.inventory, snapshot.target_node, minor)
    drain = classify_drain_pods(pods, snapshot.target_node, snapshot.pod_disruption_budgets, snapshot.inventory)

    pods_by_key = {p.key: p for p in pods}
    records: list[EligibilityRecord] = []
    for pod_key in drain.placement_candidates:
        pod = pods_by_key[pod_key]
        for node in nodes:
            records.append(
                EligibilityRecord(
                    pod=pod_key,
                    node=node.name,
                    eligibility=evaluate_node_eligibility(pod, node, snapshot.target_node),
                    capacity=evaluate_capacity_fit(resources.pod_requests[pod_key], resources.nodes[node.name]),
                )
            )

    reasons: list[str] = []
    if not snapshot.inventory.nodes_complete:
        reasons.append(f"{Reason.INVENTORY_INCOMPLETE}: node inventory is partial")
    if not snapshot.inventory.pods_complete:
        reasons.append(f"{Reason.INVENTORY_INCOMPLETE}: pod inventory is partial")
    unknown_nodes = sorted(
        a.node for a in resources.nodes.values() if not a.is_drain_target and a.status is AccountingStatus.UNKNOWN
    )
    if unknown_nodes:
        reasons.append(f"{Reason.NODE_HEADROOM_UNKNOWN}: {', '.join(unknown_nodes)}")
    unknown_pods = sorted(
        k for k in drain.placement_candidates if resources.pod_requests[k].status is AccountingStatus.UNKNOWN
    )
    if unknown_pods:
        reasons.append(f"{Reason.POD_REQUEST_UNKNOWN}: {', '.join(unknown_pods)}")
    completeness = DataCompleteness(
        nodes_inventory=snapshot.inventory.nodes,
        pods_inventory=snapshot.inventory.pods,
        pdb_inventory=snapshot.inventory.pod_disruption_budgets,
        capacity_assessment_complete=not reasons,
        reasons=tuple(reasons),
    )

    diagnostics: list[Diagnostic] = list(snapshot.diagnostics)
    for n in nodes:
        diagnostics.extend(n.diagnostics)
    diagnostics.extend(resources.diagnostics)
    diagnostics.extend(drain.diagnostics)

    return ClusterModel(
        snapshot=snapshot,
        nodes=nodes,
        pods=pods,
        resources=resources,
        drain=drain,
        eligibility=tuple(sorted(records, key=lambda r: (r.pod, r.node))),
        completeness=completeness,
        diagnostics=dedupe_diagnostics(diagnostics),
    )


def eligibility_summary(model: ClusterModel) -> dict[str, Any]:
    states = {s.value: 0 for s in Eligibility}
    fits = {f.value: 0 for f in CapacityFit}
    for r in model.eligibility:
        states[r.eligibility.state.value] += 1
        fits[r.capacity.fit.value] += 1
    return {"eligibility_states": states, "capacity_fit": fits}
