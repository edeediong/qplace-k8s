"""Deterministic first-fit-decreasing greedy placement.

Order: candidates by CPU desc, memory desc, pod key asc (``placement_order``);
destinations by node name. Each candidate goes to the first allowed destination
whose *temporary* remaining CPU, memory and pod slots can hold it. Greedy never
claims optimality, and a greedy failure proves nothing about feasibility.
"""

from __future__ import annotations

import time

from qplace.placement_problem import Code, PlacementProblem, evaluate_objective, placement_order
from qplace.placement_result import Assignment, Rejection, SolverResult, SolverStats, Termination, UnplacedPod

SOLVER_ID = "greedy-ffd-v1"


def _exclusion_rejections(problem: PlacementProblem, pod: str) -> list[Rejection]:
    c = problem.candidate(pod)
    out: list[Rejection] = []
    for d in problem.destinations:
        if d.node in c.allowed_nodes:
            continue
        if d.node in c.ineligible_nodes:
            out.append(Rejection(d.node, Code.NOT_ELIGIBLE, "M1 eligibility INELIGIBLE"))
        elif d.node in c.unknown_nodes and d.usable:
            out.append(Rejection(d.node, Code.ELIGIBILITY_UNKNOWN_PAIR, "M1 eligibility UNKNOWN"))
        else:
            out.append(Rejection(d.node, Code.DESTINATION_EXCLUDED, ",".join(map(str, d.exclusion_reasons))))
    return out


def solve_greedy(problem: PlacementProblem) -> SolverResult:
    start = time.perf_counter()
    remaining = {d.node: [d.cpu_millicores, d.memory_bytes, d.pod_slots] for d in problem.usable_destinations}
    assignment: dict[str, str] = {}
    unplaced: list[UnplacedPod] = []
    states = 0

    for c in problem.candidates:
        if not c.usable:
            unplaced.append(UnplacedPod(c.pod, c.exclusion_reasons, tuple(_exclusion_rejections(problem, c.pod))))

    for c in placement_order(problem):
        rejections = _exclusion_rejections(problem, c.pod)
        for node in c.allowed_nodes:  # already sorted by node name
            states += 1
            cpu, mem, slots = remaining[node]
            short = []
            if c.cpu_millicores > cpu:  # type: ignore[operator]
                short.append(Rejection(node, Code.INSUFFICIENT_CPU, f"needs {c.cpu_millicores}, has {cpu}"))
            if c.memory_bytes > mem:  # type: ignore[operator]
                short.append(Rejection(node, Code.INSUFFICIENT_MEMORY, f"needs {c.memory_bytes}, has {mem}"))
            if slots < 1:  # type: ignore[operator]
                short.append(Rejection(node, Code.INSUFFICIENT_POD_SLOTS, f"has {slots}"))
            if short:
                rejections.extend(short)
                continue
            remaining[node] = [cpu - c.cpu_millicores, mem - c.memory_bytes, slots - 1]  # type: ignore[operator]
            assignment[c.pod] = node
            break
        else:
            unplaced.append(UnplacedPod(c.pod, (Code.NOT_SELECTED,),
                                        tuple(sorted(rejections, key=lambda r: (r.node, str(r.code))))))

    return SolverResult(
        solver=SOLVER_ID,
        config={"candidate_order": "cpu desc, memory desc, pod key asc", "destination_order": "node name asc"},
        assignments=tuple(Assignment(p, n) for p, n in sorted(assignment.items())),
        unplaced=tuple(sorted(unplaced, key=lambda u: u.pod)),
        objective=evaluate_objective(problem, assignment),
        optimality_proven=False,
        termination=Termination.COMPLETED,
        stats=SolverStats(states_explored=states, branches_pruned=0,
                          duration_seconds=time.perf_counter() - start),
    )
