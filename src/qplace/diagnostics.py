"""Structured diagnostics, reason codes and exceptions shared by all M1 modules."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    UNSUPPORTED = "unsupported"
    INFO = "info"


class Reason(StrEnum):
    """Machine-readable reason codes. Values are part of the artifact contract."""

    # Quantity / accounting
    INVALID_QUANTITY = "INVALID_QUANTITY"
    NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"
    REQUEST_DEFAULTED_FROM_LIMIT = "REQUEST_DEFAULTED_FROM_LIMIT"
    POD_LEVEL_REQUESTS_APPLIED = "POD_LEVEL_REQUESTS_APPLIED"
    POD_LEVEL_FEATURE_STATE_UNCERTAIN = "POD_LEVEL_FEATURE_STATE_UNCERTAIN"
    POD_LEVEL_BELOW_CONTAINER_AGGREGATE = "POD_LEVEL_BELOW_CONTAINER_AGGREGATE"
    POD_LEVEL_RESIZE_NOT_MODELED = "POD_LEVEL_RESIZE_NOT_MODELED"
    SIDECAR_FEATURE_STATE_UNCERTAIN = "SIDECAR_FEATURE_STATE_UNCERTAIN"
    RESIZE_STATUS_APPLIED = "RESIZE_STATUS_APPLIED"
    RESIZE_STATUS_INCOMPLETE = "RESIZE_STATUS_INCOMPLETE"
    RESIZE_FEATURE_STATE_UNCERTAIN = "RESIZE_FEATURE_STATE_UNCERTAIN"
    UNMODELED_RESOURCE_REQUEST = "UNMODELED_RESOURCE_REQUEST"
    POD_REQUEST_UNKNOWN = "POD_REQUEST_UNKNOWN"
    POD_REQUEST_REVIEW_REQUIRED = "POD_REQUEST_REVIEW_REQUIRED"
    ALLOCATABLE_MISSING = "ALLOCATABLE_MISSING"
    OVERCOMMITTED = "OVERCOMMITTED"
    INVENTORY_INCOMPLETE = "INVENTORY_INCOMPLETE"
    TERMINAL_POD_NOT_RESERVED = "TERMINAL_POD_NOT_RESERVED"
    UNKNOWN_PHASE_RESERVED = "UNKNOWN_PHASE_RESERVED"
    UNBOUND_PENDING_PODS_PRESENT = "UNBOUND_PENDING_PODS_PRESENT"
    NOMINATED_POD_NOT_RESERVED = "NOMINATED_POD_NOT_RESERVED"

    # Snapshot integrity
    POD_BOUND_TO_UNKNOWN_NODE = "POD_BOUND_TO_UNKNOWN_NODE"

    # Eligibility: hard blockers
    DRAIN_TARGET = "DRAIN_TARGET"
    NODE_NOT_READY = "NODE_NOT_READY"
    NODE_UNSCHEDULABLE = "NODE_UNSCHEDULABLE"
    NODE_SELECTOR_MISMATCH = "NODE_SELECTOR_MISMATCH"
    UNTOLERATED_TAINT = "UNTOLERATED_TAINT"

    # Eligibility: soft / informational
    UNTOLERATED_PREFER_NO_SCHEDULE_TAINT = "UNTOLERATED_PREFER_NO_SCHEDULE_TAINT"
    PREFERRED_NODE_AFFINITY_IGNORED = "PREFERRED_NODE_AFFINITY_IGNORED"
    PREFERRED_POD_AFFINITY_IGNORED = "PREFERRED_POD_AFFINITY_IGNORED"
    SOFT_TOPOLOGY_SPREAD_IGNORED = "SOFT_TOPOLOGY_SPREAD_IGNORED"

    # Eligibility: unknown / unsupported
    NODE_READY_CONDITION_MISSING = "NODE_READY_CONDITION_MISSING"
    UNSUPPORTED_TOLERATION_OPERATOR = "UNSUPPORTED_TOLERATION_OPERATOR"
    UNSUPPORTED_TAINT_EFFECT = "UNSUPPORTED_TAINT_EFFECT"
    REQUIRED_NODE_AFFINITY_NOT_MODELED = "REQUIRED_NODE_AFFINITY_NOT_MODELED"
    REQUIRED_POD_AFFINITY_NOT_MODELED = "REQUIRED_POD_AFFINITY_NOT_MODELED"
    REQUIRED_POD_ANTI_AFFINITY_NOT_MODELED = "REQUIRED_POD_ANTI_AFFINITY_NOT_MODELED"
    TOPOLOGY_SPREAD_NOT_MODELED = "TOPOLOGY_SPREAD_NOT_MODELED"
    PERSISTENT_VOLUME_TOPOLOGY_NOT_MODELED = "PERSISTENT_VOLUME_TOPOLOGY_NOT_MODELED"
    EXTENDED_RESOURCE_NOT_MODELED = "EXTENDED_RESOURCE_NOT_MODELED"
    DYNAMIC_RESOURCE_CLAIM_NOT_MODELED = "DYNAMIC_RESOURCE_CLAIM_NOT_MODELED"
    HOST_PORT_NOT_MODELED = "HOST_PORT_NOT_MODELED"
    CUSTOM_SCHEDULER = "CUSTOM_SCHEDULER"
    RUNTIME_CLASS_NOT_MODELED = "RUNTIME_CLASS_NOT_MODELED"
    POD_REQUEST_UNKNOWN_FOR_FIT = "POD_REQUEST_UNKNOWN_FOR_FIT"
    NODE_HEADROOM_UNKNOWN = "NODE_HEADROOM_UNKNOWN"
    INSUFFICIENT_CPU = "INSUFFICIENT_CPU"
    INSUFFICIENT_MEMORY = "INSUFFICIENT_MEMORY"
    INSUFFICIENT_POD_SLOTS = "INSUFFICIENT_POD_SLOTS"

    # Drain classification
    TERMINAL_POD = "TERMINAL_POD"
    ALREADY_TERMINATING = "ALREADY_TERMINATING"
    MIRROR_POD = "MIRROR_POD"
    DAEMONSET_POD = "DAEMONSET_POD"
    UNMANAGED_POD = "UNMANAGED_POD"
    UNKNOWN_CONTROLLER = "UNKNOWN_CONTROLLER"
    JOB_REPLACEMENT_CONDITIONAL = "JOB_REPLACEMENT_CONDITIONAL"
    REPLACEMENT_TEMPLATE_UNKNOWN = "REPLACEMENT_TEMPLATE_UNKNOWN"
    LOCAL_EMPTYDIR_DATA = "LOCAL_EMPTYDIR_DATA"
    HOST_PATH_VOLUME = "HOST_PATH_VOLUME"
    STATEFUL_IDENTITY = "STATEFUL_IDENTITY"
    PDB_EVICTION_BLOCKED_AT_SNAPSHOT = "PDB_EVICTION_BLOCKED_AT_SNAPSHOT"
    PDB_MAY_BLOCK_SEQUENTIAL_EVICTION = "PDB_MAY_BLOCK_SEQUENTIAL_EVICTION"
    PDB_ADVISORY_ONLY = "PDB_ADVISORY_ONLY"
    PDB_STATUS_MISSING = "PDB_STATUS_MISSING"
    MULTIPLE_PDBS_MATCH = "MULTIPLE_PDBS_MATCH"
    PDB_INVENTORY_INCOMPLETE = "PDB_INVENTORY_INCOMPLETE"
    UNSUPPORTED_SELECTOR_OPERATOR = "UNSUPPORTED_SELECTOR_OPERATOR"


@dataclass(frozen=True)
class Diagnostic:
    code: Reason
    severity: Severity
    subject: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "severity": str(self.severity),
            "subject": self.subject,
            "message": self.message,
            "details": self.details,
        }

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.subject, str(self.code), str(self.severity), self.message)


def dedupe_diagnostics(diagnostics: Iterable[Diagnostic]) -> tuple[Diagnostic, ...]:
    """Remove exact duplicates and return a deterministically ordered tuple."""
    unique: dict[str, Diagnostic] = {}
    for d in diagnostics:
        unique.setdefault(json.dumps(d.to_dict(), sort_keys=True, default=str), d)
    return tuple(sorted(unique.values(), key=lambda d: (d.sort_key(), json.dumps(d.details, sort_keys=True, default=str))))


class QPlaceError(Exception):
    """Base class for all structured QPlace modeling errors."""


class SnapshotValidationError(QPlaceError):
    """The snapshot is malformed, inconsistent or uses an unsupported schema version."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [])
        detail = "" if not self.problems else ":\n  - " + "\n  - ".join(self.problems)
        super().__init__(message + detail)


class QuantityError(QPlaceError, ValueError):
    """A Kubernetes resource quantity is invalid or negative."""

    def __init__(self, raw: object, reason: Reason, message: str) -> None:
        self.raw = raw
        self.reason = reason
        super().__init__(message)
