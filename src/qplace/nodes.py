"""Node normalization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qplace.diagnostics import Diagnostic, QuantityError, Reason, Severity
from qplace.quantity import parse_count, parse_cpu_millicores, parse_memory_bytes


@dataclass(frozen=True)
class Taint:
    key: str
    value: str
    effect: str

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "effect": self.effect}


@dataclass(frozen=True)
class NodeResources:
    """Parsed resource list. ``None`` means absent or unparseable (see diagnostics)."""

    cpu_millicores: int | None
    memory_bytes: int | None
    pods: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"cpu_millicores": self.cpu_millicores, "memory_bytes": self.memory_bytes, "pods": self.pods}


@dataclass(frozen=True)
class NodeModel:
    name: str
    uid: str | None
    labels: dict[str, str]
    annotations: dict[str, str]
    taints: tuple[Taint, ...]
    capacity: NodeResources
    allocatable: NodeResources
    other_allocatable: dict[str, str]  # extended/unmodeled allocatable resources, kept verbatim
    ready: str | None  # "True" | "False" | "Unknown" | None when the condition is absent
    unschedulable: bool
    kubelet_version: str | None
    diagnostics: tuple[Diagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "uid": self.uid,
            "labels": dict(sorted(self.labels.items())),
            "annotations": dict(sorted(self.annotations.items())),
            "taints": [t.to_dict() for t in self.taints],
            "capacity": self.capacity.to_dict(),
            "allocatable": self.allocatable.to_dict(),
            "other_allocatable": dict(sorted(self.other_allocatable.items())),
            "ready": self.ready,
            "unschedulable": self.unschedulable,
            "kubelet_version": self.kubelet_version,
        }


def _parse_resource_list(
    resource_list: dict[str, Any] | None, subject: str, field: str, diagnostics: list[Diagnostic]
) -> NodeResources:
    resource_list = resource_list or {}
    parsers: dict[str, Callable[[object], int]] = {
        "cpu": parse_cpu_millicores,
        "memory": parse_memory_bytes,
        "pods": parse_count,
    }
    values: dict[str, int | None] = {}
    for name, parser in parsers.items():
        if name not in resource_list:
            values[name] = None
            continue
        try:
            values[name] = parser(resource_list[name])
        except QuantityError as exc:
            values[name] = None
            diagnostics.append(
                Diagnostic(exc.reason, Severity.ERROR, subject, f"{field}.{name}: {exc}", {"raw": str(exc.raw)})
            )
    return NodeResources(values["cpu"], values["memory"], values["pods"])


def normalize_node(raw: dict[str, Any]) -> NodeModel:
    md = raw["metadata"]
    spec = raw.get("spec") or {}
    status = raw.get("status") or {}
    subject = f"node/{md['name']}"
    diagnostics: list[Diagnostic] = []

    capacity = _parse_resource_list(status.get("capacity"), subject, "capacity", diagnostics)
    allocatable = _parse_resource_list(status.get("allocatable"), subject, "allocatable", diagnostics)
    for name, value in (
        ("cpu", allocatable.cpu_millicores),
        ("memory", allocatable.memory_bytes),
        ("pods", allocatable.pods),
    ):
        if value is None:
            diagnostics.append(
                Diagnostic(
                    Reason.ALLOCATABLE_MISSING,
                    Severity.WARNING,
                    subject,
                    f"allocatable {name} is missing or invalid; headroom for this resource is unknown",
                    {"resource": name},
                )
            )

    ready: str | None = None
    for cond in status.get("conditions") or []:
        if cond.get("type") == "Ready":
            ready = cond.get("status")
    taints = tuple(
        sorted(
            (Taint(t["key"], t.get("value", ""), t["effect"]) for t in spec.get("taints") or []),
            key=lambda t: (t.key, t.value, t.effect),
        )
    )
    return NodeModel(
        name=md["name"],
        uid=md.get("uid"),
        labels=dict(md.get("labels") or {}),
        annotations=dict(md.get("annotations") or {}),
        taints=taints,
        capacity=capacity,
        allocatable=allocatable,
        other_allocatable={
            k: str(v) for k, v in (status.get("allocatable") or {}).items() if k not in ("cpu", "memory", "pods")
        },
        ready=ready,
        unschedulable=bool(spec.get("unschedulable", False)),
        kubelet_version=(status.get("nodeInfo") or {}).get("kubeletVersion"),
        diagnostics=tuple(diagnostics),
    )
