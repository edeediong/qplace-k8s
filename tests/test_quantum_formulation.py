"""M3 QUBO / Ising formulation: indexing, coefficients, normalization, exhaustive correctness."""

import random
from fractions import Fraction
from itertools import product

import pytest

from qplace import build_cluster_model, load_snapshot
from qplace.placement_assess import assess_placement
from qplace.placement_problem import build_placement_problem
from qplace.quantum_formulation import (
    Encoding, FormulationStatus, QuantumConfig, _pairwise_exact, _slack_weights,
    build_quantum_formulation, qubo_to_ising, validate_formulation,
)
from tests.conftest import ROOT, build_model, cand, raw_node
from tests.test_placement_exact import random_problem

FIXTURES = (sorted((ROOT / "fixtures" / "m2").glob("scenario_*.json"))
            + sorted((ROOT / "fixtures" / "m3").glob("scenario_*.json"))
            + sorted((ROOT / "fixtures" / "m1").glob("fixture_*.json")))


def load(name):
    path = next(p for p in FIXTURES if p.stem == name)
    model = build_cluster_model(load_snapshot(path))
    return model, build_placement_problem(model)


def test_qubits_follow_m2_variable_order_and_skip_only_eliminated():
    model, p = load("fixture_b_insufficient")
    f = build_quantum_formulation(p)
    eliminated = {(a, b) for a, b, _ in f.eliminated_vars}
    assert list(f.assignment_vars) == [v for v in p.variables if v not in eliminated]
    assert all(r.startswith("INDIVIDUALLY_INFEASIBLE") for *_, r in f.eliminated_vars) and eliminated


def test_no_forbidden_assignment_variables():
    for path in FIXTURES:
        model, p = load(path.stem)
        f = build_quantum_formulation(p)
        for pod, node in f.assignment_vars:
            assert node in model.eligible_destinations(pod)  # only M1-ELIGIBLE pairs become qubits
            assert node != model.target_node


def test_greedy_trap_qubo_coefficients_by_hand():
    _, p = load("scenario_b_greedy_trap")
    f = build_quantum_formulation(p)
    assert f.assignment_vars == (("apps/batch-7a6b5c-aaaaa", "node-a"), ("apps/batch-7a6b5c-aaaaa", "node-b"),
                                 ("apps/indexer-8b7c6d-aaaaa", "node-a"))
    assert f.penalty == 3 and f.num_slack_qubits == 0
    # reward -1 per placement; P on (batch@a, batch@b) for at-most-one and on (batch@a, indexer@a) for CPU 3500 > 2500
    assert f.qubo.linear == {0: -1, 1: -1, 2: -1}
    assert f.qubo.quadratic == {(0, 1): 3, (0, 2): 3} and f.qubo.constant == 0
    assert f.ising.h == {0: Fraction(-1), 1: Fraction(-1, 4), 2: Fraction(-1, 4)}
    assert f.ising.J == {(0, 1): Fraction(3, 4), (0, 2): Fraction(3, 4)}


def test_lossless_resource_normalization_for_slack_constraint():
    _, p = load("scenario_g_triple_capacity")
    f = build_quantum_formulation(p)
    (slack,) = [c for c in f.constraints if c.encoding is Encoding.CAPACITY_SLACK]
    assert slack.name == "cap:node-big:cpu_millicores"
    assert slack.coefficients == (1000, 1000, 1000, 1000) and slack.capacity == 2500
    assert slack.scale == 1000 and slack.reduced_coefficients == (1, 1, 1, 1) and slack.reduced_capacity == 2
    assert slack.slack_weights == (1, 1) and f.required_qubits == 10
    # floor(C/g) is exact: for every subset, sum(a) <= C  <=>  sum(a/g) <= floor(C/g)
    for mask in product([0, 1], repeat=4):
        assert (1000 * sum(mask) <= 2500) == (sum(mask) <= 2)


@pytest.mark.parametrize("cap", range(1, 70))
def test_slack_weights_cover_exactly_zero_to_capacity(cap):
    w = _slack_weights(cap)
    sums = {sum(x * y for x, y in zip(bits, w)) for bits in product([0, 1], repeat=len(w))}
    assert sums == set(range(cap + 1)) and len(w) == cap.bit_length()


def test_pairwise_exactness_detection():
    assert _pairwise_exact([1500, 1500, 1500], 2000)  # any two already violate
    assert not _pairwise_exact([1000, 1000, 1000], 2500)  # pairs fit, the triple does not
    assert _pairwise_exact([1, 1], 1)  # two unit items, capacity one (a pod-slot limit of 1)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_exhaustive_minima_are_feasible_max_cardinality(path):
    model, p = load(path.stem)
    f = build_quantum_formulation(p)
    ev = validate_formulation(f)
    assert ev.valid, ev
    assert ev.max_feasible_pods == len(assess_placement(model).exact.result.assignments)
    assert ev.feasible_energy_exact and ev.infeasible_strictly_above_minimum and ev.ising_matches_qubo


def test_exhaustive_validation_on_random_problems():
    rng = random.Random(311)
    checked = 0
    for _ in range(120):
        p = random_problem(rng)
        f = build_quantum_formulation(p, QuantumConfig(qubit_budget=14))
        if f.status is FormulationStatus.SUPPORTED:
            assert validate_formulation(f).valid
            checked += 1
    assert checked > 60


def test_ising_matches_independent_qiskit_optimization_conversion():
    from qiskit_optimization import QuadraticProgram
    from qiskit.quantum_info import Statevector
    _, p = load("scenario_g_triple_capacity")
    f = build_quantum_formulation(p)
    qp = QuadraticProgram()
    for i in range(f.qubo.num_variables):
        qp.binary_var(f"x{i}")
    qp.minimize(constant=f.qubo.constant, linear={f"x{i}": a for i, a in f.qubo.linear.items()},
                quadratic={(f"x{i}", f"x{j}"): b for (i, j), b in f.qubo.quadratic.items()})
    op, offset = qp.to_ising()
    n = f.qubo.num_variables
    for mask in range(0, 1 << n, 37):
        bits = [(mask >> i) & 1 for i in range(n)]
        label = "".join(str(b) for b in reversed(bits))
        e = Statevector.from_label(label).expectation_value(op).real + offset
        assert abs(e - f.qubo.energy(bits)) < 1e-9
        assert f.ising.energy(bits) == f.qubo.energy(bits)


def test_qubit_budget_reports_unsupported_size_without_dropping_constraints():
    _, p = load("scenario_g_triple_capacity")
    f = build_quantum_formulation(p, QuantumConfig(qubit_budget=9))
    assert f.status is FormulationStatus.UNSUPPORTED_SIZE and f.qubo is None and f.ising is None
    assert f.required_qubits == 10
    assert any(c.encoding is Encoding.CAPACITY_SLACK for c in f.constraints)  # still described, not dropped
    with pytest.raises(ValueError):
        validate_formulation(f)


def test_empty_formulation_when_nothing_is_placeable():
    p = build_placement_problem(build_model([raw_node("src"), raw_node("n1", cpu="1")], [cand("big", "4")]))
    f = build_quantum_formulation(p)
    assert f.status is FormulationStatus.EMPTY and f.assignment_vars == ()


def test_qubo_to_ising_is_exact_substitution():
    from qplace.quantum_formulation import Qubo
    q = Qubo(3, {0: -1, 2: 5}, {(0, 1): 4, (1, 2): -2}, 7)
    ising = qubo_to_ising(q)
    for bits in product([0, 1], repeat=3):
        assert ising.energy(bits) == q.energy(bits)
