"""Decode QAOA measurement counts and judge every sample with M2's verifier and objective.

Bit order: Qiskit count keys are little-endian, so qubit ``i`` is character
``key[-1 - i]``. Qubit ``i < len(assignment_vars)`` is the decision variable
``problem.variables``-derived pair ``assignment_vars[i]``; higher qubits are slack.

A sample is a usable placement only if ``verify_placement`` passes. Low QUBO
energy alone proves nothing. No classical repair is applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qplace.model import ClusterModel
from qplace.placement_problem import Code, Objective, PlacementProblem, evaluate_objective
from qplace.placement_result import Assignment, SolverResult, SolverStats, Termination, UnplacedPod
from qplace.placement_verify import verify_placement
from qplace.quantum_formulation import QuantumFormulation

SOLVER_ID = "qaoa"


def bits_from_key(key: str, num_qubits: int) -> list[int]:
    key = key.replace(" ", "")
    if len(key) != num_qubits or set(key) - {"0", "1"}:
        raise ValueError(f"bitstring {key!r} does not match {num_qubits} qubits")
    return [int(key[num_qubits - 1 - i]) for i in range(num_qubits)]


@dataclass(frozen=True)
class SampleRecord:
    bitstring: str
    count: int
    energy: int
    assignment: tuple[tuple[str, str], ...]
    raw_feasible: bool  # encoded constraints in original units
    verified: bool  # independent M2 verifier
    placed: int
    objective: Objective | None
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bitstring": self.bitstring,
            "count": self.count,
            "qubo_energy": self.energy,
            "assignment": [{"pod": p, "node": n} for p, n in self.assignment],
            "raw_feasible": self.raw_feasible,
            "verified": self.verified,
            "placed": self.placed,
            "m2_objective": self.objective.to_dict() if self.objective else None,
            "verification_errors": list(self.errors),
        }


class QuantumStatus(StrEnum):
    FEASIBLE_SAMPLE_MATCHES_EXACT_COUNT = "FEASIBLE_SAMPLE_MATCHES_EXACT_COUNT"
    FEASIBLE_SAMPLE_BELOW_EXACT_COUNT = "FEASIBLE_SAMPLE_BELOW_EXACT_COUNT"
    NO_FEASIBLE_SAMPLE = "NO_FEASIBLE_SAMPLE"


@dataclass(frozen=True)
class DecodedSamples:
    records: tuple[SampleRecord, ...]  # sorted by count desc, bitstring asc
    shots: int
    best: SampleRecord | None
    feasible_shots: int
    best_count_shots: int  # verified shots that place as many pods as the best verified sample
    exact_count_shots: int  # verified shots that reach the exact solver's count
    status: QuantumStatus

    @property
    def feasible_rate(self) -> float:
        return self.feasible_shots / self.shots if self.shots else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "shots": self.shots,
            "distinct_bitstrings": len(self.records),
            "feasible_shots": self.feasible_shots,
            "feasible_sample_rate": self.feasible_rate,
            "best_count_shots": self.best_count_shots,
            "best_count_sample_rate": self.best_count_shots / self.shots if self.shots else 0.0,
            "exact_count_shots": self.exact_count_shots,
            "exact_count_sample_rate": self.exact_count_shots / self.shots if self.shots else 0.0,
            "invalid_raw_shots": sum(r.count for r in self.records if not r.raw_feasible),
            "empty_placement_shots": sum(r.count for r in self.records if r.verified and r.placed == 0),
            "status": str(self.status),
            "best": self.best.to_dict() if self.best else None,
        }


def decode_quantum_samples(model: ClusterModel, problem: PlacementProblem, form: QuantumFormulation,
                           counts: dict[str, int], exact_count: int) -> DecodedSamples:
    if form.qubo is None:
        raise ValueError("formulation has no QUBO")
    n = form.qubo.num_variables
    records: list[SampleRecord] = []
    for key, count in counts.items():
        bits = bits_from_key(key, n)
        pairs = tuple(form.decode(bits))
        check = verify_placement(model, pairs)
        objective = evaluate_objective(problem, dict(pairs)) if check.valid else None
        records.append(SampleRecord(
            bitstring=key.replace(" ", ""), count=int(count), energy=form.qubo.energy(bits), assignment=pairs,
            raw_feasible=form.assignment_feasible(bits), verified=check.valid, placed=len(pairs),
            objective=objective, errors=tuple(str(e.code) for e in check.errors)))
    records.sort(key=lambda r: (-r.count, r.bitstring))
    best: SampleRecord | None = None
    for r in records:
        if r.verified and (best is None or r.objective.better_than(best.objective)):  # type: ignore[union-attr]
            best = r
    shots = sum(r.count for r in records)
    feasible = sum(r.count for r in records if r.verified)
    best_shots = sum(r.count for r in records if r.verified and best and r.placed == best.placed)
    exact_shots = sum(r.count for r in records if r.verified and r.placed == exact_count)
    if best is None:
        status = QuantumStatus.NO_FEASIBLE_SAMPLE
    elif best.placed >= exact_count:
        status = QuantumStatus.FEASIBLE_SAMPLE_MATCHES_EXACT_COUNT
    else:
        status = QuantumStatus.FEASIBLE_SAMPLE_BELOW_EXACT_COUNT
    return DecodedSamples(tuple(records), shots, best, feasible, best_shots, exact_shots, status)


def to_solver_result(problem: PlacementProblem, decoded: DecodedSamples, config: dict[str, Any],
                     duration_seconds: float) -> SolverResult | None:
    """A SolverResult only when a verified sample exists; never claims optimality."""
    if decoded.best is None:
        return None
    assignment = dict(decoded.best.assignment)
    unplaced = [UnplacedPod(c.pod, c.exclusion_reasons or (Code.NOT_SELECTED,), ())
                for c in problem.candidates if c.pod not in assignment]
    return SolverResult(
        solver=SOLVER_ID,
        config=config,
        assignments=tuple(Assignment(p, n) for p, n in sorted(assignment.items())),
        unplaced=tuple(sorted(unplaced, key=lambda u: u.pod)),
        objective=evaluate_objective(problem, assignment),
        optimality_proven=False,
        termination=Termination.COMPLETED,
        stats=SolverStats(states_explored=decoded.shots, branches_pruned=0, duration_seconds=duration_seconds),
    )
