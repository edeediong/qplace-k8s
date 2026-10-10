"""Pod normalization and pod-intrinsic scheduling-constraint detection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from qplace.diagnostics import Reason

DEFAULT_SCHEDULER = "default-scheduler"
MIRROR_ANNOTATION = "kubernetes.io/config.mirror"
MODELED_RESOURCES = ("cpu", "memory")
TERMINAL_PHASES = ("Succeeded", "Failed")

# Volume sources with no node-topology implication for scheduling. emptyDir and
# hostPath are node-local data concerns handled by drain classification.
_TOPOLOGY_NEUTRAL_VOLUMES = frozenset(
    {"configMap", "secret", "projected", "downwardAPI", "emptyDir", "hostPath", "nfs", "image", "gitRepo"}
)


@dataclass(frozen=True)
class OwnerRef:
    api_version: str
    kind: str
    name: str
    uid: str | None
    controller: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "kind": self.kind,
            "name": self.name,
            "uid": self.uid,
            "controller": self.controller,
        }


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    role: str  # "container" | "init" | "sidecar" (init container with restartPolicy: Always)
    requests: dict[str, Any]
    limits: dict[str, Any]
    host_ports: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "requests": {k: str(v) for k, v in sorted(self.requests.items())},
            "limits": {k: str(v) for k, v in sorted(self.limits.items())},
            "host_ports": list(self.host_ports),
        }


@dataclass(frozen=True)
class Toleration:
    key: str
    operator: str
    value: str
    effect: str
    toleration_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "operator": self.operator,
            "value": self.value,
            "effect": self.effect,
            "toleration_seconds": self.toleration_seconds,
        }


@dataclass(frozen=True)
class VolumeRef:
    name: str
    source: str  # the volume source key, e.g. "persistentVolumeClaim"
    claim_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source, "claim_name": self.claim_name}


@dataclass(frozen=True)
class ConstraintFinding:
    """A pod-level scheduling constraint. ``kind`` is supported | unsupported | soft."""

    kind: str
    code: Reason | str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "code": str(self.code), "detail": self.detail}


@dataclass(frozen=True)
class PodModel:
    namespace: str
    name: str
    uid: str
    node_name: str | None
    phase: str | None
    deletion_timestamp: str | None
    nominated_node_name: str | None
    owner_references: tuple[OwnerRef, ...]
    controller: OwnerRef | None
    labels: dict[str, str]
    annotations: dict[str, str]
    containers: tuple[ContainerSpec, ...]
    init_containers: tuple[ContainerSpec, ...]
    overhead: dict[str, Any]
    pod_requests: dict[str, Any]
    pod_limits: dict[str, Any]
    node_selector: dict[str, str]
    tolerations: tuple[Toleration, ...]
    affinity: dict[str, Any]
    topology_spread_constraints: tuple[dict[str, Any], ...]
    volumes: tuple[VolumeRef, ...]
    scheduler_name: str
    runtime_class_name: str | None
    resource_claims: tuple[dict[str, Any], ...]
    priority: int | None
    priority_class_name: str | None
    qos_class: str | None
    conditions: tuple[dict[str, Any], ...]
    container_statuses: dict[str, dict[str, Any]]
    init_container_statuses: dict[str, dict[str, Any]]
    legacy_resize_status: str | None
    constraints: tuple[ConstraintFinding, ...]

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def is_mirror(self) -> bool:
        return MIRROR_ANNOTATION in self.annotations

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    @property
    def is_terminating(self) -> bool:
        return self.deletion_timestamp is not None

    @property
    def unsupported_constraints(self) -> tuple[ConstraintFinding, ...]:
        return tuple(c for c in self.constraints if c.kind == "unsupported")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "namespace": self.namespace,
            "name": self.name,
            "uid": self.uid,
            "node_name": self.node_name,
            "phase": self.phase,
            "deletion_timestamp": self.deletion_timestamp,
            "nominated_node_name": self.nominated_node_name,
            "owner_references": [o.to_dict() for o in self.owner_references],
            "controller": self.controller.to_dict() if self.controller else None,
            "is_mirror": self.is_mirror,
            "labels": dict(sorted(self.labels.items())),
            "annotations": dict(sorted(self.annotations.items())),
            "containers": [c.to_dict() for c in self.containers],
            "init_containers": [c.to_dict() for c in self.init_containers],
            "overhead": {k: str(v) for k, v in sorted(self.overhead.items())},
            "pod_level_requests": {k: str(v) for k, v in sorted(self.pod_requests.items())},
            "pod_level_limits": {k: str(v) for k, v in sorted(self.pod_limits.items())},
            "node_selector": dict(sorted(self.node_selector.items())),
            "tolerations": [t.to_dict() for t in self.tolerations],
            "affinity": self.affinity,
            "topology_spread_constraints": list(self.topology_spread_constraints),
            "volumes": [v.to_dict() for v in self.volumes],
            "scheduler_name": self.scheduler_name,
            "runtime_class_name": self.runtime_class_name,
            "resource_claims": list(self.resource_claims),
            "priority": self.priority,
            "priority_class_name": self.priority_class_name,
            "qos_class": self.qos_class,
            "resize": {
                "legacy_status_resize": self.legacy_resize_status,
                "conditions": sorted(
                    (
                        {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
                        for c in self.conditions
                        if c.get("type") in ("PodResizePending", "PodResizeInProgress")
                    ),
                    key=lambda c: str(c["type"]),
                ),
            },
            "constraints": [c.to_dict() for c in self.constraints],
        }


def _container(raw: dict[str, Any], role: str) -> ContainerSpec:
    resources = raw.get("resources") or {}
    host_ports = tuple(sorted(p["hostPort"] for p in raw.get("ports") or [] if p.get("hostPort")))
    return ContainerSpec(
        name=raw["name"],
        role=role,
        requests=dict(resources.get("requests") or {}),
        limits=dict(resources.get("limits") or {}),
        host_ports=host_ports,
    )


def _volume(raw: dict[str, Any]) -> VolumeRef:
    sources = sorted(k for k in raw if k != "name")
    source = sources[0] if sources else "unknown"
    claim = None
    if source == "persistentVolumeClaim":
        claim = (raw.get("persistentVolumeClaim") or {}).get("claimName")
    return VolumeRef(name=raw["name"], source=source, claim_name=claim)


def normalize_pod(raw: dict[str, Any]) -> PodModel:
    md = raw["metadata"]
    spec = raw["spec"]
    status = raw.get("status") or {}

    owners = tuple(
        OwnerRef(
            api_version=o["apiVersion"],
            kind=o["kind"],
            name=o["name"],
            uid=o.get("uid"),
            controller=bool(o.get("controller", False)),
        )
        for o in md.get("ownerReferences") or []
    )
    controller = next((o for o in owners if o.controller), None)

    init_containers = tuple(
        _container(c, "sidecar" if c.get("restartPolicy") == "Always" else "init")
        for c in spec.get("initContainers") or []
    )
    pod_resources = spec.get("resources") or {}
    tolerations = tuple(
        Toleration(
            key=t.get("key", ""),
            operator=t.get("operator", "Equal"),
            value=t.get("value", ""),
            effect=t.get("effect", ""),
            toleration_seconds=t.get("tolerationSeconds"),
        )
        for t in spec.get("tolerations") or []
    )

    pod = PodModel(
        namespace=md["namespace"],
        name=md["name"],
        uid=md["uid"],
        node_name=spec.get("nodeName") or None,
        phase=status.get("phase"),
        deletion_timestamp=md.get("deletionTimestamp"),
        nominated_node_name=status.get("nominatedNodeName") or None,
        owner_references=owners,
        controller=controller,
        labels=dict(md.get("labels") or {}),
        annotations=dict(md.get("annotations") or {}),
        containers=tuple(_container(c, "container") for c in spec["containers"]),
        init_containers=init_containers,
        overhead=dict(spec.get("overhead") or {}),
        pod_requests=dict(pod_resources.get("requests") or {}),
        pod_limits=dict(pod_resources.get("limits") or {}),
        node_selector=dict(spec.get("nodeSelector") or {}),
        tolerations=tolerations,
        affinity=dict(spec.get("affinity") or {}),
        topology_spread_constraints=tuple(spec.get("topologySpreadConstraints") or []),
        volumes=tuple(_volume(v) for v in spec.get("volumes") or []),
        scheduler_name=spec.get("schedulerName") or DEFAULT_SCHEDULER,
        runtime_class_name=spec.get("runtimeClassName") or None,
        resource_claims=tuple(spec.get("resourceClaims") or []),
        priority=spec.get("priority"),
        priority_class_name=spec.get("priorityClassName"),
        qos_class=status.get("qosClass"),
        conditions=tuple(status.get("conditions") or []),
        container_statuses={s["name"]: s for s in status.get("containerStatuses") or [] if "name" in s},
        init_container_statuses={s["name"]: s for s in status.get("initContainerStatuses") or [] if "name" in s},
        legacy_resize_status=status.get("resize") or None,
        constraints=(),
    )
    return _with_constraints(pod)


def _with_constraints(pod: PodModel) -> PodModel:
    return replace(pod, constraints=tuple(detect_pod_constraints(pod)))


def detect_pod_constraints(pod: PodModel) -> list[ConstraintFinding]:
    """Classify the pod's own scheduling constraints, independent of any node."""
    found: list[ConstraintFinding] = []

    if pod.node_selector:
        found.append(ConstraintFinding("supported", "NODE_SELECTOR", ",".join(sorted(pod.node_selector))))
    if pod.tolerations:
        found.append(ConstraintFinding("supported", "TOLERATIONS", f"{len(pod.tolerations)} toleration(s)"))

    node_aff = pod.affinity.get("nodeAffinity") or {}
    if node_aff.get("requiredDuringSchedulingIgnoredDuringExecution"):
        found.append(ConstraintFinding("unsupported", Reason.REQUIRED_NODE_AFFINITY_NOT_MODELED, "nodeAffinity.required"))
    if node_aff.get("preferredDuringSchedulingIgnoredDuringExecution"):
        found.append(ConstraintFinding("soft", Reason.PREFERRED_NODE_AFFINITY_IGNORED, "nodeAffinity.preferred"))
    for field, required_code in (
        ("podAffinity", Reason.REQUIRED_POD_AFFINITY_NOT_MODELED),
        ("podAntiAffinity", Reason.REQUIRED_POD_ANTI_AFFINITY_NOT_MODELED),
    ):
        block = pod.affinity.get(field) or {}
        if block.get("requiredDuringSchedulingIgnoredDuringExecution"):
            found.append(ConstraintFinding("unsupported", required_code, f"{field}.required"))
        if block.get("preferredDuringSchedulingIgnoredDuringExecution"):
            found.append(ConstraintFinding("soft", Reason.PREFERRED_POD_AFFINITY_IGNORED, f"{field}.preferred"))

    for i, tsc in enumerate(pod.topology_spread_constraints):
        # whenUnsatisfiable is required by the API; treat anything but ScheduleAnyway as hard.
        if tsc.get("whenUnsatisfiable") == "ScheduleAnyway":
            found.append(ConstraintFinding("soft", Reason.SOFT_TOPOLOGY_SPREAD_IGNORED, f"topologySpreadConstraints[{i}]"))
        else:
            found.append(
                ConstraintFinding(
                    "unsupported",
                    Reason.TOPOLOGY_SPREAD_NOT_MODELED,
                    f"topologySpreadConstraints[{i}] topologyKey={tsc.get('topologyKey')}",
                )
            )

    for vol in pod.volumes:
        if vol.source not in _TOPOLOGY_NEUTRAL_VOLUMES:
            found.append(
                ConstraintFinding(
                    "unsupported",
                    Reason.PERSISTENT_VOLUME_TOPOLOGY_NOT_MODELED,
                    f"volume {vol.name} ({vol.source}{' ' + vol.claim_name if vol.claim_name else ''})",
                )
            )

    resource_names: set[str] = set()
    for c in pod.containers + pod.init_containers:
        resource_names.update(c.requests)
        resource_names.update(c.limits)
    resource_names.update(pod.pod_requests, pod.pod_limits, pod.overhead)
    for name in sorted(resource_names - set(MODELED_RESOURCES)):
        if name == "ephemeral-storage" or name.startswith("hugepages-"):
            found.append(ConstraintFinding("unsupported", Reason.UNMODELED_RESOURCE_REQUEST, name))
        else:
            found.append(ConstraintFinding("unsupported", Reason.EXTENDED_RESOURCE_NOT_MODELED, name))

    if pod.resource_claims:
        found.append(
            ConstraintFinding(
                "unsupported", Reason.DYNAMIC_RESOURCE_CLAIM_NOT_MODELED, f"{len(pod.resource_claims)} resource claim(s)"
            )
        )
    host_ports = sorted({p for c in pod.containers + pod.init_containers for p in c.host_ports})
    if host_ports:
        found.append(
            ConstraintFinding("unsupported", Reason.HOST_PORT_NOT_MODELED, "hostPort " + ",".join(map(str, host_ports)))
        )
    if pod.scheduler_name != DEFAULT_SCHEDULER:
        found.append(ConstraintFinding("unsupported", Reason.CUSTOM_SCHEDULER, pod.scheduler_name))
    if pod.runtime_class_name:
        found.append(ConstraintFinding("unsupported", Reason.RUNTIME_CLASS_NOT_MODELED, pod.runtime_class_name))

    return sorted(found, key=lambda f: (f.kind, str(f.code), f.detail))
