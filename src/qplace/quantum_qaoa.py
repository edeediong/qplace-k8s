"""QAOA on the validated Ising model (Qiskit 2.x, V2 primitives).

Circuit for H = sum h_i Z_i + sum J_ij Z_i Z_j (offset dropped), scaled by
``s = max |coefficient|`` so one parameter range fits every instance:

    |+>^n  ->  prod_layers [ exp(-i gamma H/s) ; exp(-i beta sum X) ]  ->  measure

with exp(-i gamma h Z) = RZ(2 gamma h / s) and exp(-i gamma J ZZ) = RZZ(2 gamma J / s),
exp(-i beta X) = RX(2 beta). Qubit i is assignment/slack variable i.

Training is classical: exact expectation values from ``StatevectorEstimator``,
a fixed grid for the starting point, then COBYLA (``qiskit_optimization.optimizers``).
Sampling uses ``StatevectorSampler`` with a fixed seed, or any BackendV2 via ``backend.run``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from math import pi
from typing import Any

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import ParameterVector
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.quantum_info import SparsePauliOp, Statevector
from qiskit_optimization.optimizers import COBYLA

from qplace.quantum_formulation import Ising


@dataclass(frozen=True)
class QAOAConfig:
    reps: int = 1
    seed: int = 20261011
    shots: int = 1024
    grid_points: int = 12  # per angle for the p=1 starting grid
    maxiter: int = 150
    sampling_seeds: tuple[int, ...] = (11, 22, 33, 44, 55)


def hamiltonian_scale(ising: Ising) -> float:
    coeffs = [abs(float(c)) for c in ising.h.values()] + [abs(float(c)) for c in ising.J.values()]
    return max(coeffs) if coeffs else 1.0


def cost_operator(ising: Ising, scaled: bool = True) -> SparsePauliOp:
    s = hamiltonian_scale(ising) if scaled else 1.0
    terms = [(label, c / s) for label, c in ising.pauli_terms()]
    if not terms:
        terms = [("I" * ising.num_qubits, 0.0)]
    return SparsePauliOp.from_list(terms)


def build_qaoa_circuit(ising: Ising, reps: int = 1, measure: bool = True) -> QuantumCircuit:
    """Parameterized QAOA circuit with parameters gamma[0..p-1], beta[0..p-1]."""
    n = ising.num_qubits
    s = hamiltonian_scale(ising)
    gamma = ParameterVector("gamma", reps)
    beta = ParameterVector("beta", reps)
    qc = QuantumCircuit(n, name="qplace_qaoa")
    qc.h(range(n))
    for layer in range(reps):
        for i, c in sorted(ising.h.items()):
            qc.rz(2 * gamma[layer] * float(c) / s, i)
        for (i, j), c in sorted(ising.J.items()):
            qc.rzz(2 * gamma[layer] * float(c) / s, i, j)
        for i in range(n):
            qc.rx(2 * beta[layer], i)
    if measure:
        qc.measure_all()
    return qc


def bind(qc: QuantumCircuit, gammas: list[float], betas: list[float]) -> QuantumCircuit:
    values = {}
    for p in qc.parameters:
        idx = p.index
        values[p] = gammas[idx] if p.vector.name == "gamma" else betas[idx]
    return qc.assign_parameters(values)


@dataclass
class QAOATraining:
    reps: int
    initial_gammas: list[float]
    initial_betas: list[float]
    gammas: list[float]
    betas: list[float]
    initial_energy: float  # scaled-Hamiltonian expectation at the start point
    final_energy: float
    evaluations: int
    optimizer: dict[str, Any]
    duration_seconds: float
    history: list[float] = field(default_factory=list)


def train_qaoa(ising: Ising, config: QAOAConfig = QAOAConfig()) -> QAOATraining:
    start = time.perf_counter()
    op = cost_operator(ising)
    ansatz = build_qaoa_circuit(ising, config.reps, measure=False)
    estimator = StatevectorEstimator()
    history: list[float] = []
    ordered = sorted(ansatz.parameters, key=lambda p: (p.vector.name != "gamma", p.index))  # gammas, then betas

    def energy(theta: np.ndarray) -> float:
        values = dict(zip(ordered, theta))
        ev = float(estimator.run([(ansatz.assign_parameters(values), op)]).result()[0].data.evs)
        history.append(ev)
        return ev

    # Deterministic starting point: best of a p=1 grid, repeated for deeper layers.
    grid_g = np.linspace(pi / config.grid_points, pi, config.grid_points)
    grid_b = np.linspace(pi / (4 * config.grid_points), pi / 2, config.grid_points)
    best = None
    for g in grid_g:
        for b in grid_b:
            e = energy(np.array([g] * config.reps + [b] * config.reps))
            if best is None or e < best[0] - 1e-12:
                best = (e, float(g), float(b))
    x0 = np.array([best[1]] * config.reps + [best[2]] * config.reps)
    optimizer = COBYLA(maxiter=config.maxiter)
    result = optimizer.minimize(fun=energy, x0=x0)
    theta = np.asarray(result.x, dtype=float)
    return QAOATraining(
        reps=config.reps,
        initial_gammas=[float(v) for v in x0[: config.reps]],
        initial_betas=[float(v) for v in x0[config.reps:]],
        gammas=[float(v) for v in theta[: config.reps]],
        betas=[float(v) for v in theta[config.reps:]],
        initial_energy=float(best[0]),
        final_energy=float(result.fun),
        evaluations=len(history),
        optimizer={"name": "COBYLA", "source": "qiskit_optimization.optimizers", "maxiter": config.maxiter,
                   "start": f"best of {config.grid_points}x{config.grid_points} (gamma, beta) grid",
                   "objective": "exact <H/s> via StatevectorEstimator"},
        duration_seconds=time.perf_counter() - start,
        history=history,
    )


def exact_distribution(ising: Ising, training: QAOATraining) -> dict[str, float]:
    """Exact output probabilities of the trained circuit (little-endian bitstrings)."""
    qc = bind(build_qaoa_circuit(ising, training.reps, measure=False), training.gammas, training.betas)
    probs = Statevector(qc).probabilities_dict()
    return {k: float(v) for k, v in probs.items() if v > 1e-12}


def sample_local(ising: Ising, training: QAOATraining, shots: int, seed: int) -> tuple[dict[str, int], float]:
    start = time.perf_counter()
    qc = bind(build_qaoa_circuit(ising, training.reps), training.gammas, training.betas)
    sampler = StatevectorSampler(default_shots=shots, seed=seed)
    counts = sampler.run([qc]).result()[0].data.meas.get_counts()
    return dict(counts), time.perf_counter() - start


def circuit_metrics(ising: Ising, reps: int) -> dict[str, Any]:
    qc = build_qaoa_circuit(ising, reps)
    ops = {k: int(v) for k, v in sorted(qc.count_ops().items())}
    return {"qubits": qc.num_qubits, "depth": qc.depth(), "size": qc.size(), "gate_counts": ops,
            "two_qubit_gates": ops.get("rzz", 0)}
