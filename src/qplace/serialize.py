"""Deterministic JSON artifacts: cluster_model.json and resource_inventory.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qplace import __version__
from qplace.drain import DrainClass
from qplace.eligibility import CapacityFit, Eligibility
from qplace.model import ClusterModel, eligibility_summary
from qplace.resources import AccountingStatus

ARTIFACT_SCHEMA_VERSION = "1.0"

ACCOUNTING_POLICY = {
    "units": {"cpu": "millicores (integer)", "memory": "bytes (integer)", "pods": "count"},
    "rounding": "quantities are parsed exactly and rounded up to the next whole unit (Quantity.MilliValue/Value)",
    "pod_request_formula": "max(sum(containers) + sum(sidecars), max(init_i + sidecars_started_before_i)); "
    "pod-level cpu/memory requests replace that aggregate; spec.overhead is added once",
    "resize": "per container max(spec, status.resources.requests, status.allocatedResources); "
    "spec ignored when PodResizePending reason=Infeasible; unknown if resize is indicated without status",
    "reserving_pods": "bound pods in phase Pending/Running/Unknown, including terminating pods; "
    "Succeeded/Failed pods and unbound pods do not reserve capacity",
    "unknown_values": "null with a reason code; never replaced by zero",
    "incomplete_inventory": "reserved/remaining are null; *_lower_bound / *_upper_bound fields carry observed bounds",
    "overcommitment": "negative remaining headroom is preserved, not clamped",
    "summary_remaining_total": "sum of max(remaining, 0) over Ready, schedulable destination nodes; "
    "a necessary-condition indicator only, never a feasibility verdict",
}


def dumps(data: Any) -> str:
    """Canonical serialization: sorted keys, 2-space indent, UTF-8, trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _header(model: ClusterModel, artifact: str) -> dict[str, Any]:
    return {
        "artifact": artifact,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generator": {"name": "qplace", "version": __version__, "milestone": "M1"},
        "snapshot": {
            **model.snapshot.provenance.to_dict(),
            "inventory": model.snapshot.inventory.to_dict(),
        },
        "target_node": model.target_node,
        "data_completeness": model.completeness.to_dict(),
    }


def cluster_model_dict(model: ClusterModel) -> dict[str, Any]:
    drain = model.drain
    class_counts = {c.value: 0 for c in DrainClass}
    for r in drain.pods:
        class_counts[r.drain_class.value] += 1

    candidates = []
    for pod_key in drain.placement_candidates:
        rows = [r for r in model.eligibility if r.pod == pod_key]
        candidates.append({
            "pod": pod_key,
            "eligible": [r.node for r in rows if r.eligibility.state is Eligibility.ELIGIBLE],
            "eligible_and_individually_fits": [
                r.node for r in rows
                if r.eligibility.state is Eligibility.ELIGIBLE and r.capacity.fit is CapacityFit.FITS
            ],
            "unknown": [r.node for r in rows if r.eligibility.state is Eligibility.UNKNOWN],
            "ineligible": [r.node for r in rows if r.eligibility.state is Eligibility.INELIGIBLE],
        })

    unsupported = sorted(
        {(p.key, str(c.code), c.detail) for p in model.pods for c in p.constraints if c.kind == "unsupported"}
    )
    out = _header(model, "cluster_model")
    out.update({
        "nodes": [n.to_dict() for n in model.nodes],
        "pods": [p.to_dict() for p in model.pods],
        "drain_inventory": {
            "target_node": drain.target_node,
            "pods": [r.to_dict() for r in drain.pods],
            "placement_candidates": list(drain.placement_candidates),
            "excluded": [
                {"pod": r.pod, "drain_class": str(r.drain_class), "replacement": str(r.replacement)}
                for r in drain.pods if not r.placement_candidate
            ],
            "kubectl_drain_flags_required": list(drain.kubectl_drain_flags),
            "pdb_assessments": [a.to_dict() for a in drain.pdbs],
        },
        "eligibility": [r.to_dict() for r in model.eligibility],
        "candidate_destinations": candidates,
        "unsupported_constraints": [{"pod": p, "code": c, "detail": d} for p, c, d in unsupported],
        "diagnostics": [d.to_dict() for d in model.diagnostics],
        "summary": {
            "node_count": len(model.nodes),
            "pod_count": len(model.pods),
            "target_pod_count": len(drain.pods),
            "drain_class_counts": class_counts,
            "placement_candidate_count": len(drain.placement_candidates),
            **eligibility_summary(model),
            "placement_decision": None,
            "placement_decision_note": "M1 models eligibility only; no assignment has been computed",
        },
    })
    return out


def resource_inventory_dict(model: ClusterModel) -> dict[str, Any]:
    res = model.resources
    candidates = set(model.drain.placement_candidates)
    pods = []
    for key, req in sorted(res.pod_requests.items()):
        pod = next(p for p in model.pods if p.key == key)
        entry = req.to_dict()
        entry.update({
            "node": pod.node_name,
            "phase": pod.phase,
            "reservation": str(res.reservation[key]),
            "placement_candidate": key in candidates,
        })
        pods.append(entry)

    nodes_by_name = {n.name: n for n in model.nodes}
    usable = [
        a for a in res.nodes.values()
        if not a.is_drain_target and nodes_by_name[a.node].ready == "True" and not nodes_by_name[a.node].unschedulable
    ]

    def total(values: list[int | None], clamp: bool = False) -> int | None:
        if any(v is None for v in values):
            return None
        return sum(max(v, 0) if clamp else v for v in values)  # type: ignore[type-var]

    cand_reqs = [res.pod_requests[k] for k in sorted(candidates)]
    status_counts = {s.value: 0 for s in AccountingStatus}
    for a in res.nodes.values():
        status_counts[a.status.value] += 1

    out = _header(model, "resource_inventory")
    out.update({
        "accounting_policy": ACCOUNTING_POLICY,
        "nodes": [a.to_dict() for a in sorted(res.nodes.values(), key=lambda a: a.node)],
        "pods": pods,
        "diagnostics": [d.to_dict() for d in res.diagnostics],
        "summary": {
            "node_status_counts": status_counts,
            "overcommitted_nodes": sorted(a.node for a in res.nodes.values() if a.overcommitted),
            "ready_schedulable_destinations": sorted(a.node for a in usable),
            "remaining_total_ready_schedulable": {
                "cpu_millicores": total([a.remaining_cpu_millicores for a in usable], clamp=True),
                "memory_bytes": total([a.remaining_memory_bytes for a in usable], clamp=True),
            },
            "placement_candidate_demand": {
                "cpu_millicores": total([r.cpu_millicores for r in cand_reqs]),
                "memory_bytes": total([r.memory_bytes for r in cand_reqs]),
                "pods": len(cand_reqs),
            },
            "pod_request_status_counts": {
                s.value: sum(1 for r in res.pod_requests.values() if r.status is s) for s in AccountingStatus
            },
            "note": "Totals are necessary-condition indicators only; M1 makes no joint-placement claim.",
        },
    })
    return out


def write_artifacts(model: ClusterModel, out_dir: str | Path) -> dict[str, Path]:
    """Write both artifacts into ``out_dir`` and return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "cluster_model": out_dir / "cluster_model.json",
        "resource_inventory": out_dir / "resource_inventory.json",
    }
    paths["cluster_model"].write_text(dumps(cluster_model_dict(model)), encoding="utf-8")
    paths["resource_inventory"].write_text(dumps(resource_inventory_dict(model)), encoding="utf-8")
    return paths
