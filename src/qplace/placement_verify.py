"""Independent placement verifier.

Checks a proposed assignment against the authoritative M1 ``ClusterModel``. It
deliberately does not use ``PlacementProblem`` or any solver code, so a bug in
problem construction or in a solver cannot also hide itself here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qplace.model import ClusterModel
from qplace.resources import AccountingStatus


class Violation(StrEnum):
    NOT_A_PLACEMENT_CANDIDATE = "NOT_A_PLACEMENT_CANDIDATE"
    UNKNOWN_DESTINATION = "UNKNOWN_DESTINATION"
    DESTINATION_IS_DRAIN_TARGET = "DESTINATION_IS_DRAIN_TARGET"
    DESTINATION_NOT_ELIGIBLE = "DESTINATION_NOT_ELIGIBLE"
    DUPLICATE_ASSIGNMENT = "DUPLICATE_ASSIGNMENT"
    CPU_REQUEST_UNKNOWN = "CPU_REQUEST_UNKNOWN"
    MEMORY_REQUEST_UNKNOWN = "MEMORY_REQUEST_UNKNOWN"
    NEGATIVE_REQUEST = "NEGATIVE_REQUEST"
    HEADROOM_UNKNOWN = "HEADROOM_UNKNOWN"
    CPU_CAPACITY_EXCEEDED = "CPU_CAPACITY_EXCEEDED"
    MEMORY_CAPACITY_EXCEEDED = "MEMORY_CAPACITY_EXCEEDED"
    POD_SLOTS_EXCEEDED = "POD_SLOTS_EXCEEDED"


@dataclass(frozen=True)
class VerificationError:
    code: Violation
    pod: str | None
    node: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "pod": self.pod, "node": self.node, "detail": self.detail}


@dataclass(frozen=True)
class NodeUsage:
    node: str
    assigned_pods: tuple[str, ...]
    cpu_millicores: int
    memory_bytes: int
    remaining_before: dict[str, int | None]
    remaining_after: dict[str, int | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "assigned_pods": list(self.assigned_pods),
            "assigned": {"cpu_millicores": self.cpu_millicores, "memory_bytes": self.memory_bytes,
                         "pods": len(self.assigned_pods)},
            "remaining_before": self.remaining_before,
            "remaining_after": self.remaining_after,
        }


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    errors: tuple[VerificationError, ...]
    review_required: tuple[str, ...]  # assigned pods / nodes whose values are REVIEW_REQUIRED
    usage: tuple[NodeUsage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "review_required": list(self.review_required),
            "node_usage": [u.to_dict() for u in self.usage],
        }


def verify_placement(model: ClusterModel, assignments: Iterable[tuple[str, str]]) -> VerificationResult:
    """Verify ``(pod, node)`` pairs. Aggregate limits are checked after collecting all pairs."""
    pairs = list(assignments)
    errors: list[VerificationError] = []
    review: set[str] = set()
    candidates = set(model.drain.placement_candidates)
    node_names = {n.name for n in model.nodes}

    seen: dict[str, str] = {}
    per_node: dict[str, list[str]] = {}
    for pod, node in pairs:
        if pod in seen:
            errors.append(VerificationError(Violation.DUPLICATE_ASSIGNMENT, pod, node,
                                            f"already assigned to {seen[pod]}"))
            continue
        seen[pod] = node
        ok = True
        if pod not in candidates:
            errors.append(VerificationError(Violation.NOT_A_PLACEMENT_CANDIDATE, pod, node, "not in M1 candidates"))
            ok = False
        if node not in node_names:
            errors.append(VerificationError(Violation.UNKNOWN_DESTINATION, pod, node, "node not in snapshot"))
            ok = False
        elif node == model.target_node:
            errors.append(VerificationError(Violation.DESTINATION_IS_DRAIN_TARGET, pod, node, "drain target"))
            ok = False
        if ok and node not in model.eligible_destinations(pod):
            errors.append(VerificationError(Violation.DESTINATION_NOT_ELIGIBLE, pod, node,
                                            "M1 eligibility is not ELIGIBLE"))
            ok = False
        if pod in candidates:
            req = model.resources.pod_requests[pod]
            if req.cpu_millicores is None:
                errors.append(VerificationError(Violation.CPU_REQUEST_UNKNOWN, pod, node, "cpu request unknown"))
                ok = False
            if req.memory_bytes is None:
                errors.append(VerificationError(Violation.MEMORY_REQUEST_UNKNOWN, pod, node,
                                                "memory request unknown"))
                ok = False
            if any(v is not None and v < 0 for v in (req.cpu_millicores, req.memory_bytes)):
                errors.append(VerificationError(Violation.NEGATIVE_REQUEST, pod, node, "negative request"))
                ok = False
            if req.status is AccountingStatus.REVIEW_REQUIRED:
                review.add(f"pod/{pod}")
        if ok:
            per_node.setdefault(node, []).append(pod)

    usage: list[NodeUsage] = []
    for node in sorted(per_node):
        account = model.resources.nodes[node]
        pods = sorted(per_node[node])
        before = {"cpu_millicores": account.remaining_cpu_millicores,
                  "memory_bytes": account.remaining_memory_bytes,
                  "pods": account.remaining_pod_slots}
        cpu = sum(model.resources.pod_requests[p].cpu_millicores or 0 for p in pods)
        mem = sum(model.resources.pod_requests[p].memory_bytes or 0 for p in pods)
        used = {"cpu_millicores": cpu, "memory_bytes": mem, "pods": len(pods)}
        if None in before.values():
            errors.append(VerificationError(Violation.HEADROOM_UNKNOWN, None, node,
                                            f"remaining headroom unknown: {before}"))
        for key, code in (("cpu_millicores", Violation.CPU_CAPACITY_EXCEEDED),
                          ("memory_bytes", Violation.MEMORY_CAPACITY_EXCEEDED),
                          ("pods", Violation.POD_SLOTS_EXCEEDED)):
            if before[key] is not None and used[key] > before[key]:
                errors.append(VerificationError(code, None, node,
                                                f"assigned {used[key]} > remaining {before[key]}"))
        if account.status is AccountingStatus.REVIEW_REQUIRED:
            review.add(f"node/{node}")
        usage.append(NodeUsage(
            node=node,
            assigned_pods=tuple(pods),
            cpu_millicores=cpu,
            memory_bytes=mem,
            remaining_before=before,
            remaining_after={k: None if before[k] is None else before[k] - used[k] for k in before},
        ))

    errors.sort(key=lambda e: (str(e.code), e.pod or "", e.node or "", e.detail))
    return VerificationResult(
        valid=not errors,
        errors=tuple(errors),
        review_required=tuple(sorted(review)),
        usage=tuple(usage),
    )
