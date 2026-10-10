"""Snapshot loading and integrity validation (schema version 1.0)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from qplace.diagnostics import Diagnostic, Reason, Severity, SnapshotValidationError

SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
_VERSION_RE = re.compile(r"^v?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)")


@cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a bundled JSON schema, e.g. ``snapshot-1.0.schema.json``."""
    text = resources.files("qplace.schemas").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


@dataclass(frozen=True)
class InventoryCompleteness:
    nodes: str
    pods: str
    pod_disruption_budgets: str
    notes: str | None

    @property
    def nodes_complete(self) -> bool:
        return self.nodes == "complete"

    @property
    def pods_complete(self) -> bool:
        return self.pods == "complete"

    @property
    def pdbs_complete(self) -> bool:
        return self.pod_disruption_budgets == "complete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "pods": self.pods,
            "pod_disruption_budgets": self.pod_disruption_budgets,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class SnapshotProvenance:
    schema_version: str
    snapshot_id: str
    captured_at: str
    source_type: str
    kubernetes_version: str | None
    description: str | None
    source_file: str | None
    source_sha256: str | None

    @property
    def kubernetes_minor(self) -> tuple[int, int] | None:
        if self.kubernetes_version is None:
            return None
        match = _VERSION_RE.match(self.kubernetes_version)
        if not match:
            return None
        return int(match.group("major")), int(match.group("minor"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "source_type": self.source_type,
            "kubernetes_version": self.kubernetes_version,
            "description": self.description,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class Snapshot:
    """A validated snapshot. Raw Kubernetes objects are deep copies of the input."""

    provenance: SnapshotProvenance
    inventory: InventoryCompleteness
    target_node: str
    nodes: tuple[dict[str, Any], ...]
    pods: tuple[dict[str, Any], ...]
    pod_disruption_budgets: tuple[dict[str, Any], ...]
    diagnostics: tuple[Diagnostic, ...]


def load_snapshot(path: str | Path) -> Snapshot:
    """Read, validate and return a snapshot file. Raises SnapshotValidationError."""
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SnapshotValidationError(f"cannot read snapshot {path}: {exc}") from exc
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError(f"snapshot {path.name} is not valid UTF-8 JSON: {exc}") from exc
    return load_snapshot_data(data, source_file=path.name, source_sha256=hashlib.sha256(raw_bytes).hexdigest())


def load_snapshot_data(
    data: Any, *, source_file: str | None = None, source_sha256: str | None = None
) -> Snapshot:
    """Validate an already-parsed snapshot document."""
    if not isinstance(data, dict):
        raise SnapshotValidationError("snapshot root must be a JSON object")
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SnapshotValidationError(
            f"unsupported snapshot schema_version {version!r}; supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    validator = jsonschema.Draft202012Validator(load_schema(f"snapshot-{version}.schema.json"))
    schema_errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if schema_errors:
        problems = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in schema_errors]
        raise SnapshotValidationError("snapshot failed schema validation", problems)

    data = copy.deepcopy(data)
    meta = data["snapshot"]
    inv = meta["inventory"]
    inventory = InventoryCompleteness(
        nodes=inv["nodes"],
        pods=inv["pods"],
        pod_disruption_budgets=inv["pod_disruption_budgets"],
        notes=inv.get("notes"),
    )
    provenance = SnapshotProvenance(
        schema_version=version,
        snapshot_id=meta["id"],
        captured_at=meta["captured_at"],
        source_type=meta["source_type"],
        kubernetes_version=meta["kubernetes_version"],
        description=meta.get("description"),
        source_file=source_file,
        source_sha256=source_sha256,
    )
    diagnostics = _check_consistency(data, inventory)
    return Snapshot(
        provenance=provenance,
        inventory=inventory,
        target_node=data["target_node"],
        nodes=tuple(data["nodes"]),
        pods=tuple(data["pods"]),
        pod_disruption_budgets=tuple(data["pod_disruption_budgets"]),
        diagnostics=tuple(diagnostics),
    )


def _check_consistency(data: dict[str, Any], inventory: InventoryCompleteness) -> list[Diagnostic]:
    problems: list[str] = []
    diagnostics: list[Diagnostic] = []

    try:
        datetime.fromisoformat(data["snapshot"]["captured_at"].replace("Z", "+00:00"))
    except ValueError:
        problems.append(f"snapshot.captured_at is not a valid timestamp: {data['snapshot']['captured_at']!r}")

    node_names: set[str] = set()
    node_uids: set[str] = set()
    for node in data["nodes"]:
        name = node["metadata"]["name"]
        if name in node_names:
            problems.append(f"duplicate node name {name!r}")
        node_names.add(name)
        uid = node["metadata"].get("uid")
        if uid:
            if uid in node_uids:
                problems.append(f"duplicate node uid {uid!r}")
            node_uids.add(uid)

    if data["target_node"] not in node_names:
        problems.append(f"target_node {data['target_node']!r} is not present in nodes")

    pod_keys: set[str] = set()
    pod_uids: set[str] = set()
    for pod in data["pods"]:
        md = pod["metadata"]
        key = f"{md['namespace']}/{md['name']}"
        if key in pod_keys:
            problems.append(f"duplicate pod {key!r}")
        pod_keys.add(key)
        if md["uid"] in pod_uids:
            problems.append(f"duplicate pod uid {md['uid']!r}")
        pod_uids.add(md["uid"])
        bound = pod["spec"].get("nodeName")
        if bound and bound not in node_names:
            if inventory.nodes_complete:
                problems.append(f"pod {key!r} is bound to node {bound!r} which is absent from a complete node inventory")
            else:
                diagnostics.append(
                    Diagnostic(
                        Reason.POD_BOUND_TO_UNKNOWN_NODE,
                        Severity.WARNING,
                        f"pod/{key}",
                        f"pod is bound to node {bound!r}, which is not in the partial node inventory",
                        {"node": bound},
                    )
                )
        names = [c["name"] for c in pod["spec"]["containers"]] + [
            c["name"] for c in pod["spec"].get("initContainers", [])
        ]
        if len(names) != len(set(names)):
            problems.append(f"pod {key!r} has duplicate container names")

    pdb_keys: set[str] = set()
    for pdb in data["pod_disruption_budgets"]:
        key = f"{pdb['metadata']['namespace']}/{pdb['metadata']['name']}"
        if key in pdb_keys:
            problems.append(f"duplicate PodDisruptionBudget {key!r}")
        pdb_keys.add(key)

    if inventory.pod_disruption_budgets == "not_collected" and data["pod_disruption_budgets"]:
        problems.append("pod_disruption_budgets inventory is 'not_collected' but PDB objects are present")

    if problems:
        raise SnapshotValidationError("snapshot is internally inconsistent", problems)
    return diagnostics
