"""Run the M3 quantum experiment for one snapshot and build its six artifacts.

Deterministic artifacts (byte-checked): quantum_problem.json, qubo.json, ising_hamiltonian.json.
Observational artifacts (schema- and structure-checked; contain floats from optimization,
random samples and wall-clock times): qaoa_experiment.json, quantum_samples.json,
comparison_results.json.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

from qplace import __version__
from qplace.model import ClusterModel
from qplace.placement_assess import PlacementReport, assess_placement
from qplace.placement_exact import solve_exact
from qplace.placement_greedy import solve_greedy
from qplace.placement_problem import PlacementProblem, build_placement_problem
from qplace.placement_verify import verify_placement
from qplace.quantum_decode import DecodedSamples, bits_from_key, decode_quantum_samples, to_solver_result
from qplace.quantum_formulation import (
    FormulationEvidence,
    FormulationStatus,
    QuantumConfig,
    QuantumFormulation,
    build_quantum_formulation,
    validate_formulation,
)
from qplace.quantum_qaoa import (
    QAOAConfig,
    QAOATraining,
    circuit_metrics,
    exact_distribution,
    hamiltonian_scale,
    sample_local,
    train_qaoa,
)

ARTIFACT_SCHEMA_VERSION = "1.0"
REPS = (1, 2)
BACKEND_LABEL = "qiskit.primitives.StatevectorSampler (local, noiseless, shot-based sampling of the exact statevector)"


@dataclass
class RepsRun:
    reps: int
    training: QAOATraining
    circuit: dict[str, Any]
    exact_probabilities: dict[str, float]
    runs: list[tuple[int, dict[str, int], float, DecodedSamples, float]]  # seed, counts, sample s, decoded, decode s


@dataclass
class QuantumExperiment:
    fixture: str
    model: ClusterModel
    problem: PlacementProblem
    report: PlacementReport
    formulation: QuantumFormulation
    evidence: FormulationEvidence | None
    qaoa_config: QAOAConfig
    reps_runs: list[RepsRun]
    classical_timing: dict[str, dict[str, float]]


def _timed(fn, repeats: int = 5) -> dict[str, float]:  # noqa: ANN001
    samples = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t)
    return {"min": min(samples), "median": statistics.median(samples), "max": max(samples), "repeats": repeats}


def run_experiment(fixture: str, model: ClusterModel, qconfig: QuantumConfig = QuantumConfig(),
                   qaoa_config: QAOAConfig = QAOAConfig(), reps_list: tuple[int, ...] = REPS) -> QuantumExperiment:
    report = assess_placement(model)
    problem = build_placement_problem(model)
    form = build_quantum_formulation(problem, qconfig)
    evidence = validate_formulation(form) if form.status is FormulationStatus.SUPPORTED else None
    classical = {"greedy_seconds": _timed(lambda: solve_greedy(problem)),
                 "exact_seconds": _timed(lambda: solve_exact(problem))}
    runs: list[RepsRun] = []
    if evidence is not None and evidence.valid:
        exact_count = len(report.exact.result.assignments)
        for reps in reps_list:
            cfg = replace(qaoa_config, reps=reps)
            training = train_qaoa(form.ising, cfg)  # type: ignore[arg-type]
            seeds = []
            for seed in cfg.sampling_seeds:
                counts, sample_s = sample_local(form.ising, training, cfg.shots, seed)  # type: ignore[arg-type]
                t = time.perf_counter()
                decoded = decode_quantum_samples(model, problem, form, counts, exact_count)
                seeds.append((seed, counts, sample_s, decoded, time.perf_counter() - t))
            runs.append(RepsRun(reps, training, circuit_metrics(form.ising, reps),  # type: ignore[arg-type]
                                exact_distribution(form.ising, training), seeds))  # type: ignore[arg-type]
    return QuantumExperiment(fixture, model, problem, report, form, evidence, qaoa_config, runs, classical)


# --------------------------------------------------------------------------- artifacts


def _header(exp: QuantumExperiment, artifact: str) -> dict[str, Any]:
    prov = exp.problem.provenance
    return {
        "artifact": artifact,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generator": {"name": "qplace", "version": __version__, "milestone": "M3"},
        "fixture": exp.fixture,
        "source": {"snapshot_id": prov["snapshot_id"], "source_sha256": prov["source_sha256"]},
        "target_node": exp.problem.target_node,
    }


def quantum_problem_dict(exp: QuantumExperiment) -> dict[str, Any]:
    f = exp.formulation
    out = _header(exp, "quantum_problem")
    out.update({
        "status": str(f.status),
        "qubit_budget": f.config.qubit_budget,
        "required_qubits": f.required_qubits,
        "assignment_qubits": f.num_assignment_qubits,
        "slack_qubits": f.num_slack_qubits,
        "qubits": [{"qubit": i, "kind": "assignment", "pod": p, "node": n} for i, (p, n) in enumerate(f.assignment_vars)]
        + [{"qubit": f.num_assignment_qubits + k, "kind": "slack", "constraint": c} for k, c in enumerate(f.slack_owner)],
        "eliminated_variables": [{"pod": p, "node": n, "reason": r} for p, n, r in f.eliminated_vars],
        "m2_variables": [{"pod": p, "node": n} for p, n in exp.problem.variables],
        "m2_assessment": str(exp.report.assessment),
        "m2_conditions": list(f.conditions),
        "bit_order": "Qiskit little-endian: qubit i is character [-1 - i] of a counts key",
    })
    return out


def qubo_dict(exp: QuantumExperiment) -> dict[str, Any]:
    f = exp.formulation
    out = _header(exp, "qubo")
    out.update({
        "status": str(f.status),
        "objective": "minimize -sum(assignment x) + penalties; primary M2 objective (pods placed) only",
        "penalty": f.penalty,
        "penalty_rule": "P = number of assignment variables; any violated term costs >= P",
        "constraints": [c.to_dict() for c in f.constraints],
        "qubo": f.qubo.to_dict() if f.qubo else None,
        "correctness_evidence": exp.evidence.to_dict() if exp.evidence else None,
    })
    return out


def ising_dict(exp: QuantumExperiment) -> dict[str, Any]:
    f = exp.formulation
    out = _header(exp, "ising_hamiltonian")
    out.update({
        "status": str(f.status),
        "ising": f.ising.to_dict() if f.ising else None,
        "circuit_scale": hamiltonian_scale(f.ising) if f.ising else None,
        "circuit_scale_note": "QAOA uses H / max|coefficient|; eigenvectors and their order are unchanged",
    })
    return out


def _decoded_rows(exp: QuantumExperiment) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for rr in exp.reps_runs:
        for _seed, _c, _s, decoded, _d in rr.runs:
            for r in decoded.records:
                row = r.to_dict()
                row.pop("count")
                table.setdefault(r.bitstring, row)
    return dict(sorted(table.items()))


def qaoa_experiment_dict(exp: QuantumExperiment) -> dict[str, Any]:
    out = _header(exp, "qaoa_experiment")
    out.update({
        "observational": True,
        "backend": BACKEND_LABEL,
        "config": asdict(exp.qaoa_config) | {"reps_evaluated": [rr.reps for rr in exp.reps_runs]},
        "runs": [{
            "reps": rr.reps,
            "circuit": rr.circuit,
            "initial_parameters": {"gamma": rr.training.initial_gammas, "beta": rr.training.initial_betas},
            "final_parameters": {"gamma": rr.training.gammas, "beta": rr.training.betas},
            "scaled_energy": {"initial": rr.training.initial_energy, "final": rr.training.final_energy},
            "optimizer": rr.training.optimizer,
            "estimator_evaluations": rr.training.evaluations,
            "training_seconds": rr.training.duration_seconds,
        } for rr in exp.reps_runs],
        "skipped": None if exp.reps_runs else _skip_reason(exp),
    })
    return out


def _skip_reason(exp: QuantumExperiment) -> str:
    if exp.formulation.status is not FormulationStatus.SUPPORTED:
        return str(exp.formulation.status)
    return "FORMULATION_REJECTED" if exp.evidence and not exp.evidence.valid else "NOT_RUN"


def _verified_placed(exp: QuantumExperiment) -> dict[str, int | None]:
    """Every bitstring -> pods placed if verify_placement accepts it, else None (review F4: M2 verifier only)."""
    f = exp.formulation
    n = f.qubo.num_variables  # type: ignore[union-attr]
    table: dict[str, int | None] = {}
    for mask in range(1 << n):
        key = format(mask, f"0{n}b")
        pairs = f.decode(bits_from_key(key, n))
        table[key] = len(pairs) if verify_placement(exp.model, pairs).valid else None
    return table


def _optimal_probability(table: dict[str, int | None], probabilities: dict[str, float], exact_count: int) -> float:
    return sum(p for key, p in probabilities.items() if table[key] == exact_count)


def uniform_baseline(exp: QuantumExperiment) -> dict[str, Any] | None:
    """What uniformly random bitstrings would score (review F1): the null model for every QAOA rate."""
    if exp.formulation.qubo is None:
        return None
    table = _verified_placed(exp)
    exact_n = len(exp.report.exact.result.assignments)
    total = len(table)
    return {
        "states": total,
        "feasible_rate": sum(1 for v in table.values() if v is not None) / total,
        "nonempty_feasible_rate": sum(1 for v in table.values() if v) / total,
        "exact_count_rate": sum(1 for v in table.values() if v == exact_n) / total,
        "probability_seed_hits_exact_count_in_shots": None,  # filled per shot count below
    }


def quantum_samples_dict(exp: QuantumExperiment) -> dict[str, Any]:
    out = _header(exp, "quantum_samples")
    out.update({
        "observational": True,
        "backend": BACKEND_LABEL,
        "bitstrings": _decoded_rows(exp),
        "runs": [{
            "reps": rr.reps,
            "seed": seed,
            "shots": decoded.shots,
            "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "summary": decoded.summary(),
            "sampling_seconds": sample_s,
            "decode_verify_seconds": decode_s,
        } for rr in exp.reps_runs for seed, counts, sample_s, decoded, decode_s in rr.runs],
    })
    return out


def comparison_dict(exp: QuantumExperiment) -> dict[str, Any]:
    rep = exp.report
    exact_n = len(rep.exact.result.assignments)
    out = _header(exp, "comparison_results")
    quantum = []
    table = _verified_placed(exp) if exp.reps_runs else {}
    uniform = uniform_baseline(exp) if exp.reps_runs else None
    if uniform is not None:
        shots = exp.qaoa_config.shots
        uniform["probability_seed_hits_exact_count_in_shots"] = 1 - (1 - uniform["exact_count_rate"]) ** shots
        uniform["shots"] = shots
    for rr in exp.reps_runs:
        decs = [d for _s, _c, _t, d, _x in rr.runs]
        best_overall, best_seed_index = None, 0
        for k, d in enumerate(decs):
            if d.best and (best_overall is None or d.best.objective.better_than(best_overall.objective)):
                best_overall, best_seed_index = d.best, k
        # review F5: the SolverResult is built from the same seed that produced the best sample
        best_dec = decs[best_seed_index]
        solver = to_solver_result(exp.problem, best_dec, {"reps": rr.reps}, 0.0) if best_dec.best else None
        p_opt = _optimal_probability(table, rr.exact_probabilities, exact_n)
        quantum.append({
            "reps": rr.reps,
            "qubits": rr.circuit["qubits"],
            "circuit_depth": rr.circuit["depth"],
            "two_qubit_gates": rr.circuit["two_qubit_gates"],
            "estimator_evaluations": rr.training.evaluations,
            "sampler_jobs": len(rr.runs),
            "training_seconds": rr.training.duration_seconds,
            "sampling_seconds_median": statistics.median(s for _a, _b, s, _c, _d in rr.runs),
            "exact_probability_of_optimal_count": p_opt,
            "uniform_probability_of_optimal_count": uniform["exact_count_rate"],  # type: ignore[index]
            "ratio_to_uniform": p_opt / uniform["exact_count_rate"] if uniform["exact_count_rate"] else None,  # type: ignore[index]
            "better_than_uniform": p_opt > uniform["exact_count_rate"],  # type: ignore[index]
            "per_seed": [{"seed": seed, "best_placed": d.best.placed if d.best else None,
                          "status": str(d.status), "feasible_sample_rate": d.feasible_rate,
                          "exact_count_sample_rate": d.exact_count_shots / d.shots}
                         for seed, _c, _t, d, _x in rr.runs],
            "seeds_reaching_exact_count": sum(1 for d in decs if d.best and d.best.placed >= exact_n),
            "feasible_sample_rate_mean": statistics.mean(d.feasible_rate for d in decs),
            "exact_count_sample_rate_mean": statistics.mean(d.exact_count_shots / d.shots for d in decs),
            "best_verified_sample": best_overall.to_dict() if best_overall else None,
            "optimality_gap_pods": exact_n - best_overall.placed if best_overall else None,
            "m2_objective_equals_exact": (best_overall is not None
                                          and best_overall.objective == rep.exact.result.objective),
            "solver_result_seed": rr.runs[best_seed_index][0],
            "solver_result": None if solver is None else {
                "solver": solver.solver, "assignments": [a.to_dict() for a in solver.assignments],
                "unplaced": [u.pod for u in solver.unplaced], "optimality_proven": solver.optimality_proven},
        })
    out.update({
        "observational": True,
        "classical": {
            "greedy": {"placed": len(rep.greedy.result.assignments), "verified": rep.greedy.verification.valid,
                       "objective": rep.greedy.result.objective.to_dict(),
                       "assignments": [a.to_dict() for a in rep.greedy.result.assignments]},
            "exact": {"placed": exact_n, "verified": rep.exact.verification.valid,
                      "optimality_proven": rep.exact.result.optimality_proven,
                      "objective": rep.exact.result.objective.to_dict(),
                      "assignments": [a.to_dict() for a in rep.exact.result.assignments]},
            "candidates": len(exp.problem.candidates),
            "m2_assessment": str(rep.assessment),
            "timing_seconds": exp.classical_timing,
        },
        "quantum": quantum,
        "uniform_random_baseline": uniform,
        "quantum_skipped": None if exp.reps_runs else _skip_reason(exp),
        "claims": {
            "quantum_optimality_proven": False,
            "operational_assessment": str(rep.assessment),
            "note": "QAOA results are local noiseless simulation. They inherit M2's assessment and conditions; "
                    "a verified sample is a placement in the requested-resource model, not a drain approval.",
        },
    })
    return out


ARTIFACTS = {
    "quantum_problem.json": (quantum_problem_dict, True),
    "qubo.json": (qubo_dict, True),
    "ising_hamiltonian.json": (ising_dict, True),
    "qaoa_experiment.json": (qaoa_experiment_dict, False),
    "quantum_samples.json": (quantum_samples_dict, False),
    "comparison_results.json": (comparison_dict, False),
}
