"""M2 placement problem: a deterministic, solver-independent view of an M1 ClusterModel.

The problem is a 0/1 assignment model. A binary variable ``x[pod, node]`` exists
only for *allowed* pairs: the candidate and the destination are both usable and
M1 reports the pair ``ELIGIBLE``. Everything is integer millicores / bytes / pod
slots; no floating point is used for capacity decisions.

Nothing here mutates the M1 model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Any

from qplace.drain import Replacement
from qplace.eligibility import Eligibility
from qplace.model import ClusterModel
from qplace.resources import AccountingStatus


class Code(StrEnum):
    """M2 reason codes. Values are part of the artifact contract."""

    # Candidate exclusions
    REQUEST_UNKNOWN = "REQUEST_UNKNOWN"
    NO_ALLOWED_DESTINATION = "NO_ALLOWED_DESTINATION"
    # Destination exclusions
    DRAIN_TARGET = "DRAIN_TARGET"
    HEADROOM_UNKNOWN = "HEADROOM_UNKNOWN"
    ALLOCATABLE_UNKNOWN = "ALLOCATABLE_UNKNOWN"
    BASELINE_OVERCOMMITTED = "BASELINE_OVERCOMMITTED"
    # Problem-level conditions that prevent a conclusive assessment
    CAPACITY_ASSESSMENT_INCOMPLETE = "CAPACITY_ASSESSMENT_INCOMPLETE"
    CANDIDATE_REQUEST_UNKNOWN = "CANDIDATE_REQUEST_UNKNOWN"
    CANDIDATE_REQUEST_REVIEW_REQUIRED = "CANDIDATE_REQUEST_REVIEW_REQUIRED"
    DESTINATION_HEADROOM_UNKNOWN = "DESTINATION_HEADROOM_UNKNOWN"
    DESTINATION_ACCOUNTING_REVIEW_REQUIRED = "DESTINATION_ACCOUNTING_REVIEW_REQUIRED"
    ELIGIBILITY_UNKNOWN = "ELIGIBILITY_UNKNOWN"
    REPLACEMENT_CONDITIONAL = "REPLACEMENT_CONDITIONAL"
    REPLACEMENT_UNKNOWN = "REPLACEMENT_UNKNOWN"
    COMPETING_DEMAND_UNBOUND_PODS = "COMPETING_DEMAND_UNBOUND_PODS"
    TERMINATING_TARGET_POD = "TERMINATING_TARGET_POD"
    # Standing limitations (informational; do not by themselves block a conclusion)
    REPLACEMENT_TEMPLATE_UNKNOWN = "REPLACEMENT_TEMPLATE_UNKNOWN"
    OVERCOMMITTED_DESTINATION_EXCLUDED = "OVERCOMMITTED_DESTINATION_EXCLUDED"
    PDB_ADVISORY = "PDB_ADVISORY"
    NON_CANDIDATE_TARGET_PODS = "NON_CANDIDATE_TARGET_PODS"
    # Rejections recorded by solvers
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ELIGIBILITY_UNKNOWN_PAIR = "ELIGIBILITY_UNKNOWN_PAIR"
    DESTINATION_EXCLUDED = "DESTINATION_EXCLUDED"
    INSUFFICIENT_CPU = "INSUFFICIENT_CPU"
    INSUFFICIENT_MEMORY = "INSUFFICIENT_MEMORY"
    INSUFFICIENT_POD_SLOTS = "INSUFFICIENT_POD_SLOTS"
    NOT_SELECTED = "NOT_SELECTED"


@dataclass(frozen=True)
class Condition:
    code: Code
    subject: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "subject": self.subject, "detail": self.detail}


@dataclass(frozen=True)
class CandidateSpec:
    pod: str
    cpu_millicores: int | None
    memory_bytes: int | None
    request_status: AccountingStatus
    replacement: Replacement
    allowed_nodes: tuple[str, ...]  # ELIGIBLE pairs on usable destinations
    unknown_nodes: tuple[str, ...]  # UNKNOWN pairs on destinations not excluded by policy
    ineligible_nodes: tuple[str, ...]
    exclusion_reasons: tuple[Code, ...]

    @property
    def usable(self) -> bool:
        return not self.exclusion_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod": self.pod,
            "cpu_millicores": self.cpu_millicores,
            "memory_bytes": self.memory_bytes,
            "request_status": str(self.request_status),
            "replacement": str(self.replacement),
            "allowed_nodes": list(self.allowed_nodes),
            "unknown_nodes": list(self.unknown_nodes),
            "ineligible_nodes": list(self.ineligible_nodes),
            "usable": self.usable,
            "exclusion_reasons": [str(c) for c in self.exclusion_reasons],
        }


@dataclass(frozen=True)
class DestinationSpec:
    node: str
    cpu_millicores: int | None  # remaining (may be negative; preserved)
    memory_bytes: int | None
    pod_slots: int | None
    allocatable_cpu_millicores: int | None
    allocatable_memory_bytes: int | None
    allocatable_pods: int | None
    accounting_status: AccountingStatus
    exclusion_reasons: tuple[Code, ...]

    @property
    def usable(self) -> bool:
        return not self.exclusion_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "remaining": {
                "cpu_millicores": self.cpu_millicores,
                "memory_bytes": self.memory_bytes,
                "pods": self.pod_slots,
            },
            "allocatable": {
                "cpu_millicores": self.allocatable_cpu_millicores,
                "memory_bytes": self.allocatable_memory_bytes,
                "pods": self.allocatable_pods,
            },
            "accounting_status": str(self.accounting_status),
            "usable": self.usable,
            "exclusion_reasons": [str(c) for c in self.exclusion_reasons],
        }


@dataclass(frozen=True)
class PlacementProblem:
    target_node: str
    provenance: dict[str, Any]
    candidates: tuple[CandidateSpec, ...]  # sorted by pod key
    destinations: tuple[DestinationSpec, ...]  # sorted by node name
    conditions: tuple[Condition, ...]  # uncertainty that blocks a conclusive assessment
    limitations: tuple[Condition, ...]  # standing, informational
    # Non-INELIGIBLE pairs dropped only by the overcommitment planning policy. They do not
    # prevent MODELED_FEASIBLE, but a shortage cannot be called MODELED_BLOCKED while they exist.
    policy_excluded_pairs: tuple[tuple[str, str], ...] = ()

    @property
    def usable_candidates(self) -> tuple[CandidateSpec, ...]:
        return tuple(c for c in self.candidates if c.usable)

    @property
    def usable_destinations(self) -> tuple[DestinationSpec, ...]:
        return tuple(d for d in self.destinations if d.usable)

    @property
    def variables(self) -> tuple[tuple[str, str], ...]:
        """Binary decision variables x[pod, node], in canonical order."""
        return tuple((c.pod, n) for c in self.usable_candidates for n in c.allowed_nodes)

    def candidate(self, pod: str) -> CandidateSpec:
        return next(c for c in self.candidates if c.pod == pod)

    def destination(self, node: str) -> DestinationSpec:
        return next(d for d in self.destinations if d.node == node)

    @property
    def is_conditional(self) -> bool:
        return bool(self.conditions)


def _sorted_conditions(items: Iterable[Condition]) -> tuple[Condition, ...]:
    return tuple(sorted(set(items), key=lambda c: (str(c.code), c.subject, c.detail)))


def build_placement_problem(model: ClusterModel) -> PlacementProblem:
    """Derive the M2 placement problem from an M1 model without modifying it."""
    target = model.target_node
    accounts = model.resources.nodes
    conditions: list[Condition] = []
    limitations: list[Condition] = []

    destinations: list[DestinationSpec] = []
    for name in sorted(model.destination_nodes):
        a = accounts[name]
        reasons: list[Code] = []
        if None in (a.allocatable_cpu_millicores, a.allocatable_memory_bytes, a.allocatable_pods):
            reasons.append(Code.ALLOCATABLE_UNKNOWN)
        if None in (a.remaining_cpu_millicores, a.remaining_memory_bytes, a.remaining_pod_slots):
            reasons.append(Code.HEADROOM_UNKNOWN)
        negative = any(v is not None and v < 0 for v in
                       (a.remaining_cpu_millicores, a.remaining_memory_bytes, a.remaining_pod_slots))
        if a.overcommitted or negative:
            # Deliberate M2 planning restriction (see docs/m2/limitations.md).
            reasons.append(Code.BASELINE_OVERCOMMITTED)
        destinations.append(
            DestinationSpec(
                node=name,
                cpu_millicores=a.remaining_cpu_millicores,
                memory_bytes=a.remaining_memory_bytes,
                pod_slots=a.remaining_pod_slots,
                allocatable_cpu_millicores=a.allocatable_cpu_millicores,
                allocatable_memory_bytes=a.allocatable_memory_bytes,
                allocatable_pods=a.allocatable_pods,
                accounting_status=a.status,
                exclusion_reasons=tuple(reasons),
            )
        )
    dest_by_name = {d.node: d for d in destinations}

    states: dict[tuple[str, str], Eligibility] = {
        (r.pod, r.node): r.eligibility.state for r in model.eligibility
    }
    drain_records = {r.pod: r for r in model.drain.pods}

    candidates: list[CandidateSpec] = []
    policy_excluded: list[tuple[str, str]] = []
    for pod in sorted(model.drain.placement_candidates):
        req = model.resources.pod_requests[pod]
        record = drain_records[pod]
        allowed: list[str] = []
        unknown: list[str] = []
        ineligible: list[str] = []
        for d in destinations:
            state = states.get((pod, d.node), Eligibility.UNKNOWN)
            if state is not Eligibility.INELIGIBLE and Code.BASELINE_OVERCOMMITTED in d.exclusion_reasons:
                policy_excluded.append((pod, d.node))
            if state is Eligibility.INELIGIBLE:
                ineligible.append(d.node)
            elif state is Eligibility.UNKNOWN:
                if Code.BASELINE_OVERCOMMITTED not in d.exclusion_reasons:
                    unknown.append(d.node)
            elif d.usable:
                allowed.append(d.node)
            elif Code.BASELINE_OVERCOMMITTED not in d.exclusion_reasons:
                # ELIGIBLE, but headroom/allocatable unknown: the pair could matter.
                unknown.append(d.node)

        reasons: list[Code] = []
        if req.cpu_millicores is None or req.memory_bytes is None:
            reasons.append(Code.REQUEST_UNKNOWN)
            conditions.append(Condition(Code.CANDIDATE_REQUEST_UNKNOWN, pod, ",".join(sorted(map(str, req.reasons)))))
        elif req.status is AccountingStatus.REVIEW_REQUIRED:
            conditions.append(
                Condition(Code.CANDIDATE_REQUEST_REVIEW_REQUIRED, pod, ",".join(sorted(map(str, req.reasons))))
            )
        if not allowed:
            reasons.append(Code.NO_ALLOWED_DESTINATION)
        for node in unknown:
            dest = dest_by_name[node]
            if dest.usable:
                row = next(r for r in model.eligibility if r.pod == pod and r.node == node)
                detail = ",".join(sorted({str(f.code) for f in row.eligibility.unknowns}))
                conditions.append(Condition(Code.ELIGIBILITY_UNKNOWN, f"{pod}@{node}", detail))
            else:
                conditions.append(Condition(Code.DESTINATION_HEADROOM_UNKNOWN, f"{pod}@{node}",
                                            ",".join(map(str, dest.exclusion_reasons))))
        if record.replacement is Replacement.CONDITIONAL:
            conditions.append(Condition(Code.REPLACEMENT_CONDITIONAL, pod, f"controller {record.controller_kind}"))
        elif record.replacement is Replacement.UNKNOWN:
            conditions.append(Condition(Code.REPLACEMENT_UNKNOWN, pod,
                                        f"controller {record.controller_api_version}/{record.controller_kind}"))
        limitations.append(Condition(Code.REPLACEMENT_TEMPLATE_UNKNOWN, pod,
                                     "replacement modeled from the current pod spec"))
        for node in allowed:
            if dest_by_name[node].accounting_status is AccountingStatus.REVIEW_REQUIRED:
                conditions.append(Condition(Code.DESTINATION_ACCOUNTING_REVIEW_REQUIRED, node,
                                            "destination reservations include REVIEW_REQUIRED pod requests"))

        candidates.append(
            CandidateSpec(
                pod=pod,
                cpu_millicores=req.cpu_millicores,
                memory_bytes=req.memory_bytes,
                request_status=req.status,
                replacement=record.replacement,
                allowed_nodes=tuple(allowed),
                unknown_nodes=tuple(unknown),
                ineligible_nodes=tuple(ineligible),
                exclusion_reasons=tuple(reasons),
            )
        )

    if not model.completeness.capacity_assessment_complete:
        for reason in model.completeness.reasons:
            conditions.append(Condition(Code.CAPACITY_ASSESSMENT_INCOMPLETE, "cluster", reason))

    for d in destinations:
        if Code.BASELINE_OVERCOMMITTED in d.exclusion_reasons:
            limitations.append(
                Condition(Code.OVERCOMMITTED_DESTINATION_EXCLUDED, d.node,
                          f"remaining cpu={d.cpu_millicores} memory={d.memory_bytes} pods={d.pod_slots}")
            )
    for pdb in model.drain.pdbs:
        limitations.append(Condition(Code.PDB_ADVISORY, pdb.pdb, str(pdb.assessment)))
    unbound = [d for d in model.resources.diagnostics if str(d.code) == "UNBOUND_PENDING_PODS_PRESENT"]
    for d in unbound:
        conditions.append(Condition(Code.COMPETING_DEMAND_UNBOUND_PODS, "cluster",
                                    ", ".join(d.details.get("pods", []))))
    for r in model.drain.pods:
        if str(r.drain_class) == "TERMINATING" and r.controller_kind is not None:
            conditions.append(Condition(Code.TERMINATING_TARGET_POD, r.pod,
                                        "replacement may already compete for destination capacity"))
    non_candidates = [r for r in model.drain.pods if not r.placement_candidate]
    if non_candidates:
        limitations.append(
            Condition(Code.NON_CANDIDATE_TARGET_PODS, target,
                      ", ".join(f"{r.pod}:{r.drain_class}" for r in non_candidates))
        )

    prov = model.snapshot.provenance.to_dict()
    return PlacementProblem(
        target_node=target,
        provenance={
            "snapshot_id": prov["snapshot_id"],
            "captured_at": prov["captured_at"],
            "source_type": prov["source_type"],
            "source_file": prov["source_file"],
            "source_sha256": prov["source_sha256"],
            "kubernetes_version": prov["kubernetes_version"],
            "snapshot_schema_version": prov["schema_version"],
            "capacity_assessment_complete": model.completeness.capacity_assessment_complete,
        },
        candidates=tuple(candidates),
        destinations=tuple(destinations),
        conditions=_sorted_conditions(conditions),
        limitations=_sorted_conditions(limitations),
        policy_excluded_pairs=tuple(sorted(policy_excluded)),
    )


# --------------------------------------------------------------------------- objective


@dataclass(frozen=True)
class Objective:
    """Lexicographic objective. Larger ``assigned`` wins, then larger ``balance``
    (leximin over destinations), then smaller ``tiebreak``."""

    assigned: int
    balance: tuple[Fraction, ...]
    tiebreak: tuple[tuple[int, str], ...]

    def better_than(self, other: Objective | None) -> bool:
        if other is None:
            return True
        if self.assigned != other.assigned:
            return self.assigned > other.assigned
        if self.balance != other.balance:
            return self.balance > other.balance
        return self.tiebreak < other.tiebreak

    def to_dict(self) -> dict[str, Any]:
        return {
            "assigned": self.assigned,
            "balance_leximin": [f"{f.numerator}/{f.denominator}" for f in self.balance],
            "min_free_fraction": None if not self.balance else
            f"{self.balance[0].numerator}/{self.balance[0].denominator}",
        }


def free_fraction(dest: DestinationSpec, used_cpu: int, used_mem: int, used_pods: int) -> Fraction:
    """min over resources of (remaining - used) / allocatable, exact."""
    terms = []
    for remaining, used, alloc in (
        (dest.cpu_millicores, used_cpu, dest.allocatable_cpu_millicores),
        (dest.memory_bytes, used_mem, dest.allocatable_memory_bytes),
        (dest.pod_slots, used_pods, dest.allocatable_pods),
    ):
        if alloc:  # skip zero-allocatable resources (term undefined)
            terms.append(Fraction(remaining - used, alloc))  # type: ignore[operator]
    return min(terms) if terms else Fraction(0)  # no measurable capacity: treat as full


def balance_vector(problem: PlacementProblem, usage: Mapping[str, tuple[int, int, int]]) -> tuple[Fraction, ...]:
    return tuple(sorted(free_fraction(d, *usage.get(d.node, (0, 0, 0))) for d in problem.usable_destinations))


def tiebreak_vector(problem: PlacementProblem, assignment: Mapping[str, str]) -> tuple[tuple[int, str], ...]:
    return tuple((0, assignment[c.pod]) if c.pod in assignment else (1, "") for c in problem.candidates)


def evaluate_objective(problem: PlacementProblem, assignment: Mapping[str, str]) -> Objective:
    usage: dict[str, list[int]] = {}
    for pod, node in assignment.items():
        c = problem.candidate(pod)
        u = usage.setdefault(node, [0, 0, 0])
        u[0] += c.cpu_millicores or 0
        u[1] += c.memory_bytes or 0
        u[2] += 1
    return Objective(
        assigned=len(assignment),
        balance=balance_vector(problem, {k: (v[0], v[1], v[2]) for k, v in usage.items()}),
        tiebreak=tiebreak_vector(problem, assignment),
    )


def placement_order(problem: PlacementProblem) -> tuple[CandidateSpec, ...]:
    """Documented stable order: CPU desc, memory desc, pod key asc (first-fit decreasing)."""
    return tuple(sorted(problem.usable_candidates,
                        key=lambda c: (-(c.cpu_millicores or 0), -(c.memory_bytes or 0), c.pod)))
