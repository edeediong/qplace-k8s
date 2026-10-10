"""QAOA circuit, training, sampling, decoding and verified sample selection."""

import numpy as np
import pytest

from qplace import build_cluster_model, load_snapshot
from qplace.placement_problem import build_placement_problem
from qplace.placement_verify import verify_placement
from qplace.quantum_decode import QuantumStatus, bits_from_key, decode_quantum_samples, to_solver_result
from qplace.quantum_formulation import build_quantum_formulation
from qplace.quantum_qaoa import (
    QAOAConfig, bind, build_qaoa_circuit, cost_operator, sample_local, train_qaoa,
)
from qplace.serialize import cluster_model_dict, dumps
from qplace.placement_serialize import placement_problem_dict
from tests.conftest import ROOT

TRAP = ROOT / "fixtures" / "m2" / "scenario_b_greedy_trap.json"
FAST = QAOAConfig(reps=1, grid_points=6, maxiter=40, shots=512)


@pytest.fixture(scope="module")
def trap():
    model = build_cluster_model(load_snapshot(TRAP))
    problem = build_placement_problem(model)
    form = build_quantum_formulation(problem)
    return model, problem, form


def test_circuit_structure(trap):
    _, _, f = trap
    qc = build_qaoa_circuit(f.ising, reps=2)
    ops = qc.count_ops()
    assert qc.num_qubits == 3 and len(qc.parameters) == 4
    assert ops["rzz"] == 2 * len(f.ising.J) and ops["rx"] == 2 * 3 and ops["h"] == 3 and ops["measure"] == 3


def test_parameter_binding_matches_qiskit_qaoa_ansatz(trap):
    from qiskit.circuit.library import qaoa_ansatz
    from qiskit.quantum_info import Statevector
    _, _, f = trap
    op = cost_operator(f.ising)
    mine = bind(build_qaoa_circuit(f.ising, reps=1, measure=False), [0.7], [0.3])
    assert not mine.parameters
    ref = qaoa_ansatz(op, reps=1)  # Qiskit's own QAOA ansatz: exp(-i beta X) after exp(-i gamma H)
    ref = ref.assign_parameters({p: (0.3 if "β" in p.name else 0.7) for p in ref.parameters})
    e1 = Statevector(mine).expectation_value(op).real
    e2 = Statevector(ref).expectation_value(op).real
    assert abs(e1 - e2) < 1e-9


def test_training_is_repeatable_and_does_not_worsen_start(trap):
    _, _, f = trap
    a, b = train_qaoa(f.ising, FAST), train_qaoa(f.ising, FAST)
    assert (a.gammas, a.betas, a.final_energy) == (b.gammas, b.betas, b.final_energy)
    assert a.final_energy <= a.initial_energy + 1e-12 and a.evaluations > 36


def test_fixed_seed_sampling_is_repeatable(trap):
    _, _, f = trap
    t = train_qaoa(f.ising, FAST)
    assert sample_local(f.ising, t, 256, 5)[0] == sample_local(f.ising, t, 256, 5)[0]


def test_bit_order_is_little_endian(trap):
    _, _, f = trap
    assert bits_from_key("001", 3) == [1, 0, 0]
    assert f.decode(bits_from_key("001", 3)) == [("apps/batch-7a6b5c-aaaaa", "node-a")]
    # '110' means qubits 1 and 2: batch->node-b, indexer->node-a. Reading it big-endian would give batch->node-a twice.
    assert f.decode(bits_from_key("110", 3)) == [("apps/batch-7a6b5c-aaaaa", "node-b"),
                                                 ("apps/indexer-8b7c6d-aaaaa", "node-a")]
    with pytest.raises(ValueError):
        bits_from_key("01", 3)


def test_best_sample_is_independently_verified_not_lowest_energy(trap):
    model, problem, f = trap
    # '101' = batch->node-a + indexer->node-a: CPU 3500 > 2500, infeasible, but sampled most often.
    counts = {"101": 500, "110": 10, "001": 300}
    d = decode_quantum_samples(model, problem, f, counts, exact_count=2)
    by_key = {r.bitstring: r for r in d.records}
    assert not by_key["101"].verified and not by_key["101"].raw_feasible
    assert "CPU_CAPACITY_EXCEEDED" in by_key["101"].errors
    assert d.best.bitstring == "110" and d.status is QuantumStatus.FEASIBLE_SAMPLE_MATCHES_EXACT_COUNT
    assert d.feasible_shots == 310 and d.exact_count_shots == 10


def test_no_feasible_sample_is_an_explicit_failure(trap):
    model, problem, f = trap
    d = decode_quantum_samples(model, problem, f, {"101": 7, "011": 3}, exact_count=2)
    assert d.best is None and d.status is QuantumStatus.NO_FEASIBLE_SAMPLE
    assert to_solver_result(problem, d, {}, 0.0) is None  # never fabricated


def test_quantum_solver_result_contract(trap):
    model, problem, f = trap
    d = decode_quantum_samples(model, problem, f, {"110": 4, "001": 9}, exact_count=2)
    r = to_solver_result(problem, d, {"reps": 1}, 0.01)
    assert r.solver == "qaoa" and r.optimality_proven is False
    assert verify_placement(model, [(a.pod, a.node) for a in r.assignments]).valid
    assert [a.pod for a in r.assignments] == sorted(a.pod for a in r.assignments) and r.unplaced == ()


def test_raw_feasibility_agrees_with_m2_verifier_on_every_state():
    for name in ("m2/scenario_c_aggregate_shortage", "m3/scenario_g_triple_capacity", "m2/scenario_f_pod_slots"):
        model = build_cluster_model(load_snapshot(ROOT / "fixtures" / f"{name}.json"))
        problem = build_placement_problem(model)
        f = build_quantum_formulation(problem)
        n = f.qubo.num_variables
        for mask in range(1 << n):
            bits = [(mask >> i) & 1 for i in range(n)]
            assert f.assignment_feasible(bits) == verify_placement(model, f.decode(bits)).valid


def test_quantum_pipeline_does_not_mutate_m1_or_m2(trap):
    model, problem, f = trap
    before = (dumps(cluster_model_dict(model)), dumps(placement_problem_dict(problem)))
    t = train_qaoa(f.ising, FAST)
    counts, _ = sample_local(f.ising, t, 64, 1)
    decode_quantum_samples(model, problem, f, counts, 2)
    assert (dumps(cluster_model_dict(model)), dumps(placement_problem_dict(problem))) == before
