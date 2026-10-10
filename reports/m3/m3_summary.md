# M3 summary: Quantum Placement Optimization (QAOA) and Qollab compatibility

**MILESTONE 3 STATUS: PASS** (closed 2026-10-10). Gate 15, the last open gate, is met: the project owner ran the Qollab demo in the hosted runtime on the built-in simulator and on IonQ's remote simulator (Aria 1 noise model). Both runs sampled the verified 2/2 placement (84/256 and 83/256 shots; random guessing gives 12.5%). The agents did not repeat the hosted runs; the owner supplied the results. They are recorded with consistency checks in [`docs/m3/hosted-evidence.md`](../../docs/m3/hosted-evidence.md). The first hosted attempt failed on a `__future__` import, which was fixed in `a82dc48` (§E).

| Metric | Value |
|---|---|
| Tests | 347 passed, 0 failed, 0 skipped (M1 130 + M2 100 + M3 117), Python 3.14.2. 343 also passed on 3.13.6 before the hotfixes |
| QUBO correctness | exhaustively validated on all 10 instances and 120 random problems. The reviewer's independent fuzzing covered 2,648 instances with 0 counterexamples |
| QAOA | real Qiskit circuits, p = 1 and p = 2, on 10 instances, 5 seeds × 1024 shots each (local noiseless simulator) |
| Best verified QAOA sample vs exact | the same full M2 objective on 10/10 instances |
| QAOA vs random guessing (p = 2) | better than uniform on every instance where they differ: 2.3× to 15.9× |
| QAOA vs random guessing (p = 1) | **worse** than uniform on scenario G (0.32×), about equal on M1 fixture C (1.02×) |
| Qollab demo (hosted) | **built-in simulator: 84/256 shots placed 2/2. IonQ simulator (Aria 1 noise): 83/256.** One `backend.run` per run. Results supplied by the project owner |
| Independent review | 2 material findings and 4 minor findings fixed with regression tests. Other items documented. No critical findings |

---

## A. Executive summary

**Built:**

- QUBO and Ising formulation of the M2 placement problem. Every hard constraint is encoded exactly, with a lossless gcd reduction and no rounding.
- An exhaustive correctness gate that must pass before QAOA runs.
- A hand-built QAOA circuit, cross-checked against Qiskit's `qaoa_ansatz`.
- Training with `StatevectorEstimator` + COBYLA (`qiskit_optimization.optimizers`), and sampling with `StatevectorSampler`.
- Little-endian decoding. Every sample is checked by M2's `verify_placement` and scored with M2's `evaluate_objective`.
- A `SolverResult(solver="qaoa", optimality_proven=False)`, produced only when a verified sample exists.
- Six artifact types with schemas.
- A standalone one-job Qollab demo.

**Findings:**

- On every instance, QAOA sampled a verified placement as good as the exact solver's, at both p = 1 and p = 2.
- On the greedy trap it sampled the 2/2 placement that greedy misses: 33% of shots at p = 2, against 12.5% for random guessing.
- At p = 1, results are weak. On the slack-encoded stress test (G), QAOA does worse than random guessing.
- The classical exact solver is faster by orders of magnitude and proves optimality.
- **No quantum advantage is claimed.**

**M4 readiness:** the interfaces are ready (§I).

| # | Gate | Result |
|---|---|---|
| 1 | M1/M2 suite passes | met (230 of 343 tests) |
| 2 | Existing artifacts unchanged | met: M1 `--check` 6/6, M2 `--check` 27/27; `git diff HEAD -- src/qplace` (M1/M2 files) empty |
| 3 | Deterministic QUBO construction | met: `qubo.json` / `ising_hamiltonian.json` byte-checked |
| 4 | Encoded constraints correct | met (§B proof + enumeration) |
| 5 | Exhaustive tiny-instance tests | met (10 fixtures + 120 random) |
| 6 | Ising conversion correct | met: exact on every state; matches `qiskit_optimization` `to_ising` |
| 7 | Genuine QAOA circuit executes | met (local Qiskit simulators) |
| 8 | Bitstrings decode correctly | met (`test_bit_order_is_little_endian`; reviewer's expectation cross-check) |
| 9 | Feasible claims pass `verify_placement` | met |
| 10 | M2 objective used for comparison | met |
| 11 | Greedy/exact preserved | met |
| 12 | Settings and outcomes recorded | met (`artifacts/m3`) |
| 13 | Quantum never claims optimality | met |
| 14 | Demo uses selected backend, ≤ 1 job | met locally (mock and BasicSimulator injection) |
| **15** | **Hosted Qollab built-in simulator exercised** | **met**: owner-supplied hosted runs on built-in and IonQ simulators (`docs/m3/hosted-evidence.md`) |
| 16 | Success / failure / uncertainty distinguished | met |
| 17 | Documentation complete | met (`docs/m3/*`) |
| 18 | No unresolved critical review finding | met |
| 19 | Entire suite passes | met |
| 20 | No Kubernetes mutation | met (safety tests on `src/qplace` and `qollab/`) |

---

## B. Quantum formulation

Full detail is in `docs/m3/quantum-formulation.md`.

**Variables:**

- One qubit per M2 variable `x[pod,node]`, in M2 order.
- Pairs that are individually infeasible are removed first (they can never be 1).
- Slack qubits come after the assignment qubits.

**QUBO:**

```
minimize  −Σ x_i
        + P · Σ_{same pod, i<j} x_i x_j                                (at most one destination)
        + P · Σ_{violating pairs} x_i x_j                              (capacity, when pairwise-exact by subset enumeration)
        + P · (Σ a′_i x_i + Σ w_k s_k − C′)²                           (capacity, otherwise; a′ = a/g, C′ = ⌊C/g⌋)
P = number of assignment variables
```

**Proof sketch:**

- A feasible `x` with its best slack has energy `−|x|`.
- Every violation adds `≥ P`, so an infeasible state has energy `≥ −V + P = 0`.
- The optimum `−k* ≤ −1`.
- So every global minimum is a feasible maximum-cardinality placement, and every such placement is a minimum.
- `validate_formulation` checks all of this by enumerating every state, judged in **original** integer units.

**Ising:** `x = (1 − z)/2`. The coefficients are exact rationals. Qubit 0 is the rightmost character of a Pauli label.

| Instance | Assignment + slack qubits | Encodings used | k* |
|---|---|---|---|
| scenario_b_greedy_trap | 3 + 0 | at-most-one; node-a CPU pairwise-exact | 2 |
| scenario_g_triple_capacity (new) | 8 + 2 | node-big CPU via slack (a′ = 1, C′ = 2, w = 1, 1); node-small CPU pairwise | 3 |
| other 8 instances | 1–8 + 0 | at-most-one, redundant, pairwise-exact | = M2 exact |

The QUBO is the **primary** objective only. M2's leximin balance and tie-break are applied by scoring verified samples with `evaluate_objective`. The QUBO energy is never reported as the M2 objective.

---

## C. QAOA implementation

| Item | Value |
|---|---|
| Packages | qiskit 2.5.2, qiskit-optimization 0.7.0, numpy 2.5.3, scipy 1.18.1 (optional extra `quantum`) |
| Circuit | `H^n`, then per layer: `RZ(2γh_i/s)`, `RZZ(2γJ_ij/s)`, `RX(2β)`; `measure_all`. `s = max |coef|` |
| Training | exact `⟨H/s⟩` via `StatevectorEstimator`; start = best of a 12 × 12 grid; `COBYLA(maxiter=150)` |
| Sampling | `StatevectorSampler(default_shots=1024, seed ∈ {11, 22, 33, 44, 55})` |
| Depths | p = 1 and p = 2 for every instance |
| Greedy-trap circuit | 3 qubits. p = 1: depth 6, 2 RZZ, 183 estimator evaluations. p = 2: depth 10, 4 RZZ, 254 evaluations |
| Demo angles (p = 2) | γ = (0.40440, 0.44838), β = (1.27430, 1.37964). Observational, from `qaoa_experiment.json` |
| Qollab circuit | same unitary, with RZZ written as CX·RZ·CX. Transpiled for BasicSimulator: depth 16 |

---

## D. Experimental results

All results are from local noiseless simulation. The QAOA rates are seed-free probabilities from the exact output distribution. "Uniform" is the share of all bitstrings that verify and reach the exact count.

| Instance | M2 assessment | Greedy | Exact | QAOA best (p=1 / p=2) | P(opt) p=1 | P(opt) p=2 | Uniform | p=2 depth / RZZ | p=2 train s |
|---|---|---|---|---|---|---|---|---|---|
| **scenario_b_greedy_trap** | MODELED_FEASIBLE | **1** | **2** | **2 / 2** | 0.154 | 0.333 | 0.125 | 10 / 4 | 0.26 |
| scenario_a_feasible | MODELED_FEASIBLE | 3 | 3 | 3 / 3 | 1.000 | 0.972 | 0.125 | 8 / 6 | 0.45 |
| scenario_c_aggregate_shortage | MODELED_BLOCKED | 2 | 2 | 2 / 2 | 0.135 | 0.578 | 0.094 | 14 / 18 | 0.50 |
| scenario_d_eligibility | MODELED_FEASIBLE | 3 | 3 | 3 / 3 | 1.000 | 1.000 | 0.125 | 6 / 0 | 0.14 |
| scenario_e_uncertain | REVIEW_REQUIRED | 1 | 1 | 1 / 1 | 1.000 | 1.000 | 0.500 | 6 / 2 | 0.12 |
| scenario_f_pod_slots | MODELED_BLOCKED | 1 | 1 | 1 / 1 | 1.000 | 1.000 | 0.500 | 6 / 2 | 0.10 |
| **scenario_g_triple_capacity** | MODELED_BLOCKED | 3 | 3 | 3 / 3 | **0.015** | 0.106 | 0.047 | 22 / 50 | 1.43 |
| fixture_a_healthy | MODELED_FEASIBLE | 4 | 4 | 4 / 4 | 0.861 | 0.996 | 0.062 | 8 / 8 | 0.62 |
| fixture_b_insufficient | REVIEW_REQUIRED | 1 | 1 | 1 / 1 | 1.000 | 1.000 | 0.500 | 6 / 0 | 0.09 |
| fixture_c_constraints | REVIEW_REQUIRED | 2 | 2 | 2 / 2 | 0.254 | 0.696 | 0.250 | 8 / 2 | 0.29 |

**Other measurements:**

- **Optimality gap** (exact − QAOA best): 0 pods on every instance and depth. The best sample's full M2 objective equals the exact solver's on all 10.
- **Feasible sample rate (mean over seeds):**
  - Scenario G: 0.06 at p = 1, 0.50 at p = 2.
  - The greedy trap: 0.94 and 0.92. That includes the empty placement, which is always feasible.
- **Runtimes on this machine:**
  - Classical greedy: about 0.02 ms. Exact: 0.02–0.35 ms.
  - QAOA training: 0.07–1.43 s. One 1024-shot sampling job: 2–9 ms.
  - The QAOA workflow costs 10³–10⁴× more than the classical solvers on these instances.
- **Scenario E (unknown inputs):**
  - Only the fully modeled pod gets a qubit.
  - `m2_assessment` stays `REVIEW_REQUIRED`, and all M2 conditions are carried into `quantum_problem.json`.
  - The comparison's `operational_assessment` is `REVIEW_REQUIRED`.
- **Experiment D (eligibility):** pairs that M1 forbids get no qubit (`test_no_forbidden_assignment_variables`).
- **Experiment E (stress):**
  - The verifier rejects every three-pods-on-node-big state.
  - Exhaustive checks confirm that the formulation's feasibility agrees with `verify_placement` on all 1024 states.

---

## E. Qollab validation

| Item | Status |
|---|---|
| Project | `qollab/main.py`, `quantum_demo.py`, `placement_data.py` (generated), `requirements.txt` (`qiskit>=1.0`), `README.md` |
| Runtime model | Qollab injects `backend` and calls `main()`. Source: public Qollab hackathon projects; Qollab's own docs page could not be retrieved |
| Local CPython run | **done**: `python main.py` falls back to BasicSimulator, labeled "LOCAL FALLBACK (not Qollab)". Result: 91/256 shots placed 2/2 (35.5%), random baseline 12.5%, best placement batch→node-b, indexer→node-a |
| Injected-backend tests | **done**, but mocks only: exactly 1 `run` call; a single circuit, never a list; refuses to substitute a backend under `emscripten`; reports failure when no sample is feasible; prints no advantage claim on uniform noise |
| Hosted run 1 (Qollab built-in simulator) | **FAILED before execution**: `SyntaxError: from __future__ imports must occur at the beginning of the file` (`<code>`, line 11, the `__future__` import in `main.py`). Fixed by removing both `__future__` imports (`qollab-runtime.md` §5). After the fix: 346 tests pass, including 3 new compatibility tests; all 6 + 27 + 61 artifact checks are ok; the local BasicSimulator demo ran (85/256 shots placed 2/2, 33.2%, random baseline 12.5%) |
| Qollab built-in simulator (hosted, after hotfix, 2026-10-10) | **SUCCESS**: backend `basic_simulator` (Qollab-selected), p = 2, 3 qubits, depth 16, 256 shots. 234 feasible, **84/256 (32.8%) placed 2/2**, random baseline 12.5%. Best: batch→node-b, indexer→node-a. Optimality NOT PROVEN |
| IonQ remote simulator (hosted, Aria 1 noise model, 2026-10-10) | **SUCCESS**: backend `ionq_simulator` (Qollab-selected), p = 2, depth 18, 256 shots, one job. 229 feasible, **83/256 (32.4%) placed 2/2**. Same best placement. A remote **simulator** with a hardware noise model, not hardware |
| 29-qubit report on IonQ | explained: the `ionq_simulator` target is 29 qubits wide, so transpilation pads the 3-qubit circuit; active qubits are {0, 1, 2}. Reproduced locally (width 29, depth 18). Console wording now prints logical, active and padded widths separately |
| Provenance | hosted runs performed and reported by the project owner, not repeated by an agent. All numbers are checked for internal consistency in `docs/m3/hosted-evidence.md` §3 |
| Resolved assumptions | Qollab's Pyodide runtime provides Qiskit; `backend` is injected into `main.py`'s globals; `main()` is invoked once per run |
| After the evidence | the console-wording change (active vs padded qubits) was made after these runs. Circuit, angles and decoding are unchanged. One confirming built-in run is requested before publication |

---

## F. Independent verification

- **Valid and invalid samples:** every distinct sampled bitstring is in `quantum_samples.json` with `raw_feasible`, `verified` and the verifier's error codes. Invalid shots are counted (`invalid_raw_shots`) and never repaired.
- **Verifier results:** each claimed best placement passed `verify_placement` (10 instances × 2 depths × 5 seeds).
- **QUBO exhaustive tests:**
  - `test_exhaustive_minima_are_feasible_max_cardinality` (10 fixtures);
  - `test_exhaustive_validation_on_random_problems` (120 problems, more than 60 supported);
  - the independent Qiskit `to_ising` cross-check;
  - slack-weight coverage for capacities 1–69.

---

## G. Test evidence

```
$ .venv/bin/python -m pytest -q                                   # Python 3.14.2
343 passed
$ <python 3.13.6 venv with qiskit>/bin/python -m pytest -q -p no:cacheprovider
343 passed
$ .venv/bin/python scripts/generate_m1_artifacts.py --check       # 6/6 ok
$ .venv/bin/python scripts/generate_m2_artifacts.py --check       # 27/27 ok
$ .venv/bin/python scripts/generate_m3_artifacts.py check         # 60/60 ok + qollab/placement_data.py ok
$ .venv/bin/python scripts/generate_m3_artifacts.py bogus         # exit 2 (unknown arguments rejected)
```

| Suite | Functions | Collected | Result |
|---|---|---|---|
| M1 + M2 (unchanged) | – | 230 | passed |
| `test_quantum_formulation.py` | 12 | 89 (69 slack-capacity cases + 10 fixtures) | passed |
| `test_quantum_qaoa.py` | 10 | 10 | passed |
| `test_quantum_experiment.py` | 14 | 14 | passed |
| **Total** | | **343** | 0 failed, 0 skipped |

---

## H. Review findings

The reviewer was a separate agent that did not write M3.

| # | Finding | Severity | Fix | Regression test |
|---|---|---|---|---|
| F1 | No random-guess baseline. "5/5 seeds reach the optimum" is near-certain even for noise. Scenario G p = 1 is below random | material | **fixed**: `uniform_random_baseline`, `ratio_to_uniform` and `better_than_uniform` in every comparison; docs table; limitation stated | `test_review_f1_uniform_random_baseline_is_reported_and_correct` |
| F2 | The demo's "Quantum sample beat greedy" fired on pure noise | material | **fixed**: line removed. The demo prints the random-guess baseline and states "not an advantage" | `test_review_f2_demo_makes_no_advantage_claim_on_noise` |
| F3 | "Feasible" counts include the empty placement; the demo's "VERIFIED" wording overstated what was checked | minor | **fixed**: `empty_placement_shots` added; the demo labels feasibility "(incl. empty placement)" and names its check | `test_review_f3_empty_placement_is_counted_separately` |
| F4 | `exact_probability_of_optimal_count` used the formulation's own feasibility check | minor | **fixed**: it now uses `verify_placement` | covered by F1 test + existing equivalence test |
| F5 | `solver_result` came from seed 11 while the best sample came from any seed; the demo's ranking is simpler than M2's | minor | **fixed**: the result is built from the best seed. The demo's ranking is documented (one optimal placement on the demo instance) | `test_review_f5_solver_result_comes_from_the_best_seed` |
| F6 | Runtime assumptions: double job if run as `__main__` outside Pyodide; injection location; unpinned Qiskit; upload hygiene | document | documented (`docs/m3/limitations.md`, `qollab-runtime.md`); explicit upload file list | – |
| F7 | Slack penalties stiffen the scaled landscape | document | documented | – |

**No findings in these areas:**

- QUBO math and the penalty proof;
- hard-constraint completeness;
- variable↔qubit mapping;
- endianness (the reviewer matched the estimator against a decoded expectation to 1e-15);
- surrogate vs M2 objective;
- invalid-sample handling;
- M1/M2 preservation.

**Remaining risks:**

- Hosted evidence comes from one run per backend (256 shots each). It shows the pipeline works, not a statistically precise success rate.
- The post-evidence console-wording change needs one confirming hosted run.

---

## I. M4 handoff

```python
from qplace.quantum_formulation import build_quantum_formulation, validate_formulation, QuantumConfig
from qplace.quantum_qaoa import train_qaoa, sample_local, build_qaoa_circuit, bind, QAOAConfig
from qplace.quantum_decode import decode_quantum_samples, to_solver_result, bits_from_key
from qplace.quantum_experiment import run_experiment, ARTIFACTS

form = build_quantum_formulation(problem, QuantumConfig(qubit_budget=12))  # status SUPPORTED / EMPTY / UNSUPPORTED_SIZE
evidence = validate_formulation(form)          # must be .valid before QAOA
training = train_qaoa(form.ising, QAOAConfig(reps=2))
counts, _ = sample_local(form.ising, training, shots=1024, seed=11)   # or any backend: one job
decoded = decode_quantum_samples(model, problem, form, counts, exact_count)
result = to_solver_result(problem, decoded, config, seconds)          # None when no verified sample
```

| Item | Detail |
|---|---|
| Schemas | `src/qplace/schemas/m3-*-1.0.schema.json` (six) |
| CLI integration points for M4 | formulation status → a `quantum: skipped (UNSUPPORTED_SIZE)` message; `comparison_results.json` → report tables; `QuantumStatus` → the success, failure and uncertainty wording |
| Execution config | `QuantumConfig.qubit_budget`; `QAOAConfig(reps, seed, shots, grid_points, maxiter, sampling_seeds)` |
| Safe failure | `NO_FEASIBLE_SAMPLE` → no `SolverResult`; `UNSUPPORTED_SIZE` / `EMPTY` → QAOA skipped; the M2 exact result is always the authority, and a quantum output never changes the M2 assessment |

---

## J. Git state

| Item | Value |
|---|---|
| Starting commit | `0a7f06cb7298879e4875f0d85b762071e4bb4f44` (= `origin/main`, clean) |
| Final commit | given in the final response (a hash cannot be written into the file it identifies) |
| Author | repo-local `Edidiong Etuk <edidiongmichael08@gmail.com>`; global config not changed |
| Push | fast-forward to `origin/main` as `edeediong`; `gh` restored to `edidiong-mono` afterwards |
