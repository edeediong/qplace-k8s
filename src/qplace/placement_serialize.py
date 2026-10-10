"""Deterministic M2 artifacts: placement_problem.json, placement_results.json, benchmark_results.json.

Runtime measurements are observational. They live only in the
``runtime_observations`` block of benchmark_results.json, which reproducibility
checks exclude.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from qplace import __version__
from qplace.model import ClusterModel
from qplace.placement_assess import DISCLAIMER, PlacementEvaluation, PlacementReport, assess_placement
from qplace.placement_exact import ExactConfig, solve_exact
from qplace.placement_greedy import solve_greedy
from qplace.placement_problem import PlacementProblem

ARTIFACT_SCHEMA_VERSION = "1.0"

FORMULATION = {
    "variables": "x[p,n] in {0,1} for each allowed (candidate p, destination n) pair listed in 'variables'",
    "constraints": [
        "sum_n x[p,n] <= 1 for every candidate p (at most one destination)",
        "x[p,n] exists only for M1-ELIGIBLE pairs on usable destinations (eligibility)",
        "sum_p cpu[p] * x[p,n] <= remaining_cpu[n] for every destination n",
        "sum_p mem[p] * x[p,n] <= remaining_memory[n] for every destination n",
        "sum_p x[p,n] <= remaining_pods[n] for every destination n",
    ],
    "objective": [
        "1. maximize sum x[p,n] (pods placed)",
        "2. maximize, lexicographically, the ascending vector over usable destinations of "
        "min_r (remaining_r[n] - used_r[n]) / allocatable_r[n] for r in cpu, memory, pods (leximin balance)",
        "3. minimize the tie-break vector over candidates in pod-key order: (0, node) if placed, (1, '') if not",
    ],
}


def _header(problem: PlacementProblem, artifact: str) -> dict[str, Any]:
    return {
        "artifact": artifact,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generator": {"name": "qplace", "version": __version__, "milestone": "M2"},
        "source": problem.provenance,
        "target_node": problem.target_node,
    }


def placement_problem_dict(problem: PlacementProblem) -> dict[str, Any]:
    out = _header(problem, "placement_problem")
    out.update({
        "units": {"cpu": "millicores", "memory": "bytes", "pods": "count"},
        "candidates": [c.to_dict() for c in problem.candidates],
        "destinations": [d.to_dict() for d in problem.destinations],
        "variables": [{"index": i, "pod": p, "node": n} for i, (p, n) in enumerate(problem.variables)],
        "conditions": [c.to_dict() for c in problem.conditions],
        "limitations": [c.to_dict() for c in problem.limitations],
        "policy_excluded_pairs": [{"pod": p, "node": n} for p, n in problem.policy_excluded_pairs],
        "formulation": FORMULATION,
        "summary": {
            "candidates": len(problem.candidates),
            "usable_candidates": len(problem.usable_candidates),
            "destinations": len(problem.destinations),
            "usable_destinations": len(problem.usable_destinations),
            "variables": len(problem.variables),
            "conditional": problem.is_conditional,
        },
    })
    return out


def _remaining_after(problem: PlacementProblem, ev: PlacementEvaluation) -> list[dict[str, Any]]:
    usage = {u.node: u for u in ev.verification.usage}
    rows = []
    for d in problem.destinations:
        u = usage.get(d.node)
        used = (u.cpu_millicores, u.memory_bytes, len(u.assigned_pods)) if u else (0, 0, 0)
        before = (d.cpu_millicores, d.memory_bytes, d.pod_slots)
        rows.append({
            "node": d.node,
            "usable": d.usable,
            "assigned": {"cpu_millicores": used[0], "memory_bytes": used[1], "pods": used[2]},
            "remaining_after": {
                k: None if b is None else b - x
                for k, b, x in zip(("cpu_millicores", "memory_bytes", "pods"), before, used)
            },
        })
    return rows


def _evaluation_dict(problem: PlacementProblem, ev: PlacementEvaluation) -> dict[str, Any]:
    r = ev.result
    return {
        "solver": r.solver,
        "config": r.config,
        "outcome": str(ev.outcome),
        "optimality_proven": r.optimality_proven,
        "termination": str(r.termination),
        "assignments": [a.to_dict() for a in r.assignments],
        "unplaced": [u.to_dict() for u in r.unplaced],
        "objective": r.objective.to_dict(),
        "verification": ev.verification.to_dict(),
        "remaining_capacity": _remaining_after(problem, ev),
        "search": r.stats.deterministic_dict(),
        "metrics": {
            "candidates": len(problem.candidates),
            "assigned": len(r.assignments),
            "unplaced": len(r.unplaced),
            "verified": ev.verification.valid,
        },
    }


def placement_results_dict(report: PlacementReport) -> dict[str, Any]:
    p = report.problem
    out = _header(p, "placement_results")
    out.update({
        "assessment": str(report.assessment),
        "findings": [str(f) for f in report.findings],
        "disclaimer": DISCLAIMER,
        "greedy": _evaluation_dict(p, report.greedy),
        "exact": _evaluation_dict(p, report.exact),
        "verified_assignments": [a.to_dict() for a in report.verified_assignments],
        "verified_assignments_source": report.supporting,
        "greedy_gap": report.greedy_gap,
        "conditions": [c.to_dict() for c in p.conditions],
        "limitations": [c.to_dict() for c in p.limitations],
    })
    return out


def _time(fn, repeats: int) -> list[float]:  # noqa: ANN001
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return samples


def benchmark_results_dict(fixture_id: str, model: ClusterModel, repeats: int = 5,
                           exact_config: ExactConfig = ExactConfig()) -> dict[str, Any]:
    report = assess_placement(model, exact_config)
    p = report.problem
    g, e = report.greedy, report.exact
    greedy_times = _time(lambda: solve_greedy(p), repeats)
    exact_times = _time(lambda: solve_exact(p, exact_config), repeats)

    def stats(samples: list[float]) -> dict[str, float]:
        return {"min": min(samples), "median": statistics.median(samples), "max": max(samples)}

    out = _header(p, "benchmark_results")
    out.update({
        "fixture": fixture_id,
        "solver_configuration": {"greedy": g.result.config, "exact": e.result.config},
        "deterministic": {
            "candidates": len(p.candidates),
            "usable_candidates": len(p.usable_candidates),
            "usable_destinations": len(p.usable_destinations),
            "variables": len(p.variables),
            "greedy": {"placed": len(g.result.assignments), "outcome": str(g.outcome),
                       "verified": g.verification.valid},
            "exact": {"placed": len(e.result.assignments), "outcome": str(e.outcome),
                      "verified": e.verification.valid, "optimality_proven": e.result.optimality_proven,
                      "termination": str(e.result.termination), **e.result.stats.deterministic_dict()},
            "greedy_optimality_gap": report.greedy_gap,
            "assessment": str(report.assessment),
        },
        "runtime_observations": {
            "note": "Wall-clock seconds on the machine that generated this file; varies between runs.",
            "repeats": repeats,
            "greedy_seconds": stats(greedy_times),
            "exact_seconds": stats(exact_times),
        },
    })
    return out
