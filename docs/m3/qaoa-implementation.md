# M3 QAOA implementation

**In short:** a hand-built QAOA circuit on the validated Ising model. The angles are trained classically: exact expectation values from Qiskit's `StatevectorEstimator`, a deterministic grid start, then COBYLA. The trained circuit is sampled with `StatevectorSampler`. Every sample is decoded little-endian and checked with M2's independent verifier.

Code: `src/qplace/quantum_qaoa.py`, `src/qplace/quantum_decode.py`.

## 1. Versions (tested)

| Package | Version |
|---|---|
| Python | 3.14.2 |
| qiskit | 2.5.2 |
| qiskit-optimization | 0.7.0 (`qiskit_optimization.optimizers.COBYLA`; `QuadraticProgram` for an independent Ising cross-check) |
| numpy / scipy | 2.5.3 / 1.18.1 |

Install with `pip install -e '.[quantum]'`. The M1/M2 core still depends only on `jsonschema`.

## 2. Circuit

For the scaled Hamiltonian `H/s`, with `s = max |h_i|, |J_ij|` (the same eigenvectors, so one angle range works for every instance):

```
H on every qubit                       |+>^n
for layer l = 1..p:
    RZ(2·γ_l·h_i/s)        on qubit i       = exp(−i γ_l h_i Z_i / s)
    RZZ(2·γ_l·J_ij/s)      on (i, j)        = exp(−i γ_l J_ij Z_i Z_j / s)
    RX(2·β_l)              on every qubit   = exp(−i β_l X_i)
measure_all
```

The parameters are `gamma[0..p−1]` and `beta[0..p−1]` (`ParameterVector`).

- A test compares this circuit with Qiskit's own `qaoa_ansatz`: identical expectation values at the same angles.
- The Qollab copy writes RZZ as `CX · RZ · CX`. A test checks that its unitary is identical to the repository circuit.

## 3. Training (classical, local)

| Setting | Value |
|---|---|
| Cost | exact `⟨H/s⟩` from `StatevectorEstimator` (V2 primitive). No shot noise during training |
| Start | best point of a 12 × 12 grid, `γ ∈ (0, π]`, `β ∈ (0, π/2]`, repeated across layers for p = 2 |
| Optimizer | `COBYLA(maxiter=150)` from `qiskit_optimization.optimizers` |
| Depths | p = 1 and p = 2 for every instance |
| Determinism | there are no random elements, and the same inputs give the same angles on this machine (`test_training_is_repeatable_and_does_not_worsen_start`). Floating-point results can differ on other platforms, so the trained angles are stored in *observational* artifacts |

## 4. Sampling

`StatevectorSampler(default_shots=1024, seed=s)` runs for 5 sampling seeds `s ∈ {11, 22, 33, 44, 55}` at each depth. This is noiseless shot sampling of the exact state. The exact output distribution (`Statevector.probabilities_dict`) is also recorded, which gives a seed-free probability of sampling an optimal placement.

## 5. Decoding and selection

1. Each counts key is little-endian: qubit `i` is `key[-1-i]`. A test fails if the order is reversed (`test_bit_order_is_little_endian`).
2. Assignment qubits map to `(pod, node)` pairs. Slack qubits are ignored after decoding.
3. Each distinct bitstring gets three values:
   - QUBO energy;
   - `raw_feasible`: the encoded constraints, in original units;
   - `verified`: `verify_placement(model, pairs)`, which reads only M1.

   A test shows that raw feasibility and the verifier agree on every state of three instances.
4. The best sample is the **verified** one that M2's `Objective.better_than` ranks highest. Low energy alone never qualifies a sample.
5. If no verified sample exists, the status is `NO_FEASIBLE_SAMPLE` and no `SolverResult` is produced. No repair or classical substitution is applied.
6. A verified best sample becomes a `SolverResult(solver="qaoa", optimality_proven=False)`. Its objective comes from `evaluate_objective`.
