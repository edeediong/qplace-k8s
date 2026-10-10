"""Effective pod requests and per-node requested-resource accounting.

Mirrors ``k8s.io/component-helpers/resource.PodRequests`` as used by the
scheduler's NodeResourcesFit filter (see docs/m1/kubernetes-semantics.md):

1. regular containers are summed;
2. restartable init containers (sidecars) are added to that sum, and each
   ordinary init container's peak is ``its request + sidecars started before it``;
   the result is ``max(sum, max(init peaks))``;
3. supported pod-level requests (cpu, memory) replace the container aggregate;
4. ``spec.overhead`` is added once;
5. when container status reports resources (in-place resize), each regular or
   sidecar container contributes ``max(spec, status.resources, allocatedResources)``,
   or ``max(status.resources, allocatedResources)`` if the resize is Infeasible.

All values are CPU millicores / memory bytes. ``None`` always means unknown and
is accompanied by a reason; it is never silently replaced by zero.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from qplace.diagnostics import Diagnostic, QuantityError, Reason, Severity, dedupe_diagnostics
from qplace.nodes import NodeModel
from qplace.pods import ContainerSpec, PodModel
from qplace.quantity import parse_cpu_millicores, parse_memory_bytes
from qplace.snapshot import InventoryCompleteness

_PARSERS: dict[str, Callable[[object], int]] = {"cpu": parse_cpu_millicores, "memory": parse_memory_bytes}
RESOURCE_FIELDS = {"cpu": "cpu_millicores", "memory": "memory_bytes"}

POD_LEVEL_RESOURCES_DEFAULT_ON = (1, 34)  # PodLevelResources beta, enabled by default
SIDECARS_DEFAULT_ON = (1, 29)  # SidecarContainers beta, enabled by default
IN_PLACE_RESIZE_DEFAULT_ON = (1, 33)  # InPlacePodVerticalScaling beta, enabled by default


class AccountingStatus(StrEnum):
    EXACT = "EXACT"  # value follows from modeled Kubernetes semantics
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # value present, but feature state or data is ambiguous
    INCOMPLETE = "INCOMPLETE"  # inventory is partial; totals are bounds, not exact values
    UNKNOWN = "UNKNOWN"  # at least one value cannot be determined


_STATUS_RANK = {
    AccountingStatus.EXACT: 0,
    AccountingStatus.REVIEW_REQUIRED: 1,
    AccountingStatus.INCOMPLETE: 2,
    AccountingStatus.UNKNOWN: 3,
}


def worst_status(statuses: Iterable[AccountingStatus]) -> AccountingStatus:
    return max(statuses, key=_STATUS_RANK.__getitem__, default=AccountingStatus.EXACT)


class _Unknown(Exception):
    pass


@dataclass(frozen=True)
class PodRequests:
    pod_key: str
    cpu_millicores: int | None
    memory_bytes: int | None
    status: AccountingStatus
    method: tuple[str, ...]
    reasons: tuple[Reason, ...]
    breakdown: dict[str, dict[str, int | None]]
    diagnostics: tuple[Diagnostic, ...]

    def value(self, resource: str) -> int | None:
        return getattr(self, RESOURCE_FIELDS[resource])

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod": self.pod_key,
            "cpu_millicores": self.cpu_millicores,
            "memory_bytes": self.memory_bytes,
            "status": str(self.status),
            "method": list(self.method),
            "reasons": sorted(str(r) for r in self.reasons),
            "breakdown": self.breakdown,
        }


@dataclass
class _Ctx:
    pod: PodModel
    kubernetes_minor: tuple[int, int] | None
    method: list[str] = field(default_factory=list)
    reasons: set[Reason] = field(default_factory=set)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    review: bool = False

    def note(self, step: str) -> None:
        if step not in self.method:
            self.method.append(step)

    def flag(self, reason: Reason, severity: Severity, message: str, review: bool = False, **details: Any) -> None:
        self.reasons.add(reason)
        self.review = self.review or review
        diag = Diagnostic(reason, severity, f"pod/{self.pod.key}", message, details)
        if diag not in self.diagnostics:
            self.diagnostics.append(diag)

    def parse(self, resource: str, raw: object, where: str) -> int:
        try:
            return _PARSERS[resource](raw)
        except QuantityError as exc:
            self.flag(exc.reason, Severity.ERROR, f"{where}: {exc}", raw=str(raw), resource=resource)
            raise _Unknown from exc

    def version_before(self, minimum: tuple[int, int]) -> bool | None:
        """True/False when the version is known, None when it is not."""
        if self.kubernetes_minor is None:
            return None
        return self.kubernetes_minor < minimum


def _resize_infeasible(pod: PodModel) -> bool:
    for cond in pod.conditions:
        if cond.get("type") == "PodResizePending" and cond.get("reason") == "Infeasible":
            return True
    return pod.legacy_resize_status == "Infeasible"


def _resize_indicated(pod: PodModel) -> bool:
    if pod.legacy_resize_status:
        return True
    return any(c.get("type") in ("PodResizePending", "PodResizeInProgress") for c in pod.conditions)


def _spec_request(ctx: _Ctx, c: ContainerSpec, resource: str) -> int:
    if resource in c.requests:
        return ctx.parse(resource, c.requests[resource], f"{c.name}.requests.{resource}")
    if resource in c.limits:
        # API defaulting sets a missing request equal to the limit. An exported
        # live Pod has already been defaulted, so reaching this branch means the
        # input was probably never admitted; record that explicitly.
        ctx.flag(
            Reason.REQUEST_DEFAULTED_FROM_LIMIT,
            Severity.INFO,
            f"container {c.name}: {resource} request absent, defaulted from limit",
            container=c.name,
            resource=resource,
        )
        ctx.note("request_defaulted_from_limit")
        return ctx.parse(resource, c.limits[resource], f"{c.name}.limits.{resource}")
    return 0


def _container_request(ctx: _Ctx, c: ContainerSpec, resource: str, statuses: dict[str, dict[str, Any]]) -> int:
    spec_value = _spec_request(ctx, c, resource)
    status = statuses.get(c.name)
    if not status or status.get("resources") is None:
        return spec_value
    candidates: list[int] = [] if _resize_infeasible(ctx.pod) else [spec_value]
    actual = (status["resources"].get("requests") or {}).get(resource)
    if actual is not None:
        candidates.append(ctx.parse(resource, actual, f"status {c.name}.resources.requests.{resource}"))
    allocated = (status.get("allocatedResources") or {}).get(resource)
    if allocated is not None:
        candidates.append(ctx.parse(resource, allocated, f"status {c.name}.allocatedResources.{resource}"))
    value = max(candidates, default=0)
    ctx.note("status_resources_considered")
    if value != spec_value:
        ctx.flag(
            Reason.RESIZE_STATUS_APPLIED,
            Severity.INFO,
            f"container {c.name}: {resource} reservation taken from resize status ({value}) instead of spec ({spec_value})",
            container=c.name,
            resource=resource,
            spec=spec_value,
            effective=value,
        )
    return value


def _effective(ctx: _Ctx, resource: str) -> tuple[int, dict[str, int | None]]:
    pod = ctx.pod
    containers_sum = sum(_container_request(ctx, c, resource, pod.container_statuses) for c in pod.containers)
    ctx.note("sum_containers")

    sidecars_sum = 0
    init_peak = 0
    for c in pod.init_containers:
        if c.role == "sidecar":
            value = _container_request(ctx, c, resource, pod.init_container_statuses)
            sidecars_sum += value
            # A sidecar keeps running; while it starts, earlier sidecars are also running.
            init_peak = max(init_peak, sidecars_sum)
            ctx.note("restartable_init_containers_added")
        else:
            value = _spec_request(ctx, c, resource)
            init_peak = max(init_peak, value + sidecars_sum)
            ctx.note("init_container_peak")

    aggregate = max(containers_sum + sidecars_sum, init_peak)
    pod_level: int | None = None
    total = aggregate

    if resource in pod.pod_requests or resource in pod.pod_limits:
        if resource in pod.pod_requests:
            pod_level = ctx.parse(resource, pod.pod_requests[resource], f"spec.resources.requests.{resource}")
        else:
            pod_level = ctx.parse(resource, pod.pod_limits[resource], f"spec.resources.limits.{resource}")
            ctx.flag(
                Reason.REQUEST_DEFAULTED_FROM_LIMIT,
                Severity.WARNING,
                f"pod-level {resource} limit without request; using the limit as a conservative upper bound",
                review=True,
                resource=resource,
            )
        ctx.note("pod_level_requests_applied")
        ctx.flag(
            Reason.POD_LEVEL_REQUESTS_APPLIED,
            Severity.INFO,
            f"pod-level {resource} request replaces the container aggregate",
            resource=resource,
        )
        if ctx.version_before(POD_LEVEL_RESOURCES_DEFAULT_ON) is not False:
            ctx.flag(
                Reason.POD_LEVEL_FEATURE_STATE_UNCERTAIN,
                Severity.WARNING,
                "pod-level resources are only enabled by default from Kubernetes 1.34; "
                "scheduler feature state is unknown for this snapshot",
                review=True,
                kubernetes_version=_fmt_minor(ctx.kubernetes_minor),
            )
        if pod_level < aggregate:
            ctx.flag(
                Reason.POD_LEVEL_BELOW_CONTAINER_AGGREGATE,
                Severity.WARNING,
                f"pod-level {resource} request {pod_level} is below the container aggregate {aggregate}; "
                "using the larger value",
                review=True,
                resource=resource,
            )
        total = max(pod_level, aggregate)

    overhead = 0
    if resource in pod.overhead:
        overhead = ctx.parse(resource, pod.overhead[resource], f"spec.overhead.{resource}")
        ctx.note("overhead_added")
    total += overhead

    return total, {
        "containers": containers_sum,
        "restartable_init_containers": sidecars_sum,
        "init_peak": init_peak,
        "container_aggregate": aggregate,
        "pod_level": pod_level,
        "overhead": overhead,
        "effective": total,
    }


def _fmt_minor(minor: tuple[int, int] | None) -> str | None:
    return None if minor is None else f"{minor[0]}.{minor[1]}"


def calculate_pod_requests(pod: PodModel, kubernetes_minor: tuple[int, int] | None = None) -> PodRequests:
    """Effective scheduler-reserved CPU/memory requests of one pod."""
    ctx = _Ctx(pod=pod, kubernetes_minor=kubernetes_minor)
    values: dict[str, int | None] = {}
    breakdown: dict[str, dict[str, int | None]] = {}
    unknown = False

    resize_blocked = False
    if _resize_indicated(pod):
        if pod.pod_requests or pod.pod_limits:
            resize_blocked = True
            ctx.flag(
                Reason.POD_LEVEL_RESIZE_NOT_MODELED,
                Severity.UNSUPPORTED,
                "pod has pod-level resources and an in-progress/pending resize; reservation cannot be modeled",
            )
        statuses = {**pod.container_statuses, **pod.init_container_statuses}
        running = list(pod.containers) + [c for c in pod.init_containers if c.role == "sidecar"]
        missing = sorted(c.name for c in running if (statuses.get(c.name) or {}).get("resources") is None)
        if missing:
            resize_blocked = True
            ctx.flag(
                Reason.RESIZE_STATUS_INCOMPLETE,
                Severity.WARNING,
                "pod is resizing but container status lacks resource information; reservation is unknown",
                containers=missing,
            )
        if ctx.version_before(IN_PLACE_RESIZE_DEFAULT_ON) is not False:
            ctx.flag(
                Reason.RESIZE_FEATURE_STATE_UNCERTAIN,
                Severity.WARNING,
                "in-place resize accounting semantics are only stable from Kubernetes 1.33",
                review=True,
                kubernetes_version=_fmt_minor(kubernetes_minor),
            )

    if any(c.role == "sidecar" for c in pod.init_containers) and ctx.version_before(SIDECARS_DEFAULT_ON):
        ctx.flag(
            Reason.SIDECAR_FEATURE_STATE_UNCERTAIN,
            Severity.WARNING,
            "restartable init containers are only enabled by default from Kubernetes 1.29",
            review=True,
            kubernetes_version=_fmt_minor(kubernetes_minor),
        )

    for resource in ("cpu", "memory"):
        if resize_blocked:
            values[resource] = None
            breakdown[resource] = {"effective": None}
            unknown = True
            continue
        try:
            values[resource], breakdown[resource] = _effective(ctx, resource)
        except _Unknown:
            values[resource] = None
            breakdown[resource] = {"effective": None}
            unknown = True

    if unknown:
        ctx.reasons.add(Reason.POD_REQUEST_UNKNOWN)
        status = AccountingStatus.UNKNOWN
    elif ctx.review:
        ctx.reasons.add(Reason.POD_REQUEST_REVIEW_REQUIRED)
        status = AccountingStatus.REVIEW_REQUIRED
    else:
        status = AccountingStatus.EXACT

    return PodRequests(
        pod_key=pod.key,
        cpu_millicores=values["cpu"],
        memory_bytes=values["memory"],
        status=status,
        method=tuple(ctx.method),
        reasons=tuple(sorted(ctx.reasons)),
        breakdown=breakdown,
        diagnostics=dedupe_diagnostics(ctx.diagnostics),
    )


# --------------------------------------------------------------------------- node accounting


class ReservationPolicy(StrEnum):
    RESERVED = "RESERVED"
    RESERVED_TERMINATING = "RESERVED_TERMINATING"
    RESERVED_UNKNOWN_PHASE = "RESERVED_UNKNOWN_PHASE"
    NOT_RESERVED_TERMINAL = "NOT_RESERVED_TERMINAL"
    NOT_RESERVED_UNBOUND = "NOT_RESERVED_UNBOUND"


def reservation_policy(pod: PodModel) -> ReservationPolicy:
    """Whether a pod occupies requested capacity on its node.

    The scheduler's pod informer excludes Succeeded/Failed pods, so terminal
    pods free their requests. Terminating pods remain in the scheduler cache
    until the API object is deleted, so they still reserve capacity.
    """
    if not pod.node_name:
        return ReservationPolicy.NOT_RESERVED_UNBOUND
    if pod.is_terminal:
        return ReservationPolicy.NOT_RESERVED_TERMINAL
    if pod.is_terminating:
        return ReservationPolicy.RESERVED_TERMINATING
    if pod.phase in ("Pending", "Running"):
        return ReservationPolicy.RESERVED
    return ReservationPolicy.RESERVED_UNKNOWN_PHASE


RESERVING_POLICIES = frozenset(
    {ReservationPolicy.RESERVED, ReservationPolicy.RESERVED_TERMINATING, ReservationPolicy.RESERVED_UNKNOWN_PHASE}
)


@dataclass(frozen=True)
class NodeAccount:
    node: str
    is_drain_target: bool
    allocatable_cpu_millicores: int | None
    allocatable_memory_bytes: int | None
    allocatable_pods: int | None
    reserved_cpu_millicores: int | None
    reserved_memory_bytes: int | None
    reserved_pod_count: int | None
    reserved_cpu_millicores_lower_bound: int
    reserved_memory_bytes_lower_bound: int
    reserved_pod_count_lower_bound: int
    remaining_cpu_millicores: int | None
    remaining_memory_bytes: int | None
    remaining_pod_slots: int | None
    remaining_cpu_millicores_upper_bound: int | None
    remaining_memory_bytes_upper_bound: int | None
    remaining_pod_slots_upper_bound: int | None
    overcommitted: tuple[str, ...]
    status: AccountingStatus
    reasons: tuple[Reason, ...]
    reserving_pods: tuple[str, ...]

    def remaining(self, resource: str) -> int | None:
        return {"cpu": self.remaining_cpu_millicores, "memory": self.remaining_memory_bytes,
                "pods": self.remaining_pod_slots}[resource]

    def remaining_upper_bound(self, resource: str) -> int | None:
        return {"cpu": self.remaining_cpu_millicores_upper_bound,
                "memory": self.remaining_memory_bytes_upper_bound,
                "pods": self.remaining_pod_slots_upper_bound}[resource]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "is_drain_target": self.is_drain_target,
            "allocatable": {
                "cpu_millicores": self.allocatable_cpu_millicores,
                "memory_bytes": self.allocatable_memory_bytes,
                "pods": self.allocatable_pods,
            },
            "reserved": {
                "cpu_millicores": self.reserved_cpu_millicores,
                "memory_bytes": self.reserved_memory_bytes,
                "pods": self.reserved_pod_count,
            },
            "reserved_lower_bound": {
                "cpu_millicores": self.reserved_cpu_millicores_lower_bound,
                "memory_bytes": self.reserved_memory_bytes_lower_bound,
                "pods": self.reserved_pod_count_lower_bound,
            },
            "remaining": {
                "cpu_millicores": self.remaining_cpu_millicores,
                "memory_bytes": self.remaining_memory_bytes,
                "pods": self.remaining_pod_slots,
            },
            "remaining_upper_bound": {
                "cpu_millicores": self.remaining_cpu_millicores_upper_bound,
                "memory_bytes": self.remaining_memory_bytes_upper_bound,
                "pods": self.remaining_pod_slots_upper_bound,
            },
            "overcommitted": list(self.overcommitted),
            "status": str(self.status),
            "reasons": sorted(str(r) for r in self.reasons),
            "reserving_pods": list(self.reserving_pods),
        }


@dataclass(frozen=True)
class ResourceInventory:
    nodes: dict[str, NodeAccount]
    pod_requests: dict[str, PodRequests]
    reservation: dict[str, ReservationPolicy]
    diagnostics: tuple[Diagnostic, ...]


def _sub(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a - b


def build_resource_inventory(
    nodes: Iterable[NodeModel],
    pods: Iterable[PodModel],
    inventory: InventoryCompleteness,
    target_node: str,
    kubernetes_minor: tuple[int, int] | None = None,
) -> ResourceInventory:
    nodes = sorted(nodes, key=lambda n: n.name)
    pods = sorted(pods, key=lambda p: p.key)
    requests = {p.key: calculate_pod_requests(p, kubernetes_minor) for p in pods}
    policies = {p.key: reservation_policy(p) for p in pods}
    diagnostics: list[Diagnostic] = []
    for r in requests.values():
        diagnostics.extend(r.diagnostics)

    unbound_pending = sorted(p.key for p in pods if policies[p.key] is ReservationPolicy.NOT_RESERVED_UNBOUND
                             and not p.is_terminal)
    if unbound_pending:
        diagnostics.append(
            Diagnostic(
                Reason.UNBOUND_PENDING_PODS_PRESENT,
                Severity.WARNING,
                "cluster",
                "unscheduled pods exist and may compete for destination capacity; they are not reserved on any node",
                {"pods": unbound_pending},
            )
        )

    accounts: dict[str, NodeAccount] = {}
    for node in nodes:
        reasons: set[Reason] = set()
        statuses: list[AccountingStatus] = []
        reserving = [p for p in pods if p.node_name == node.name and policies[p.key] in RESERVING_POLICIES]
        cpu_lb = sum(requests[p.key].cpu_millicores or 0 for p in reserving)
        mem_lb = sum(requests[p.key].memory_bytes or 0 for p in reserving)
        cpu_known = all(requests[p.key].cpu_millicores is not None for p in reserving)
        mem_known = all(requests[p.key].memory_bytes is not None for p in reserving)

        for p in reserving:
            if requests[p.key].status is AccountingStatus.UNKNOWN:
                reasons.add(Reason.POD_REQUEST_UNKNOWN)
                statuses.append(AccountingStatus.UNKNOWN)
            elif requests[p.key].status is AccountingStatus.REVIEW_REQUIRED:
                reasons.add(Reason.POD_REQUEST_REVIEW_REQUIRED)
                statuses.append(AccountingStatus.REVIEW_REQUIRED)
            if policies[p.key] is ReservationPolicy.RESERVED_UNKNOWN_PHASE:
                reasons.add(Reason.UNKNOWN_PHASE_RESERVED)
        if any(p.node_name == node.name and policies[p.key] is ReservationPolicy.NOT_RESERVED_TERMINAL for p in pods):
            reasons.add(Reason.TERMINAL_POD_NOT_RESERVED)
        nominated = sorted(p.key for p in pods if not p.node_name and p.nominated_node_name == node.name)
        if nominated:
            reasons.add(Reason.NOMINATED_POD_NOT_RESERVED)
            diagnostics.append(
                Diagnostic(
                    Reason.NOMINATED_POD_NOT_RESERVED,
                    Severity.WARNING,
                    f"node/{node.name}",
                    "pods are nominated to this node (preemption in progress) but not yet bound",
                    {"pods": nominated},
                )
            )

        alloc = node.allocatable
        if None in (alloc.cpu_millicores, alloc.memory_bytes, alloc.pods):
            reasons.add(Reason.ALLOCATABLE_MISSING)
            statuses.append(AccountingStatus.UNKNOWN)

        complete = inventory.pods_complete
        if not complete:
            reasons.add(Reason.INVENTORY_INCOMPLETE)
            statuses.append(AccountingStatus.INCOMPLETE)
        if not (cpu_known and mem_known):
            statuses.append(AccountingStatus.UNKNOWN)

        reserved_cpu = cpu_lb if complete and cpu_known else None
        reserved_mem = mem_lb if complete and mem_known else None
        reserved_pods = len(reserving) if complete else None

        overcommitted = []
        for name, reserved_lb, allocatable in (
            ("cpu", cpu_lb, alloc.cpu_millicores),
            ("memory", mem_lb, alloc.memory_bytes),
            ("pods", len(reserving), alloc.pods),
        ):
            # A lower bound above allocatable proves overcommitment even when inventory is partial.
            if allocatable is not None and reserved_lb > allocatable:
                overcommitted.append(name)
        if overcommitted:
            reasons.add(Reason.OVERCOMMITTED)
            diagnostics.append(
                Diagnostic(
                    Reason.OVERCOMMITTED,
                    Severity.WARNING,
                    f"node/{node.name}",
                    "reserved requests exceed allocatable; negative headroom preserved",
                    {"resources": overcommitted},
                )
            )

        accounts[node.name] = NodeAccount(
            node=node.name,
            is_drain_target=node.name == target_node,
            allocatable_cpu_millicores=alloc.cpu_millicores,
            allocatable_memory_bytes=alloc.memory_bytes,
            allocatable_pods=alloc.pods,
            reserved_cpu_millicores=reserved_cpu,
            reserved_memory_bytes=reserved_mem,
            reserved_pod_count=reserved_pods,
            reserved_cpu_millicores_lower_bound=cpu_lb,
            reserved_memory_bytes_lower_bound=mem_lb,
            reserved_pod_count_lower_bound=len(reserving),
            remaining_cpu_millicores=_sub(alloc.cpu_millicores, reserved_cpu),
            remaining_memory_bytes=_sub(alloc.memory_bytes, reserved_mem),
            remaining_pod_slots=_sub(alloc.pods, reserved_pods),
            remaining_cpu_millicores_upper_bound=_sub(alloc.cpu_millicores, cpu_lb),
            remaining_memory_bytes_upper_bound=_sub(alloc.memory_bytes, mem_lb),
            remaining_pod_slots_upper_bound=_sub(alloc.pods, len(reserving)),
            overcommitted=tuple(overcommitted),
            status=worst_status(statuses),
            reasons=tuple(sorted(reasons)),
            reserving_pods=tuple(p.key for p in reserving),
        )

    return ResourceInventory(
        nodes=accounts,
        pod_requests=requests,
        reservation=policies,
        diagnostics=dedupe_diagnostics(diagnostics),
    )
