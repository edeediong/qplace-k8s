"""Exact branch-and-bound placement for small problems.

Explores joint assignments depth-first. Each usable candidate (in
``placement_order``) is either assigned to one allowed destination with enough
remaining capacity or left unplaced. The lexicographic objective is
``Objective.better_than``: most pods placed, then leximin free-capacity
balance, then the canonical tie-break vector.

Pruning is exact (never discards an optimum):
- count bound: placed + remaining candidates that still fit *somewhere on their
  own* must reach the incumbent's count;
- balance bound: placing more pods can only lower every destination's free
  fraction, so when the count bound merely ties the incumbent and the current
  balance vector is already lexicographically worse, no completion can win.

The search is bounded by size limits, a state budget and an optional deadline.
If any bound stops it, the result is INDETERMINATE and optimality is not claimed.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from qplace.placement_problem import (
    Code,
    Objective,
    PlacementProblem,
    balance_vector,
    evaluate_objective,
    placement_order,
)
from qplace.placement_result import Assignment, SolverResult, SolverStats, Termination, UnplacedPod

SOLVER_ID = "exact-bnb-v1"


@dataclass(frozen=True)
class ExactConfig:
    max_candidates: int = 8
    max_destinations: int = 6
    max_states: int = 2_000_000
    deadline_seconds: float | None = None  # wall-clock limit; None keeps runs deterministic


class _Stop(Exception):
    def __init__(self, reason: Termination) -> None:
        self.reason = reason


def solve_exact(problem: PlacementProblem, config: ExactConfig = ExactConfig()) -> SolverResult:
    start = time.perf_counter()
    order = placement_order(problem)
    dests = problem.usable_destinations
    excluded = [UnplacedPod(c.pod, c.exclusion_reasons, ()) for c in problem.candidates if not c.usable]

    def result(assignment: dict[str, str], proven: bool, termination: Termination,
               states: int, pruned: int) -> SolverResult:
        unplaced = list(excluded) + [UnplacedPod(c.pod, (Code.NOT_SELECTED,), ())
                                     for c in order if c.pod not in assignment]
        return SolverResult(
            solver=SOLVER_ID,
            config=asdict(config),
            assignments=tuple(Assignment(p, n) for p, n in sorted(assignment.items())),
            unplaced=tuple(sorted(unplaced, key=lambda u: u.pod)),
            objective=evaluate_objective(problem, assignment),
            optimality_proven=proven,
            termination=termination,
            stats=SolverStats(states, pruned, time.perf_counter() - start),
        )

    if len(order) > config.max_candidates or len(dests) > config.max_destinations:
        return result({}, False, Termination.SIZE_LIMIT_EXCEEDED, 0, 0)

    remaining = {d.node: [d.cpu_millicores, d.memory_bytes, d.pod_slots] for d in dests}
    used = {d.node: [0, 0, 0] for d in dests}
    assignment: dict[str, str] = {}
    best: list[Objective | None] = [None]
    best_assignment: list[dict[str, str]] = [{}]
    counters = {"states": 0, "pruned": 0}

    def fits(c, node: str) -> bool:  # noqa: ANN001
        cpu, mem, slots = remaining[node]
        return c.cpu_millicores <= cpu and c.memory_bytes <= mem and slots >= 1

    def dfs(i: int) -> None:
        counters["states"] += 1
        if counters["states"] > config.max_states:
            raise _Stop(Termination.STATE_LIMIT_REACHED)
        if config.deadline_seconds is not None and time.perf_counter() - start > config.deadline_seconds:
            raise _Stop(Termination.DEADLINE_EXCEEDED)

        incumbent = best[0]
        if incumbent is not None:
            bound = len(assignment) + sum(1 for c in order[i:] if any(fits(c, n) for n in c.allowed_nodes))
            if bound < incumbent.assigned:
                counters["pruned"] += 1
                return
            if bound == incumbent.assigned:
                current = balance_vector(problem, {n: tuple(u) for n, u in used.items()})
                if current < incumbent.balance:
                    counters["pruned"] += 1
                    return

        if i == len(order):
            candidate = evaluate_objective(problem, assignment)
            if candidate.better_than(best[0]):
                best[0] = candidate
                best_assignment[0] = dict(assignment)
            return

        c = order[i]
        for node in c.allowed_nodes:
            if not fits(c, node):
                continue
            r, u = remaining[node], used[node]
            r[0] -= c.cpu_millicores; r[1] -= c.memory_bytes; r[2] -= 1  # noqa: E702
            u[0] += c.cpu_millicores; u[1] += c.memory_bytes; u[2] += 1  # noqa: E702
            assignment[c.pod] = node
            dfs(i + 1)
            del assignment[c.pod]
            r[0] += c.cpu_millicores; r[1] += c.memory_bytes; r[2] += 1  # noqa: E702
            u[0] -= c.cpu_millicores; u[1] -= c.memory_bytes; u[2] -= 1  # noqa: E702
        dfs(i + 1)  # leave c unplaced

    try:
        dfs(0)
    except _Stop as stop:
        return result(best_assignment[0], False, stop.reason, counters["states"], counters["pruned"])
    return result(best_assignment[0], True, Termination.SEARCH_COMPLETED, counters["states"], counters["pruned"])
