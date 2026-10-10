"""Run both solvers, verify each independently, and derive the overall assessment.

The optimization *outcome* (COMPLETE / PARTIAL / NONE / INDETERMINATE) is kept
separate from the operational *assessment* (MODELED_FEASIBLE / MODELED_BLOCKED /
REVIEW_REQUIRED). Neither one is a drain approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from qplace.model import ClusterModel
from qplace.placement_exact import ExactConfig, solve_exact
from qplace.placement_greedy import solve_greedy
from qplace.placement_problem import PlacementProblem, build_placement_problem
from qplace.placement_result import SolverResult, Termination
from qplace.placement_verify import VerificationResult, verify_placement


class Outcome(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    INDETERMINATE = "INDETERMINATE"


class Assessment(StrEnum):
    MODELED_FEASIBLE = "MODELED_FEASIBLE"
    MODELED_BLOCKED = "MODELED_BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Finding(StrEnum):
    EXACT_SEARCH_INCONCLUSIVE = "EXACT_SEARCH_INCONCLUSIVE"
    EXACT_VERIFICATION_FAILED = "EXACT_VERIFICATION_FAILED"
    COMPLETE_ASSIGNMENT_FOUND = "COMPLETE_ASSIGNMENT_FOUND"
    COMPLETE_ASSIGNMENT_IMPOSSIBLE_IN_MODEL = "COMPLETE_ASSIGNMENT_IMPOSSIBLE_IN_MODEL"
    KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES = "KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES"
    UNMODELED_CANDIDATES_UNPLACED = "UNMODELED_CANDIDATES_UNPLACED"
    CONDITIONAL_INPUTS = "CONDITIONAL_INPUTS"
    GREEDY_MISSED_FEASIBLE_ASSIGNMENT = "GREEDY_MISSED_FEASIBLE_ASSIGNMENT"
    GREEDY_SUBOPTIMAL = "GREEDY_SUBOPTIMAL"
    COMPLETE_ASSIGNMENT_FOUND_BY_GREEDY = "COMPLETE_ASSIGNMENT_FOUND_BY_GREEDY"
    POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE = "POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE"


DISCLAIMER = (
    "Requested-resource model only. MODELED_FEASIBLE is not SAFE_TO_DRAIN: it does not predict the "
    "Kubernetes scheduler's choices, does not simulate controller reconciliation, and does not "
    "establish that evictions will be permitted by PodDisruptionBudgets."
)


@dataclass(frozen=True)
class PlacementEvaluation:
    result: SolverResult
    verification: VerificationResult
    outcome: Outcome


@dataclass(frozen=True)
class PlacementReport:
    problem: PlacementProblem
    greedy: PlacementEvaluation
    exact: PlacementEvaluation
    assessment: Assessment
    findings: tuple[Finding, ...]
    greedy_gap: int | None  # exact assigned - greedy assigned, only when exact is proven optimal
    supporting: str | None  # "exact" | "greedy": the verified evaluation the assessment rests on

    @property
    def verified_assignments(self):  # noqa: ANN201
        ev = {"exact": self.exact, "greedy": self.greedy}.get(self.supporting or "")
        return ev.result.assignments if ev else ()


def evaluate(model: ClusterModel, problem: PlacementProblem, result: SolverResult) -> PlacementEvaluation:
    """Verify a solver result against M1 and derive its outcome."""
    verification = verify_placement(model, ((a.pod, a.node) for a in result.assignments))
    total = len(problem.candidates)
    placed = len(result.assignments)
    interrupted = result.termination in (Termination.STATE_LIMIT_REACHED, Termination.DEADLINE_EXCEEDED,
                                         Termination.SIZE_LIMIT_EXCEEDED)
    if not verification.valid or interrupted:
        outcome = Outcome.INDETERMINATE
    elif placed == total:
        outcome = Outcome.COMPLETE
    elif placed == 0:
        outcome = Outcome.NONE
    else:
        outcome = Outcome.PARTIAL
    return PlacementEvaluation(result, verification, outcome)


def assess_placement(model: ClusterModel, exact_config: ExactConfig = ExactConfig()) -> PlacementReport:
    problem = build_placement_problem(model)
    greedy = evaluate(model, problem, solve_greedy(problem))
    exact = evaluate(model, problem, solve_exact(problem, exact_config))
    findings: list[Finding] = []
    conditional = problem.is_conditional
    if conditional:
        findings.append(Finding.CONDITIONAL_INPUTS)

    proven = exact.result.optimality_proven and exact.verification.valid
    supporting: str | None = "exact" if exact.outcome is not Outcome.INDETERMINATE else None
    if not exact.verification.valid:
        findings.append(Finding.EXACT_VERIFICATION_FAILED)
    if exact.outcome is Outcome.INDETERMINATE:
        findings.append(Finding.EXACT_SEARCH_INCONCLUSIVE)
        if greedy.outcome is Outcome.COMPLETE:
            # A verified complete assignment proves feasibility on its own; only optimality is unclaimed.
            supporting = "greedy"
            findings.append(Finding.COMPLETE_ASSIGNMENT_FOUND_BY_GREEDY)
            assessment = Assessment.REVIEW_REQUIRED if conditional else Assessment.MODELED_FEASIBLE
        else:
            assessment = Assessment.REVIEW_REQUIRED
    elif exact.outcome is Outcome.COMPLETE:
        findings.append(Finding.COMPLETE_ASSIGNMENT_FOUND)
        assessment = Assessment.REVIEW_REQUIRED if conditional else Assessment.MODELED_FEASIBLE
    else:
        placed = set(exact.result.assignment_map())
        if any(c.pod not in placed for c in problem.usable_candidates):
            findings.append(Finding.KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES)
        if any(not c.usable for c in problem.candidates):
            findings.append(Finding.UNMODELED_CANDIDATES_UNPLACED)
        if problem.policy_excluded_pairs:
            findings.append(Finding.POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE)
        if proven and not conditional and not problem.policy_excluded_pairs:
            findings.append(Finding.COMPLETE_ASSIGNMENT_IMPOSSIBLE_IN_MODEL)
            assessment = Assessment.MODELED_BLOCKED
        else:
            assessment = Assessment.REVIEW_REQUIRED

    gap: int | None = None
    if proven and greedy.verification.valid:
        gap = len(exact.result.assignments) - len(greedy.result.assignments)
        if gap > 0:
            findings.append(Finding.GREEDY_SUBOPTIMAL)
            if exact.outcome is Outcome.COMPLETE:
                findings.append(Finding.GREEDY_MISSED_FEASIBLE_ASSIGNMENT)

    return PlacementReport(problem, greedy, exact, assessment, tuple(sorted(set(findings))), gap, supporting)
