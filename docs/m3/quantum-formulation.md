# M3 quantum formulation

**In short:** each M2 decision variable `x[pod,node]` becomes one qubit. The QUBO rewards every placed pod with −1. It penalizes the "at most one node per pod" rule and every CPU, memory and pod-slot limit with an exact integer penalty `P`. Every QUBO is checked against all of its bitstrings before QAOA runs. On all 10 benchmark instances the lowest-energy states are exactly the feasible placements with the most pods.

Code: `src/qplace/quantum_formulation.py`. Artifacts: `artifacts/m3/<fixture>/{quantum_problem,qubo,ising_hamiltonian}.json`.

## 1. Variables

| Qubits | Source |
|---|---|
| `0 … V−1` (assignment) | M2 `problem.variables`, in the same order, minus *individually infeasible* pairs (pod request > node remaining in any resource). Every feasible solution sets those pairs to 0, so dropping them changes nothing. They are listed in `eliminated_variables` |
| `V … V+S−1` (slack) | binary slack for capacity constraints that need it (§3.3) |

Only M2 variables become qubits, and M2 variables exist only for M1-`ELIGIBLE` pairs on usable destinations. `UNKNOWN` and `INELIGIBLE` pairs never get a qubit (`test_no_forbidden_assignment_variables`).

## 2. Objective

`minimize  −Σ x_i  +  penalties`

This is M2's **primary** objective only: the number of pods placed. M2's secondary leximin balance and its tie-break are not quadratic, so they are not in the QUBO. Every sampled assignment is scored with M2's real `evaluate_objective`. The QUBO energy is never reported as the M2 objective.

## 3. Constraints (all exact, integer, no rounding)

### 3.1 At most one destination per pod

For a pod's variables `i < j`: add `P·x_i·x_j`. This is zero exactly when at most one is 1.

### 3.2 Capacity `Σ a_i x_i ≤ C` for each (destination, resource ∈ {cpu, memory, pods})

| Case | Encoding | Extra qubits |
|---|---|---|
| `Σ a_i ≤ C` (cannot be violated) | `CAPACITY_REDUNDANT`: nothing added | 0 |
| a subset violates the limit **iff** it contains a violating pair. This is checked by enumerating **every** subset (≤16 variables) | `CAPACITY_PAIRWISE_EXACT`: `P·x_i·x_j` for each violating pair | 0 |
| otherwise (for example, any pair fits but three do not) | `CAPACITY_SLACK` (§3.3) | `⌈log₂(C′+1)⌉` |

Pairwise conflicts are used **only** when the enumeration proves them exact. A limit that three pods can break while every pair fits always gets the slack encoding (scenario G).

### 3.3 Slack encoding and resource normalization

- `g = gcd(a_i)`, `a′_i = a_i / g`, `C′ = ⌊C / g⌋`.
- This is lossless: the left side is a multiple of `g`, so `Σa x ≤ C ⇔ Σa′ x ≤ C′`.
- This turns 1000-millicore requests into units of 1, and Mi-aligned memory into Mi units.
- Slack weights `1, 2, 4, …, 2^(K−2), C′ − (2^(K−1) − 1)` with `K = bit_length(C′)` represent exactly the integers `0…C′` (`test_slack_weights_cover_exactly_zero_to_capacity`).
- Term added: `P·(Σ a′_i x_i + Σ w_k s_k − C′)²`, expanded with `v² = v`.

Large unreduced quantities (for example memory limits that aren't Mi-aligned) need many slack bits. The qubit budget then rejects the instance instead of approximating it.

## 4. Penalty and proof

Let `P = V`, the number of assignment variables.

1. A feasible assignment with `k` placed pods, plus its correct slack, has energy `−k`, and no completion has lower energy (`feasible_energy_exact`).
2. A violated pairwise term adds `≥ P`. A violated slack constraint adds `P·(≥1)²`, because `Σa′x + s − C′ ≥ 1` when `Σa′x > C′` and `s ≥ 0`.
3. So an infeasible state has energy `≥ −V + P = 0`.
4. If `V ≥ 1`, some single assignment is feasible, so the optimum is `−k* ≤ −1 < 0`.

Every global minimum is therefore a feasible maximum-cardinality placement. Every such placement reaches the minimum.

## 5. Exhaustive correctness gate (`validate_formulation`)

For every instance, all `2^(V+S)` states are enumerated (up to 1024 here). The gate checks:

| Check | Meaning |
|---|---|
| `minima_all_feasible_max_cardinality` | every lowest-energy state decodes to a feasible placement with `k*` pods |
| `every_max_assignment_reaches_minimum` | no optimal placement is missing from the minima |
| `feasible_energy_exact` | min over slack of `E(x,s) = −|x|` for every feasible `x` |
| `infeasible_strictly_above_minimum` | no infeasible state ties or beats the optimum |
| `ising_matches_qubo` | the Ising energy equals the QUBO energy on every state, exactly (`Fraction`) |

Feasibility here is checked in **original** integer units, not reduced ones. `k*` is also compared with M2's exact solver. QAOA runs only if every check passes. It also passed on 120 seeded random problems (`test_exhaustive_validation_on_random_problems`).

| Fixture | Assignment + slack qubits | Encodings | k* = M2 exact |
|---|---|---|---|
| scenario_a_feasible | 6 + 0 | at-most-one, redundant | 3 |
| scenario_b_greedy_trap | 3 + 0 | at-most-one, pairwise-exact (node-a CPU) | 2 |
| scenario_c_aggregate_shortage | 6 + 0 | pairwise-exact | 2 |
| scenario_d_eligibility | 3 + 0 | at-most-one, redundant | 3 |
| scenario_e_uncertain | 2 + 0 | at-most-one | 1 |
| scenario_f_pod_slots | 2 + 0 | pairwise-exact (pod slots = 1) | 1 |
| **scenario_g_triple_capacity** | **8 + 2** | **slack** (node-big CPU: 1000m ×4, cap 2500m → a′=1, C′=2, w=(1,1)) | 3 |
| fixture_a_healthy (M1) | 8 + 0 | redundant | 4 |
| fixture_b_insufficient (M1) | 1 + 0 (2 eliminated) | redundant | 1 |
| fixture_c_constraints (M1) | 3 + 0 | redundant | 2 |

## 6. Ising mapping

The substitution is `x_i = (1 − z_i)/2`, with `z_i = +1` for `|0⟩`:

```
h_i    = −a_i/2 − Σ_j b_ij/4
J_ij   =  b_ij/4
offset =  c + Σ a_i/2 + Σ b_ij/4
```

The coefficients are exact rationals (`ising_hamiltonian.json`). In Qiskit Pauli labels, qubit 0 is the rightmost character. An independent conversion with `qiskit_optimization.QuadraticProgram.to_ising` gives the same energies (`test_ising_matches_independent_qiskit_optimization_conversion`).

Greedy trap example (P = 3):

- QUBO: `−x0 −x1 −x2 + 3x0x1 + 3x0x2`
- Ising: `h = (−1, −¼, −¼)`, `J01 = J02 = ¾`

## 7. Qubit budget

`QuantumConfig.qubit_budget = 12` by default. If an instance needs more qubits, it gets the status `UNSUPPORTED_SIZE`: no QUBO, no QAOA, and its constraints are still listed. If no variable survives elimination, the status is `EMPTY`.
