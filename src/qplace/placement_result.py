"""Solver-independent result objects shared by every placement solver (M2 and later M3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qplace.placement_problem import Code, Objective


class Termination(StrEnum):
    COMPLETED = "COMPLETED"  # greedy finished its single pass
    SEARCH_COMPLETED = "SEARCH_COMPLETED"  # exact search exhausted the space (optimality proven)
    STATE_LIMIT_REACHED = "STATE_LIMIT_REACHED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class Assignment:
    pod: str
    node: str

    def to_dict(self) -> dict[str, str]:
        return {"pod": self.pod, "node": self.node}


@dataclass(frozen=True)
class Rejection:
    node: str
    code: Code
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"node": self.node, "code": str(self.code), "detail": self.detail}


@dataclass(frozen=True)
class UnplacedPod:
    pod: str
    reasons: tuple[Code, ...]
    rejections: tuple[Rejection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod": self.pod,
            "reasons": [str(r) for r in self.reasons],
            "rejections": [r.to_dict() for r in self.rejections],
        }


@dataclass(frozen=True)
class SolverStats:
    states_explored: int
    branches_pruned: int
    duration_seconds: float  # observational only; never serialized into deterministic artifacts

    def deterministic_dict(self) -> dict[str, int]:
        return {"states_explored": self.states_explored, "branches_pruned": self.branches_pruned}


@dataclass(frozen=True)
class SolverResult:
    """Raw solver output. Feasibility is *not* trusted until ``verify_placement`` passes."""

    solver: str
    config: dict[str, Any]
    assignments: tuple[Assignment, ...]  # sorted by pod key
    unplaced: tuple[UnplacedPod, ...]  # sorted by pod key
    objective: Objective
    optimality_proven: bool
    termination: Termination
    stats: SolverStats

    def assignment_map(self) -> dict[str, str]:
        return {a.pod: a.node for a in self.assignments}
