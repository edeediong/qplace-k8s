# M2 summary: Classical Placement Engine

**MILESTONE 2 STATUS: PASS.** All 18 gate conditions are met (§A). 230 tests pass (130 M1 + 100 M2), with 0 failures and 0 skips, on Python 3.14.2 and 3.13.6. The M1 sources, fixtures and reference artifacts are byte-unchanged. M3 can build on the contract in §H.

| Metric | Value |
|---|---|
| Tests passed / failed / skipped | 230 / 0 / 0 (M1 130, M2 100) |
| Exact vs exhaustive oracle | 300 seeded random problems in the suite: count, balance and assignment identical. The reviewer's separate brute force: 2,700 problems, 0 mismatches |
| Benchmark fixtures | 9 (6 M2 scenarios + 3 M1 fixtures); 27 artifacts, all schema-valid |
| Greedy vs exact | exact placed more in 1 of 9 fixtures (scenario B, gap 1). Equal elsewhere |
| Independent review | 4 findings fixed with regression tests, 1 script defect fixed, 1 documented. Nothing open |
| New dependencies | none |

---

## A. Executive summary

**Built:**

- A deterministic placement problem (binary `x[pod,node]` form) derived from the M1 model.
- A first-fit-decreasing greedy solver.
- An exact branch-and-bound solver with proof-preserving pruning and explicit limits.
- An independent verifier that uses only M1.
- An outcome/assessment layer that keeps uncertainty visible.
- Three new artifact types with JSON Schemas.

**M3 readiness:** ready. M3 consumes `PlacementProblem` / `placement_problem.json`, returns a `SolverResult` and is checked by `verify_placement`.

| # | Gate condition | Result | Evidence |
|---|---|---|---|
| 1 | M1 unchanged in behavior | met | `git diff 7cc9a8b -- src/qplace/{M1 modules} fixtures/m1 artifacts/m1` is empty; 130 M1 tests pass; M1 `--check` gives 6/6 ok |
| 2 | Deterministic problem | met | `test_problem_independent_of_input_order_and_does_not_mutate_model` |
| 3 | Greedy correct | met | `test_placement_greedy.py` (8) |
| 4 | Exact correct | met | `test_placement_exact.py` (10) |
| 5 | Exact matches brute force | met | `test_exact_matches_oracle_on_random_problems` (300 problems) |
| 6 | Verifier rejects invalid assignments | met | `test_placement_verify.py` (12) |
| 7 | CPU/memory/pod-slot aggregates enforced | met | aggregate overflow tests; scenario F |
| 8 | Unknown constraints not silently accepted | met | scenario E, problem tests, review F1/F3 regressions |
| 9 | Incomplete snapshots give no false certainty | met | `test_incomplete_inventory_never_modeled_feasible` |
| 10 | Greedy failure ≠ infeasibility | met | scenario B is `MODELED_FEASIBLE`; greedy never sets `optimality_proven` |
| 11 | Interrupted search ≠ optimality | met | state-limit, size-limit and deadline tests |
| 12 | Benchmark cases run | met | 9 fixtures, §E |
| 13 | New artifacts schema-valid | met | 27/27 validate |
| 14 | M1 artifacts unchanged | met | M1 `--check` |
| 15 | Docs match behavior | met | `docs/m2/*`, updated after the review fixes |
| 16 | All tests pass | met | §G |
| 17 | M3 handoff documented | met | §H, `docs/m2/solver-contract.md` |
| 18 | No mutation code | met | `test_safety.py` covers all of `src/qplace` (no Kubernetes/HTTP/socket/subprocess imports) |

---

## B. Repository changes

| Added | Purpose |
|---|---|
| `src/qplace/placement_problem.py` | problem builder, reason codes, objective (`Objective`, `evaluate_objective`, `placement_order`) |
| `src/qplace/placement_result.py` | solver-independent `SolverResult`, `Assignment`, `UnplacedPod`, `Termination` |
| `src/qplace/placement_greedy.py` | `solve_greedy` |
| `src/qplace/placement_exact.py` | `solve_exact`, `ExactConfig` |
| `src/qplace/placement_verify.py` | `verify_placement` (M1-only) |
| `src/qplace/placement_assess.py` | `assess_placement`, `evaluate`, `Outcome`, `Assessment`, `Finding` |
| `src/qplace/placement_serialize.py` | three artifact builders |
| `src/qplace/schemas/{placement-problem,placement-results,benchmark-results}-1.0.schema.json` | schemas |
| `fixtures/m2/scenario_{a..f}_*.json` | 6 synthetic scenarios |
| `artifacts/m2/<fixture>/` ×9 | `placement_problem.json`, `placement_results.json`, `benchmark_results.json` |
| `scripts/generate_m2_artifacts.py` | regenerate / `--check` (developer script, not a CLI) |
| `tests/test_placement_{problem,greedy,exact,verify,integration}.py` | 100 tests |
| `docs/m2/*.md`, `reports/m2/m2_summary.md` | documentation |

| Modified | Change |
|---|---|
| `tests/conftest.py` | added `snapshot_data`, `build_model`, `cand` helpers. Existing helpers unchanged |
| `README.md` | M2 status, usage and layout |

**Decisions:**

- M2 sits on top of M1 and changes none of its code.
- The problem object is the only input a solver sees.
- The verifier reads only M1, so problem-builder bugs can't hide.
- Runtime is kept apart from deterministic output.
- The package version stays `0.1.0`, because bumping it would change the M1 reference artifacts (documented in limitations §7).

**Dependencies:** none added. The standard library is enough; `fractions` gives exact balance arithmetic.

---

## C. Solver design

Full detail is in `docs/m2/optimization-model.md`.

| Topic | Design |
|---|---|
| Variables | `x[p,n]` only for M1-`ELIGIBLE` pairs on usable destinations |
| Constraints | at most one destination per pod; per-destination Σcpu ≤ remaining cpu, Σmemory ≤ remaining memory, Σpods ≤ remaining slots |
| Objective | lexicographic: (1) maximize pods placed; (2) leximin of per-destination free fraction `min_r (remaining−used)/allocatable` in exact `Fraction`s; (3) smallest tie-break vector `(0,node)`/`(1,"")` in pod-key order |
| Greedy | first-fit decreasing (CPU desc, memory desc, key asc); destinations by name; temporary capacity updated after each assignment; rejections recorded for every node |
| Exact | DFS branch-and-bound. Count bound plus balance bound, both proven safe. Ties fully explored |
| Limits | 8 usable candidates, 6 usable destinations, 2,000,000 states, optional deadline. Exceeding any one gives `INDETERMINATE` and no optimality claim |
| Uncertainty | unknown requests or headroom exclude the pod or node. `UNKNOWN` pairs are never variables. All of them become conditions, so the result is `REVIEW_REQUIRED` |

---

## D. Verification

**The verifier** (`verify_placement(model, pairs)`) reads only the M1 `ClusterModel`. It runs all 11 required checks, and aggregates per node only after every pair is collected (see the table in `docs/m2/solver-contract.md` §3). Every solver result on all 9 fixtures was verified, and all passed (`test_every_solver_result_is_verified`).

**The oracle** in `tests/test_placement_exact.py` is a separate brute-force over `itertools.product`, with its own capacity and objective code. On 300 seeded random problems it matched the exact solver in count, balance vector and exact assignment. The random set includes cases where greedy places fewer pods, and the test asserts that such cases exist.

**Guarantees:**

- `MODELED_FEASIBLE` requires a verified complete assignment and no conditions.
- `MODELED_BLOCKED` requires a completed exact search, verification, no conditions and no policy-excluded pairs.

**Known uncertainty:** inherited M1 `UNKNOWN`s; the replacement template; scheduler behavior; PDBs. See `docs/m2/limitations.md`.

---

## E. Benchmark results

These are deterministic values from `artifacts/m2/*/benchmark_results.json`. Runtimes are medians of 5 `perf_counter` runs on this machine (Python 3.14.2), and they vary between runs.

| Fixture | Candidates (usable) | Variables | Greedy placed | Exact placed | Proven | States / pruned | Gap | Assessment | Greedy median s | Exact median s |
|---|---|---|---|---|---|---|---|---|---|---|
| scenario_a_feasible | 3 (3) | 6 | 3 | 3 | yes | 22 / 12 | 0 | MODELED_FEASIBLE | 1.7e-05 | 1.4e-04 |
| scenario_b_greedy_trap | 2 (2) | 3 | **1** | **2** | yes | 7 / 2 | **1** | MODELED_FEASIBLE | 1.6e-05 | 3.8e-05 |
| scenario_c_aggregate_shortage | 3 (3) | 6 | 2 | 2 | yes | 21 / 5 | 0 | MODELED_BLOCKED | 2.1e-05 | 1.6e-04 |
| scenario_d_eligibility | 3 (3) | 3 | 3 | 3 | yes | 7 / 3 | 0 | MODELED_FEASIBLE | 1.8e-05 | 3.1e-05 |
| scenario_e_uncertain | 3 (1) | 2 | 1 | 1 | yes | 4 / 1 | 0 | REVIEW_REQUIRED | 1.7e-05 | 4.6e-05 |
| scenario_f_pod_slots | 2 (2) | 2 | 1 | 1 | yes | 6 / 1 | 0 | MODELED_BLOCKED | 1.6e-05 | 4.9e-05 |
| fixture_a_healthy (M1) | 4 (4) | 8 | 4 | 4 | yes | 31 / 15 | 0 | MODELED_FEASIBLE | 1.8e-05 | 2.2e-04 |
| fixture_b_insufficient (M1) | 3 (3) | 3 | 1 | 1 | yes | 5 / 1 | 0 | REVIEW_REQUIRED | 2.0e-05 | 2.0e-05 |
| fixture_c_constraints (M1) | 8 (2) | 3 | 2 | 2 | yes | 6 / 2 | 0 | REVIEW_REQUIRED | 4.9e-05 | 9.4e-05 |

Both solvers' results were verified valid on all 9 fixtures.

**Scenario B, why greedy fails:**

- First-fit decreasing places `batch` (2000m, no selector) first, on `node-a` (first by name). That leaves 500m.
- `indexer` (1500m) must run on `disk=ssd`, which only `node-a` has, so it cannot be placed.
- The exact solver uses `batch→node-b, indexer→node-a`.

**Where both solvers place the same number but choose different nodes:**

- Scenario A and M1 fixture A: greedy packs everything onto the first node. Exact spreads the pods by the balance objective.

**Assessments that stay `REVIEW_REQUIRED`:**

- M1 fixture B has a review-required pod-level request (Kubernetes 1.33), a known shortage, and an overcommitted node excluded by policy. All three findings are kept.
- M1 fixture C has 13 unknown-eligibility pairs plus Job and unknown-controller replacements.

The runtimes are microseconds because the fixtures are tiny. They are not evidence of scaling.

---

## F. Independent review

A separate agent that did not write M2 reviewed the 10 required areas. It found nothing in these:

- M1 preservation, objective correctness (its own brute force on 2,700 problems, 0 mismatches), aggregate accounting, greedy vs proof, optimality claims, determinism, no mutation, benchmark integrity.

| # | Finding | Severity | Action | Regression test |
|---|---|---|---|---|
| F1 | `MODELED_BLOCKED` was reported when the only blocker was the overcommit policy, even though M1 said a zero-CPU pod fits | material | **fixed**. Non-`INELIGIBLE` pairs on overcommitted destinations are recorded as `policy_excluded_pairs`. They block `MODELED_BLOCKED` (`POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE`) but not `MODELED_FEASIBLE` | `test_review_f1_policy_exclusion_cannot_produce_modeled_blocked`, `..._does_not_block_feasibility_elsewhere` |
| F2 | Hitting the size or state limit forced `REVIEW_REQUIRED` even when greedy had a verified complete placement | limitation | **fixed**. A verified complete greedy assignment now supports `MODELED_FEASIBLE` (`verified_assignments_source: greedy`), with no optimality claim. The interrupted case without a complete greedy stays `REVIEW_REQUIRED` | updated `test_state_limit_gives_indeterminate_without_optimality`; new `test_interrupted_search_without_complete_greedy_is_review_required` |
| F3 | Unscheduled pods and managed terminating pods on the target compete for capacity, but could still give `MODELED_FEASIBLE` | limitation | **fixed** as conditions `COMPETING_DEMAND_UNBOUND_PODS` and `TERMINATING_TARGET_POD` | `test_review_f3_*` (2) |
| F4 | A destination with zero allocatable counted as fully free in the balance objective | minor | **fixed**: free fraction 0 | `test_review_f4_zero_allocatable_destination_counts_as_full` |
| F4b | `candidate()` / `destination()` are linear scans | minor | accepted (fine at M2 sizes) | – |
| Script | `generate_m2_artifacts.py` wrote artifacts on any argument except `--check`. The reviewer's `--help` run rewrote the runtime blocks | defect | **fixed**: unknown arguments exit with code 2 | `test_review_generator_script_rejects_unknown_arguments` |
| M3 note | The leximin balance term is not linear or quadratic | limitation | documented (`docs/m2/limitations.md` §6) | – |

One test was changed on purpose: the F2 assertion in `test_state_limit_gives_indeterminate_without_optimality` now expects `MODELED_FEASIBLE` backed by greedy. That test is M2's own and was written in this milestone. No M1 test was changed.

---

## G. Test evidence

```
$ .venv/bin/python -m pytest -q                                  # Python 3.14.2
230 passed in 0.79s

$ <python3.13.6 venv>/bin/python -m pytest -q -p no:cacheprovider
230 passed in 0.76s

$ .venv/bin/python -m pytest -q --collect-only tests/test_placement_*.py
100 tests collected

$ .venv/bin/python scripts/generate_m1_artifacts.py --check      # 6 × ok
$ .venv/bin/python scripts/generate_m2_artifacts.py --check      # 27 × ok
```

| Suite | Tests | Result |
|---|---|---|
| M1 (unchanged) | 130 | 130 passed |
| `test_placement_problem.py` | 10 | passed |
| `test_placement_greedy.py` | 8 | passed |
| `test_placement_exact.py` | 10 | passed |
| `test_placement_verify.py` | 12 | passed |
| `test_placement_integration.py` | 60 (9 fixtures × 5 parametrized tests + 15 others) | passed |
| **Total** | **230** | **0 failed, 0 skipped** |

**Failures seen during development, and what fixed them:**

- After the review fixes, 10 tests failed. Nine were stale committed artifacts, fixed by regenerating them. One was the intended F2 behavior change, fixed by updating that test.
- One test failed only on 3.13: the script test assumed an installed package. It now sets `PYTHONPATH`.

No assertion was weakened to make a test pass.

---

## H. M3 handoff

```python
from qplace import load_snapshot, build_cluster_model
from qplace.placement_problem import build_placement_problem, evaluate_objective, Objective
from qplace.placement_result import SolverResult, Assignment, UnplacedPod, SolverStats, Termination
from qplace.placement_verify import verify_placement
from qplace.placement_assess import assess_placement, evaluate

model   = build_cluster_model(load_snapshot(path))
problem = build_placement_problem(model)
problem.variables            # tuple[(pod, node)]  = binary vector order
problem.usable_candidates    # CandidateSpec: pod, cpu_millicores, memory_bytes, allowed_nodes
problem.usable_destinations  # DestinationSpec: node, cpu_millicores, memory_bytes, pod_slots (remaining)
```

| Contract | Location |
|---|---|
| Problem format | `PlacementProblem`, or `placement_problem.json` (`variables[]`, `candidates[]`, `destinations[]`, `formulation`) |
| Result format | `SolverResult(solver, config, assignments, unplaced, objective, optimality_proven, termination, stats)` |
| Objective | `evaluate_objective(problem, {pod: node})` and `Objective.better_than` |
| Verifier | `verify_placement(model, [(pod, node), ...]) -> VerificationResult(valid, errors, review_required, usage)` |
| Baselines | `placement_results.json` → `greedy`, `exact`; `benchmark_results.json` → `deterministic` |

**Rules for M3:**

- Return `optimality_proven=False`.
- Only produce variables in `problem.variables`.
- Always run `verify_placement`.
- Compare with `Objective.better_than`.
- Do not change M1 semantics.

**Restrictions:**

- Size limits are 8 × 6 for exact proofs.
- The balance term needs a surrogate in a QUBO.
- No QUBO, Qiskit or circuits exist in M2.

---

## I. Git state

| Item | Value |
|---|---|
| Starting commit | `7cc9a8bdc0060ff3fc66e4cd7c8be8421f4600de` (= `origin/main`, clean tree) |
| M1 commits | unchanged (`14d5995`, `7cc9a8b`) |
| Final commit | see the final response. A hash cannot be written into the file it identifies |
| Author | repo-local identity `Edidiong Etuk <edidiongmichael08@gmail.com>`; global git config not changed |
| Push | to `origin` (`edeediong/qplace-k8s`); fast-forward only, no force |
