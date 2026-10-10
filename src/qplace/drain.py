"""Drain-target pod classification and advisory PodDisruptionBudget relevance."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qplace.diagnostics import Diagnostic, Reason, Severity, dedupe_diagnostics
from qplace.eligibility import Finding
from qplace.pods import OwnerRef, PodModel
from qplace.snapshot import InventoryCompleteness


class DrainClass(StrEnum):
    CONTROLLER_MANAGED = "CONTROLLER_MANAGED"
    UNKNOWN_CONTROLLER = "UNKNOWN_CONTROLLER"
    DAEMONSET = "DAEMONSET"
    MIRROR = "MIRROR"
    UNMANAGED = "UNMANAGED"
    TERMINAL = "TERMINAL"
    TERMINATING = "TERMINATING"


class Replacement(StrEnum):
    ASSUMED = "ASSUMED"  # controller is known to recreate the pod elsewhere
    CONDITIONAL = "CONDITIONAL"  # controller may recreate it depending on its own policy
    NOT_EXPECTED = "NOT_EXPECTED"  # no relocated replacement is expected
    UNKNOWN = "UNKNOWN"


# (API group, kind) -> replacement assumption. Matching uses the owner
# reference's apiVersion group and kind, never the pod name.
_KNOWN_CONTROLLERS: dict[tuple[str, str], Replacement] = {
    ("apps", "ReplicaSet"): Replacement.ASSUMED,
    ("apps", "StatefulSet"): Replacement.ASSUMED,
    ("", "ReplicationController"): Replacement.ASSUMED,
    ("batch", "Job"): Replacement.CONDITIONAL,
    ("apps", "DaemonSet"): Replacement.NOT_EXPECTED,
}


def _group(api_version: str) -> str:
    return api_version.split("/", 1)[0] if "/" in api_version else ""


def known_controller(owner: OwnerRef | None) -> tuple[str, str] | None:
    if owner is None:
        return None
    key = (_group(owner.api_version), owner.kind)
    return key if key in _KNOWN_CONTROLLERS else None


@dataclass(frozen=True)
class DrainPodRecord:
    pod: str
    drain_class: DrainClass
    controller_kind: str | None
    controller_api_version: str | None
    controller_name: str | None
    controller_known: bool
    replacement: Replacement
    placement_candidate: bool
    kubectl_drain_flags: tuple[str, ...]
    findings: tuple[Finding, ...]
    matching_pdbs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod": self.pod,
            "drain_class": str(self.drain_class),
            "controller": {
                "kind": self.controller_kind,
                "api_version": self.controller_api_version,
                "name": self.controller_name,
                "known": self.controller_known,
            },
            "replacement": str(self.replacement),
            "placement_candidate": self.placement_candidate,
            "kubectl_drain_flags": list(self.kubectl_drain_flags),
            "findings": [f.to_dict() for f in self.findings],
            "matching_pdbs": list(self.matching_pdbs),
        }


@dataclass(frozen=True)
class PdbAssessment:
    pdb: str
    selector_summary: str
    disruptions_allowed: int | None
    current_healthy: int | None
    desired_healthy: int | None
    expected_pods: int | None
    unhealthy_pod_eviction_policy: str | None
    matching_target_pods: tuple[str, ...]
    assessment: Reason
    selector_uncertain: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdb": self.pdb,
            "selector": self.selector_summary,
            "disruptions_allowed": self.disruptions_allowed,
            "current_healthy": self.current_healthy,
            "desired_healthy": self.desired_healthy,
            "expected_pods": self.expected_pods,
            "unhealthy_pod_eviction_policy": self.unhealthy_pod_eviction_policy,
            "matching_target_pods": list(self.matching_target_pods),
            "assessment": str(self.assessment),
            "selector_uncertain": self.selector_uncertain,
            "advisory": True,
        }


@dataclass(frozen=True)
class DrainInventory:
    target_node: str
    pods: tuple[DrainPodRecord, ...]
    pdbs: tuple[PdbAssessment, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def placement_candidates(self) -> tuple[str, ...]:
        return tuple(r.pod for r in self.pods if r.placement_candidate)

    @property
    def kubectl_drain_flags(self) -> tuple[str, ...]:
        return tuple(sorted({f for r in self.pods for f in r.kubectl_drain_flags}))


def selector_matches(selector: dict[str, Any] | None, labels: dict[str, str]) -> bool | None:
    """metav1.LabelSelector semantics (policy/v1: empty selector matches all, null matches none).

    Returns None when an operator is not supported.
    """
    if selector is None:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    undetermined = False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr.get("key"), expr.get("operator"), expr.get("values") or []
        if op == "In":
            ok = labels.get(key) in values
        elif op == "NotIn":
            ok = key not in labels or labels[key] not in values
        elif op == "Exists":
            ok = key in labels
        elif op == "DoesNotExist":
            ok = key not in labels
        else:
            undetermined = True
            continue
        if not ok:
            return False
    return None if undetermined else True


def _selector_summary(selector: dict[str, Any] | None) -> str:
    if selector is None:
        return "<null: matches no pods>"
    parts = [f"{k}={v}" for k, v in sorted((selector.get("matchLabels") or {}).items())]
    for e in selector.get("matchExpressions") or []:
        parts.append(f"{e.get('key')} {e.get('operator')} {sorted(e.get('values') or [])}")
    return ", ".join(parts) if parts else "<empty: matches all pods in namespace>"


def _classify(pod: PodModel) -> tuple[DrainClass, Replacement, list[Finding]]:
    findings: list[Finding] = []
    known = known_controller(pod.controller)
    if pod.is_mirror:
        return DrainClass.MIRROR, Replacement.NOT_EXPECTED, [Finding(Reason.MIRROR_POD, "static pod managed by kubelet")]
    if pod.is_terminal:
        return DrainClass.TERMINAL, Replacement.NOT_EXPECTED, [Finding(Reason.TERMINAL_POD, f"phase={pod.phase}")]
    if known == ("apps", "DaemonSet"):
        # kubectl's daemonSetFilter ignores deletionTimestamp, so a terminating
        # DaemonSet pod still requires --ignore-daemonsets.
        findings.append(Finding(Reason.DAEMONSET_POD, pod.controller.name))
        if pod.is_terminating:
            findings.append(Finding(Reason.ALREADY_TERMINATING, f"deletionTimestamp={pod.deletion_timestamp}"))
        return DrainClass.DAEMONSET, Replacement.NOT_EXPECTED, findings
    if pod.is_terminating:
        return DrainClass.TERMINATING, Replacement.UNKNOWN, [
            Finding(Reason.ALREADY_TERMINATING, f"deletionTimestamp={pod.deletion_timestamp}")
        ]
    if pod.controller is None:
        return DrainClass.UNMANAGED, Replacement.NOT_EXPECTED, [
            Finding(Reason.UNMANAGED_POD, "no controller ownerReference; deletion would be permanent")
        ]
    if known is None:
        findings.append(
            Finding(Reason.UNKNOWN_CONTROLLER, f"{pod.controller.api_version}/{pod.controller.kind} {pod.controller.name}")
        )
        return DrainClass.UNKNOWN_CONTROLLER, Replacement.UNKNOWN, findings
    replacement = _KNOWN_CONTROLLERS[known]
    if known == ("batch", "Job"):
        findings.append(Finding(Reason.JOB_REPLACEMENT_CONDITIONAL, "depends on Job backoff/podFailurePolicy"))
    if known == ("apps", "StatefulSet"):
        findings.append(Finding(Reason.STATEFUL_IDENTITY, "replacement keeps the same identity and volume claims"))
    return DrainClass.CONTROLLER_MANAGED, replacement, findings


def classify_drain_pods(
    pods: Iterable[PodModel],
    target_node: str,
    pdbs: Iterable[dict[str, Any]] = (),
    inventory: InventoryCompleteness | None = None,
) -> DrainInventory:
    """Classify every pod bound to ``target_node`` and assess potentially relevant PDBs."""
    on_target = sorted((p for p in pods if p.node_name == target_node), key=lambda p: p.key)
    diagnostics: list[Diagnostic] = []

    # PDB matching first so each pod record can carry its budgets.
    pdb_matches: dict[str, list[str]] = {p.key: [] for p in on_target}
    pdb_rows: list[tuple[dict[str, Any], str, list[str], bool]] = []
    classified = {p.key: _classify(p) for p in on_target}
    # The eviction API skips PDB checks for Pending, terminal and terminating pods (canIgnorePDB).
    evictable = [
        p for p in on_target
        if classified[p.key][0] in (DrainClass.CONTROLLER_MANAGED, DrainClass.UNKNOWN_CONTROLLER, DrainClass.UNMANAGED)
        and p.phase != "Pending"
    ]
    for pdb in sorted(pdbs, key=lambda b: (b["metadata"]["namespace"], b["metadata"]["name"])):
        key = f"{pdb['metadata']['namespace']}/{pdb['metadata']['name']}"
        selector = (pdb.get("spec") or {}).get("selector")
        matched: list[str] = []
        uncertain = False
        for p in evictable:
            if p.namespace != pdb["metadata"]["namespace"]:
                continue
            result = selector_matches(selector, p.labels)
            if result is None:
                uncertain = True
                matched.append(p.key)  # conservative: treat as possibly covered
            elif result:
                matched.append(p.key)
        if uncertain:
            diagnostics.append(
                Diagnostic(Reason.UNSUPPORTED_SELECTOR_OPERATOR, Severity.UNSUPPORTED, f"pdb/{key}",
                           "selector uses an unsupported operator; matches are conservative", {})
            )
        for pk in matched:
            pdb_matches[pk].append(key)
        if matched:
            pdb_rows.append((pdb, key, matched, uncertain))

    assessments: list[PdbAssessment] = []
    for pdb, key, matched, uncertain in pdb_rows:
        status = pdb.get("status") or {}
        allowed = status.get("disruptionsAllowed")
        if allowed is None:
            verdict = Reason.PDB_STATUS_MISSING
        elif allowed == 0:
            verdict = Reason.PDB_EVICTION_BLOCKED_AT_SNAPSHOT
        elif len(matched) > allowed:
            verdict = Reason.PDB_MAY_BLOCK_SEQUENTIAL_EVICTION
        else:
            verdict = Reason.PDB_ADVISORY_ONLY
        assessments.append(
            PdbAssessment(
                pdb=key,
                selector_summary=_selector_summary((pdb.get("spec") or {}).get("selector")),
                disruptions_allowed=allowed,
                current_healthy=status.get("currentHealthy"),
                desired_healthy=status.get("desiredHealthy"),
                expected_pods=status.get("expectedPods"),
                unhealthy_pod_eviction_policy=(pdb.get("spec") or {}).get("unhealthyPodEvictionPolicy"),
                matching_target_pods=tuple(matched),
                assessment=verdict,
                selector_uncertain=uncertain,
            )
        )
        severity = Severity.INFO if verdict is Reason.PDB_ADVISORY_ONLY else Severity.WARNING
        diagnostics.append(
            Diagnostic(verdict, severity, f"pdb/{key}",
                       "advisory PDB assessment at snapshot time; does not establish drain safety",
                       {"disruptions_allowed": allowed, "matching_target_pods": matched})
        )

    if inventory is not None and not inventory.pdbs_complete:
        diagnostics.append(
            Diagnostic(Reason.PDB_INVENTORY_INCOMPLETE, Severity.WARNING, "cluster",
                       f"PodDisruptionBudget inventory is {inventory.pod_disruption_budgets}; "
                       "absence of a matching PDB proves nothing", {})
        )

    records: list[DrainPodRecord] = []
    for p in on_target:
        drain_class, replacement, findings = classified[p.key]
        flags: list[str] = []
        if drain_class is DrainClass.DAEMONSET:
            flags.append("--ignore-daemonsets")
        if drain_class in (DrainClass.CONTROLLER_MANAGED, DrainClass.UNKNOWN_CONTROLLER, DrainClass.UNMANAGED,
                           DrainClass.TERMINATING):
            if p.controller is None:
                flags.append("--force")
            if any(v.source == "emptyDir" for v in p.volumes):
                flags.append("--delete-emptydir-data")
                findings.append(Finding(Reason.LOCAL_EMPTYDIR_DATA, "emptyDir data is lost on eviction"))
        if drain_class is not DrainClass.TERMINAL and any(v.source == "hostPath" for v in p.volumes):
            findings.append(Finding(Reason.HOST_PATH_VOLUME, "node-local hostPath data does not move"))
        if len(pdb_matches[p.key]) > 1:
            findings.append(Finding(Reason.MULTIPLE_PDBS_MATCH, ", ".join(pdb_matches[p.key])))
            diagnostics.append(
                Diagnostic(Reason.MULTIPLE_PDBS_MATCH, Severity.WARNING, f"pod/{p.key}",
                           "the eviction API refuses to evict a pod covered by more than one PDB",
                           {"pdbs": pdb_matches[p.key]})
            )
        candidate = drain_class in (DrainClass.CONTROLLER_MANAGED, DrainClass.UNKNOWN_CONTROLLER) and replacement in (
            Replacement.ASSUMED, Replacement.CONDITIONAL, Replacement.UNKNOWN
        )
        if candidate:
            findings.append(
                Finding(Reason.REPLACEMENT_TEMPLATE_UNKNOWN,
                        "replacement modeled from the current pod spec; controller template not in snapshot")
            )
        records.append(
            DrainPodRecord(
                pod=p.key,
                drain_class=drain_class,
                controller_kind=p.controller.kind if p.controller else None,
                controller_api_version=p.controller.api_version if p.controller else None,
                controller_name=p.controller.name if p.controller else None,
                controller_known=known_controller(p.controller) is not None,
                replacement=replacement,
                placement_candidate=candidate,
                kubectl_drain_flags=tuple(sorted(flags)),
                findings=tuple(sorted(findings, key=lambda f: (str(f.code), f.detail))),
                matching_pdbs=tuple(pdb_matches[p.key]),
            )
        )

    return DrainInventory(target_node, tuple(records), tuple(assessments), dedupe_diagnostics(diagnostics))
