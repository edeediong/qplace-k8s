"""M3 quantum formulation: QUBO and Ising model of an M2 PlacementProblem.

Pure Python (no Qiskit) so it can be checked exhaustively and reused in tests.

Encoding summary (proofs in docs/m3/quantum-formulation.md):

- One binary variable per M2 decision variable ``(pod, node)``. Variables a pod
  can never use alone (request > remaining) are eliminated first; this removes
  only assignments that every feasible solution already sets to 0.
- Objective: minimize ``-sum(x)`` (one unit of reward per placed pod).
- At most one destination per pod: ``P * x_i * x_j`` for every pair of that
  pod's variables. Zero exactly when at most one is set.
- Capacity ``sum a_i x_i <= C`` per (destination, resource), in exact integers:
  * redundant (``sum a_i <= C``): no term needed;
  * pairwise-exact (checked by enumerating every subset of the constraint's
    variables: a subset violates the constraint iff it contains a violating
    pair): ``P * x_i * x_j`` for each violating pair;
  * otherwise slack encoding ``P * (sum a'_i x_i + sum w_k s_k - C')^2`` with
    ``a' = a / g``, ``C' = floor(C / g)``, ``g = gcd(a)`` (lossless) and a
    bounded binary slack whose weights sum to exactly ``C'``.
- Penalty ``P = number of assignment variables``. Every violated term costs
  at least ``P``; a feasible placement of ``k >= 1`` pods costs ``-k``; an
  infeasible state costs at least ``-V + P = 0 > -k*``. Hence every global
  minimum is a feasible maximum-cardinality placement.

No rounding is ever applied. If a problem needs more qubits than the budget,
the formulation is ``UNSUPPORTED_SIZE`` instead of dropping a constraint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from itertools import combinations
from math import gcd
from typing import Any

from qplace.placement_problem import PlacementProblem

RESOURCES = ("cpu_millicores", "memory_bytes", "pods")
MAX_PAIRWISE_CHECK_VARS = 16  # subsets enumerated to prove pairwise exactness


class FormulationStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    EMPTY = "EMPTY"  # no assignment variable survives; nothing to optimize
    UNSUPPORTED_SIZE = "UNSUPPORTED_SIZE"


class Encoding(StrEnum):
    AT_MOST_ONE_PAIRWISE = "AT_MOST_ONE_PAIRWISE"
    CAPACITY_REDUNDANT = "CAPACITY_REDUNDANT"
    CAPACITY_PAIRWISE_EXACT = "CAPACITY_PAIRWISE_EXACT"
    CAPACITY_SLACK = "CAPACITY_SLACK"


@dataclass(frozen=True)
class QuantumConfig:
    qubit_budget: int = 12


@dataclass(frozen=True)
class ConstraintEncoding:
    name: str
    encoding: Encoding
    variables: tuple[int, ...]
    coefficients: tuple[int, ...]  # original units
    capacity: int | None  # original units (None for at-most-one)
    scale: int | None  # gcd used for lossless reduction
    reduced_coefficients: tuple[int, ...]
    reduced_capacity: int | None
    penalty_pairs: tuple[tuple[int, int], ...]
    slack_qubits: tuple[int, ...]
    slack_weights: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "encoding": str(self.encoding),
            "variables": list(self.variables),
            "coefficients": list(self.coefficients),
            "capacity": self.capacity,
            "scale": self.scale,
            "reduced_coefficients": list(self.reduced_coefficients),
            "reduced_capacity": self.reduced_capacity,
            "penalty_pairs": [list(p) for p in self.penalty_pairs],
            "slack_qubits": list(self.slack_qubits),
            "slack_weights": list(self.slack_weights),
        }


@dataclass(frozen=True)
class Qubo:
    """E(x) = constant + sum linear[i] x_i + sum quadratic[(i,j)] x_i x_j, i < j, integer coefficients."""

    num_variables: int
    linear: dict[int, int]
    quadratic: dict[tuple[int, int], int]
    constant: int

    def energy(self, bits: tuple[int, ...] | list[int]) -> int:
        e = self.constant
        for i, a in self.linear.items():
            if bits[i]:
                e += a
        for (i, j), b in self.quadratic.items():
            if bits[i] and bits[j]:
                e += b
        return e

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_variables": self.num_variables,
            "constant": self.constant,
            "linear": [{"i": i, "coefficient": a} for i, a in sorted(self.linear.items())],
            "quadratic": [{"i": i, "j": j, "coefficient": b} for (i, j), b in sorted(self.quadratic.items())],
        }


@dataclass(frozen=True)
class Ising:
    """H(z) = offset + sum h[i] z_i + sum J[(i,j)] z_i z_j with z_i = 1 - 2 x_i (|0> -> z=+1 -> x=0)."""

    num_qubits: int
    h: dict[int, Fraction]
    J: dict[tuple[int, int], Fraction]
    offset: Fraction

    def energy(self, bits: tuple[int, ...] | list[int]) -> Fraction:
        z = [1 - 2 * b for b in bits]
        e = self.offset
        for i, c in self.h.items():
            e += c * z[i]
        for (i, j), c in self.J.items():
            e += c * z[i] * z[j]
        return e

    def pauli_terms(self) -> list[tuple[str, float]]:
        """Qiskit Pauli labels (qubit 0 is the rightmost character) with float coefficients."""
        out = []
        for i, c in sorted(self.h.items()):
            label = ["I"] * self.num_qubits
            label[self.num_qubits - 1 - i] = "Z"
            out.append(("".join(label), float(c)))
        for (i, j), c in sorted(self.J.items()):
            label = ["I"] * self.num_qubits
            label[self.num_qubits - 1 - i] = "Z"
            label[self.num_qubits - 1 - j] = "Z"
            out.append(("".join(label), float(c)))
        return out

    def to_dict(self) -> dict[str, Any]:
        def frac(f: Fraction) -> str:
            return f"{f.numerator}/{f.denominator}"

        return {
            "num_qubits": self.num_qubits,
            "convention": "z_i = 1 - 2 x_i; Pauli label qubit 0 = rightmost character",
            "offset": frac(self.offset),
            "h": [{"i": i, "coefficient": frac(c)} for i, c in sorted(self.h.items())],
            "J": [{"i": i, "j": j, "coefficient": frac(c)} for (i, j), c in sorted(self.J.items())],
            "pauli_terms": [{"label": lbl, "coefficient": float(c)} for lbl, c in self.pauli_terms()],
        }


@dataclass(frozen=True)
class QuantumFormulation:
    status: FormulationStatus
    config: QuantumConfig
    assignment_vars: tuple[tuple[str, str], ...]  # qubit i <-> (pod, node), i < len(assignment_vars)
    eliminated_vars: tuple[tuple[str, str, str], ...]  # (pod, node, reason)
    slack_owner: tuple[str, ...]  # qubit (len(assignment_vars) + k) belongs to this constraint
    constraints: tuple[ConstraintEncoding, ...]
    capacity_data: tuple[tuple[str, str, int, tuple[tuple[int, int], ...]], ...]  # (node, resource, cap, ((var, a),..))
    penalty: int
    qubo: Qubo | None
    ising: Ising | None
    required_qubits: int
    conditions: tuple[str, ...] = field(default=())

    @property
    def num_assignment_qubits(self) -> int:
        return len(self.assignment_vars)

    @property
    def num_slack_qubits(self) -> int:
        return len(self.slack_owner)

    def decode(self, bits: tuple[int, ...] | list[int]) -> list[tuple[str, str]]:
        """Assignment pairs selected by a bit vector (index = qubit). Slack bits are ignored."""
        return [self.assignment_vars[i] for i in range(self.num_assignment_qubits) if bits[i]]

    def assignment_feasible(self, bits: tuple[int, ...] | list[int]) -> bool:
        """Original-unit check of the encoded constraints, independent of the QUBO."""
        pods = [self.assignment_vars[i][0] for i in range(self.num_assignment_qubits) if bits[i]]
        if len(pods) != len(set(pods)):
            return False
        for _node, _res, cap, terms in self.capacity_data:
            if sum(a for v, a in terms if bits[v]) > cap:
                return False
        return True


def _slack_weights(cap: int) -> list[int]:
    """Bounded binary slack: weights 1, 2, 4, ..., last adjusted so they sum to exactly ``cap``."""
    if cap <= 0:
        return []
    k = cap.bit_length()
    weights = [1 << i for i in range(k - 1)]
    weights.append(cap - (sum(weights)))
    return weights


def _pairwise_exact(coeffs: list[int], cap: int) -> bool:
    """True iff every violating subset contains a violating pair (checked over all subsets)."""
    n = len(coeffs)
    bad_pairs = {(i, j) for i, j in combinations(range(n), 2) if coeffs[i] + coeffs[j] > cap}
    for mask in range(1 << n):
        members = [i for i in range(n) if mask >> i & 1]
        violates = sum(coeffs[i] for i in members) > cap
        has_bad_pair = any((i, j) in bad_pairs for i, j in combinations(members, 2))
        if violates != has_bad_pair:
            return False
    return True


def build_quantum_formulation(problem: PlacementProblem, config: QuantumConfig = QuantumConfig()) -> QuantumFormulation:
    cands = {c.pod: c for c in problem.usable_candidates}
    dests = {d.node: d for d in problem.usable_destinations}

    assignment: list[tuple[str, str]] = []
    eliminated: list[tuple[str, str, str]] = []
    for pod, node in problem.variables:
        c, d = cands[pod], dests[node]
        reasons = []
        if c.cpu_millicores > d.cpu_millicores:  # type: ignore[operator]
            reasons.append("cpu")
        if c.memory_bytes > d.memory_bytes:  # type: ignore[operator]
            reasons.append("memory")
        if d.pod_slots < 1:  # type: ignore[operator]
            reasons.append("pods")
        if reasons:
            eliminated.append((pod, node, "INDIVIDUALLY_INFEASIBLE:" + ",".join(reasons)))
        else:
            assignment.append((pod, node))

    n_assign = len(assignment)
    penalty = max(n_assign, 1)
    constraints: list[ConstraintEncoding] = []
    capacity_data = []
    slack_owner: list[str] = []
    linear: dict[int, int] = {}
    quadratic: dict[tuple[int, int], int] = {}
    constant = 0

    def add_lin(i: int, v: int) -> None:
        linear[i] = linear.get(i, 0) + v

    def add_quad(i: int, j: int, v: int) -> None:
        key = (min(i, j), max(i, j))
        quadratic[key] = quadratic.get(key, 0) + v

    for i in range(n_assign):
        add_lin(i, -1)  # reward: one unit per placed pod

    by_pod: dict[str, list[int]] = {}
    for i, (pod, _node) in enumerate(assignment):
        by_pod.setdefault(pod, []).append(i)
    pair_penalties: set[tuple[int, int]] = set()
    for pod in sorted(by_pod):
        idx = by_pod[pod]
        pairs = tuple(combinations(idx, 2))
        pair_penalties.update(pairs)
        constraints.append(ConstraintEncoding(
            name=f"one:{pod}", encoding=Encoding.AT_MOST_ONE_PAIRWISE, variables=tuple(idx),
            coefficients=tuple(1 for _ in idx), capacity=1, scale=None, reduced_coefficients=(),
            reduced_capacity=None, penalty_pairs=pairs, slack_qubits=(), slack_weights=()))

    slack_terms: list[tuple[list[tuple[int, int]], int]] = []  # ([(qubit, coeff)], -C')
    for node in sorted(dests):
        d = dests[node]
        idx = [i for i, (_p, n) in enumerate(assignment) if n == node]
        if not idx:
            continue
        for resource in RESOURCES:
            cap = {"cpu_millicores": d.cpu_millicores, "memory_bytes": d.memory_bytes, "pods": d.pod_slots}[resource]
            coeff_of = {
                "cpu_millicores": lambda i: cands[assignment[i][0]].cpu_millicores,
                "memory_bytes": lambda i: cands[assignment[i][0]].memory_bytes,
                "pods": lambda i: 1,
            }[resource]
            terms = [(i, coeff_of(i)) for i in idx]
            capacity_data.append((node, resource, cap, tuple(terms)))
            nonzero = [(i, a) for i, a in terms if a > 0]
            name = f"cap:{node}:{resource}"
            if sum(a for _, a in nonzero) <= cap:
                constraints.append(ConstraintEncoding(name, Encoding.CAPACITY_REDUNDANT, tuple(i for i, _ in terms),
                                                      tuple(a for _, a in terms), cap, None, (), None, (), (), ()))
                continue
            coeffs = [a for _, a in nonzero]
            if len(nonzero) <= MAX_PAIRWISE_CHECK_VARS and _pairwise_exact(coeffs, cap):
                pairs = tuple((nonzero[x][0], nonzero[y][0]) for x, y in combinations(range(len(nonzero)), 2)
                              if coeffs[x] + coeffs[y] > cap)
                pair_penalties.update(pairs)
                constraints.append(ConstraintEncoding(name, Encoding.CAPACITY_PAIRWISE_EXACT,
                                                      tuple(i for i, _ in terms), tuple(a for _, a in terms), cap,
                                                      None, (), None, pairs, (), ()))
                continue
            g = 0
            for a in coeffs:
                g = gcd(g, a)
            reduced = [a // g for a in coeffs]
            rcap = cap // g  # lossless: LHS is a multiple of g
            weights = _slack_weights(rcap)
            first = n_assign + len(slack_owner)
            slack_q = tuple(range(first, first + len(weights)))
            slack_owner.extend([name] * len(weights))
            slack_terms.append(([(nonzero[k][0], reduced[k]) for k in range(len(nonzero))]
                                + list(zip(slack_q, weights)), -rcap))
            constraints.append(ConstraintEncoding(name, Encoding.CAPACITY_SLACK, tuple(i for i, _ in terms),
                                                  tuple(a for _, a in terms), cap, g, tuple(reduced), rcap, (),
                                                  slack_q, tuple(weights)))

    required = n_assign + len(slack_owner)
    conditions = tuple(f"{c.code}:{c.subject}" for c in problem.conditions)
    base = dict(config=config, assignment_vars=tuple(assignment), eliminated_vars=tuple(eliminated),
                slack_owner=tuple(slack_owner), constraints=tuple(constraints), capacity_data=tuple(capacity_data),
                penalty=penalty, required_qubits=required, conditions=conditions)
    if n_assign == 0:
        return QuantumFormulation(status=FormulationStatus.EMPTY, qubo=None, ising=None, **base)
    if required > config.qubit_budget:
        return QuantumFormulation(status=FormulationStatus.UNSUPPORTED_SIZE, qubo=None, ising=None, **base)

    for i, j in sorted(pair_penalties):
        add_quad(i, j, penalty)
    for terms, d0 in slack_terms:
        # P * (sum c_v v + d0)^2 with v^2 = v
        for v, c in terms:
            add_lin(v, penalty * (c * c + 2 * d0 * c))
        for (u, cu), (v, cv) in combinations(terms, 2):
            add_quad(u, v, penalty * 2 * cu * cv)
        constant += penalty * d0 * d0

    qubo = Qubo(required, {k: v for k, v in linear.items() if v}, {k: v for k, v in quadratic.items() if v}, constant)
    return QuantumFormulation(status=FormulationStatus.SUPPORTED, qubo=qubo, ising=qubo_to_ising(qubo), **base)


def qubo_to_ising(qubo: Qubo) -> Ising:
    """Exact substitution x_i = (1 - z_i) / 2."""
    h: dict[int, Fraction] = {}
    J: dict[tuple[int, int], Fraction] = {}
    offset = Fraction(qubo.constant)
    for i, a in qubo.linear.items():
        offset += Fraction(a, 2)
        h[i] = h.get(i, Fraction(0)) - Fraction(a, 2)
    for (i, j), b in qubo.quadratic.items():
        offset += Fraction(b, 4)
        h[i] = h.get(i, Fraction(0)) - Fraction(b, 4)
        h[j] = h.get(j, Fraction(0)) - Fraction(b, 4)
        J[(i, j)] = J.get((i, j), Fraction(0)) + Fraction(b, 4)
    return Ising(qubo.num_variables, {k: v for k, v in h.items() if v}, {k: v for k, v in J.items() if v}, offset)


# --------------------------------------------------------------------------- correctness gate


@dataclass(frozen=True)
class FormulationEvidence:
    states_enumerated: int
    max_feasible_pods: int
    min_energy: int
    minimum_states: int
    minima_all_feasible_max_cardinality: bool
    every_max_assignment_reaches_minimum: bool
    feasible_energy_exact: bool  # min over slack of E(x, s) == -|x| for every feasible x
    infeasible_strictly_above_minimum: bool
    min_infeasible_energy: int | None
    ising_matches_qubo: bool

    @property
    def valid(self) -> bool:
        return (self.minima_all_feasible_max_cardinality and self.every_max_assignment_reaches_minimum
                and self.feasible_energy_exact and self.infeasible_strictly_above_minimum
                and self.ising_matches_qubo)

    def to_dict(self) -> dict[str, Any]:
        return {**{k: getattr(self, k) for k in self.__dataclass_fields__}, "valid": self.valid}


def validate_formulation(form: QuantumFormulation, max_qubits: int = 20) -> FormulationEvidence:
    """Exhaustively check that the QUBO's global minima are exactly the feasible max-cardinality placements."""
    if form.qubo is None or form.ising is None:
        raise ValueError(f"no QUBO to validate (status {form.status})")
    n = form.qubo.num_variables
    if n > max_qubits:
        raise ValueError(f"{n} qubits exceeds exhaustive validation limit {max_qubits}")
    na = form.num_assignment_qubits
    best_by_x: dict[int, int] = {}
    min_energy: int | None = None
    min_states: list[int] = []
    ising_ok = True
    min_infeasible: int | None = None
    for mask in range(1 << n):
        bits = [(mask >> i) & 1 for i in range(n)]
        e = form.qubo.energy(bits)
        if ising_ok and form.ising.energy(bits) != e:
            ising_ok = False
        x = mask & ((1 << na) - 1)
        best_by_x[x] = e if x not in best_by_x else min(best_by_x[x], e)
        if not form.assignment_feasible(bits):
            min_infeasible = e if min_infeasible is None else min(min_infeasible, e)
        if min_energy is None or e < min_energy:
            min_energy, min_states = e, [mask]
        elif e == min_energy:
            min_states.append(mask)
    feasible_x = [x for x in range(1 << na) if form.assignment_feasible([(x >> i) & 1 for i in range(na)] + [0] * (n - na))]
    kstar = max(bin(x).count("1") for x in feasible_x)
    exact = all(best_by_x[x] == -bin(x).count("1") for x in feasible_x)
    minima_ok = all(
        form.assignment_feasible([(m >> i) & 1 for i in range(n)]) and bin(m & ((1 << na) - 1)).count("1") == kstar
        for m in min_states)
    reach = all(best_by_x[x] == min_energy for x in feasible_x if bin(x).count("1") == kstar)
    return FormulationEvidence(
        states_enumerated=1 << n, max_feasible_pods=kstar, min_energy=min_energy,  # type: ignore[arg-type]
        minimum_states=len(min_states), minima_all_feasible_max_cardinality=minima_ok,
        every_max_assignment_reaches_minimum=reach, feasible_energy_exact=exact,
        infeasible_strictly_above_minimum=min_infeasible is None or min_infeasible > min_energy,  # type: ignore[operator]
        min_infeasible_energy=min_infeasible, ising_matches_qubo=ising_ok)
