"""Per (pod, destination node) eligibility against the M1-modeled node-level constraints.

Eligibility is a node-level verdict for one pod in isolation. It is *not* a
placement decision and says nothing about whether several pods fit together.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qplace.diagnostics import Reason
from qplace.nodes import NodeModel, Taint
from qplace.pods import PodModel, Toleration
from qplace.resources import NodeAccount, PodRequests

HARD_TAINT_EFFECTS = ("NoSchedule", "NoExecute")
SOFT_TAINT_EFFECTS = ("PreferNoSchedule",)
SUPPORTED_TOLERATION_OPERATORS = ("Equal", "Exists", "")
UNSCHEDULABLE_TAINT = Taint(key="node.kubernetes.io/unschedulable", value="", effect="NoSchedule")


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class CapacityFit(StrEnum):
    FITS = "FITS"
    DOES_NOT_FIT = "DOES_NOT_FIT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Finding:
    code: Reason | str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "detail": self.detail}


def _sorted(findings: list[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(set(findings), key=lambda f: (str(f.code), f.detail)))


@dataclass(frozen=True)
class EligibilityResult:
    pod: str
    node: str
    state: Eligibility
    blockers: tuple[Finding, ...]
    unknowns: tuple[Finding, ...]
    soft: tuple[Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod": self.pod,
            "node": self.node,
            "state": str(self.state),
            "blockers": [f.to_dict() for f in self.blockers],
            "unknowns": [f.to_dict() for f in self.unknowns],
            "soft": [f.to_dict() for f in self.soft],
        }


@dataclass(frozen=True)
class CapacityFitResult:
    pod: str
    node: str
    fit: CapacityFit
    reasons: tuple[Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"fit": str(self.fit), "reasons": [f.to_dict() for f in self.reasons]}


def toleration_matches(tol: Toleration, taint: Taint) -> bool | None:
    """Kubernetes ``Toleration.ToleratesTaint``. Returns None for unsupported operators."""
    if tol.effect and tol.effect != taint.effect:
        return False
    if tol.key and tol.key != taint.key:
        return False
    if tol.operator in ("Equal", ""):
        return tol.value == taint.value
    if tol.operator == "Exists":
        return True
    return None


def tolerates(pod: PodModel, taint: Taint) -> bool | None:
    """True if any toleration matches, None if only an unsupported operator might match."""
    undetermined = False
    for tol in pod.tolerations:
        result = toleration_matches(tol, taint)
        if result:
            return True
        if result is None:
            undetermined = True
    return None if undetermined else False


def _taint_label(t: Taint) -> str:
    return f"{t.key}={t.value}:{t.effect}" if t.value else f"{t.key}:{t.effect}"


def evaluate_node_eligibility(pod: PodModel, node: NodeModel, target_node: str) -> EligibilityResult:
    """Evaluate the modeled node-level constraints for placing a replacement of ``pod`` on ``node``.

    ``pod.node_name`` (the current binding) is deliberately ignored: it is not a
    constraint on a hypothetical replacement.
    """
    blockers: list[Finding] = []
    unknowns: list[Finding] = []
    soft: list[Finding] = []

    if node.name == target_node:
        blockers.append(Finding(Reason.DRAIN_TARGET, node.name))

    if node.ready is None:
        unknowns.append(Finding(Reason.NODE_READY_CONDITION_MISSING, "no Ready condition reported"))
    elif node.ready != "True":
        blockers.append(Finding(Reason.NODE_NOT_READY, f"Ready={node.ready}"))

    if node.unschedulable:
        tolerated = tolerates(pod, UNSCHEDULABLE_TAINT)
        if tolerated is None:
            unknowns.append(Finding(Reason.UNSUPPORTED_TOLERATION_OPERATOR, _taint_label(UNSCHEDULABLE_TAINT)))
        elif not tolerated:
            blockers.append(Finding(Reason.NODE_UNSCHEDULABLE, "spec.unschedulable=true"))

    for key, value in sorted(pod.node_selector.items()):
        actual = node.labels.get(key)
        if actual != value:
            blockers.append(
                Finding(Reason.NODE_SELECTOR_MISMATCH, f"{key}={value} (node has {key}={actual})" if actual is not None
                        else f"{key}={value} (label absent)")
            )

    for taint in node.taints:
        if taint.effect in HARD_TAINT_EFFECTS:
            tolerated = tolerates(pod, taint)
            if tolerated is None:
                unknowns.append(Finding(Reason.UNSUPPORTED_TOLERATION_OPERATOR, _taint_label(taint)))
            elif not tolerated:
                blockers.append(Finding(Reason.UNTOLERATED_TAINT, _taint_label(taint)))
        elif taint.effect in SOFT_TAINT_EFFECTS:
            if not tolerates(pod, taint):
                soft.append(Finding(Reason.UNTOLERATED_PREFER_NO_SCHEDULE_TAINT, _taint_label(taint)))
        else:
            unknowns.append(Finding(Reason.UNSUPPORTED_TAINT_EFFECT, _taint_label(taint)))

    for c in pod.constraints:
        if c.kind == "unsupported":
            unknowns.append(Finding(c.code, c.detail))
        elif c.kind == "soft":
            soft.append(Finding(c.code, c.detail))

    if blockers:
        state = Eligibility.INELIGIBLE
    elif unknowns:
        state = Eligibility.UNKNOWN
    else:
        state = Eligibility.ELIGIBLE
    return EligibilityResult(pod.key, node.name, state, _sorted(blockers), _sorted(unknowns), _sorted(soft))


def evaluate_capacity_fit(requests: PodRequests, account: NodeAccount) -> CapacityFitResult:
    """Would this single pod's requests fit the node's remaining requested-resource headroom?

    Independent per-pod check only. It does not account for other pods being
    placed on the same node; M2 must validate aggregate capacity.
    """
    reasons: list[Finding] = []
    outcomes: list[CapacityFit] = []
    checks = (
        ("cpu", requests.cpu_millicores, Reason.INSUFFICIENT_CPU),
        ("memory", requests.memory_bytes, Reason.INSUFFICIENT_MEMORY),
        ("pods", 1, Reason.INSUFFICIENT_POD_SLOTS),
    )
    for resource, need, short_code in checks:
        remaining = account.remaining(resource)
        upper = account.remaining_upper_bound(resource)
        if need is None:
            outcomes.append(CapacityFit.UNKNOWN)
            reasons.append(Finding(Reason.POD_REQUEST_UNKNOWN_FOR_FIT, resource))
        elif need == 0 and resource != "pods":
            # NodeResourcesFit only checks a resource the pod actually requests.
            outcomes.append(CapacityFit.FITS)
        elif remaining is not None:
            if need <= remaining:
                outcomes.append(CapacityFit.FITS)
            else:
                outcomes.append(CapacityFit.DOES_NOT_FIT)
                reasons.append(Finding(short_code, f"needs {need}, remaining {remaining}"))
        elif upper is not None and need > upper:
            # Even the most optimistic headroom is insufficient.
            outcomes.append(CapacityFit.DOES_NOT_FIT)
            reasons.append(Finding(short_code, f"needs {need}, remaining at most {upper}"))
        else:
            outcomes.append(CapacityFit.UNKNOWN)
            reasons.append(Finding(Reason.NODE_HEADROOM_UNKNOWN, resource))

    if CapacityFit.DOES_NOT_FIT in outcomes:
        fit = CapacityFit.DOES_NOT_FIT
    elif CapacityFit.UNKNOWN in outcomes:
        fit = CapacityFit.UNKNOWN
    else:
        fit = CapacityFit.FITS
    return CapacityFitResult(requests.pod_key, account.node, fit, _sorted(reasons))
