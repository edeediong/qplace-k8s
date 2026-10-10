# M2 validation

**In short:** `pytest` runs the M1 suite unchanged plus the M2 suite. Both artifact scripts run with `--check`. The exact solver is compared with an independent exhaustive oracle on 300 seeded random problems. The verifier gets every invalid assignment the spec lists.

## Commands

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q                               # M1 + M2
.venv/bin/python scripts/generate_m1_artifacts.py --check   # M1 artifacts unchanged
.venv/bin/python scripts/generate_m2_artifacts.py --check   # M2 artifacts current (runtime block excluded)
.venv/bin/python scripts/generate_m2_artifacts.py           # regenerate, which also re-measures runtimes
```

## Benchmark fixtures

| Fixture | Purpose | Expected exact result | Expected assessment |
|---|---|---|---|
| `fixtures/m2/scenario_a_feasible.json` | all pods fit | 3/3 | `MODELED_FEASIBLE` |
| `scenario_b_greedy_trap.json` | first-fit trap (selector) | 2/2; greedy 1/2 | `MODELED_FEASIBLE`, `GREEDY_MISSED_FEASIBLE_ASSIGNMENT` |
| `scenario_c_aggregate_shortage.json` | each pod fits alone, not together | 2/3, proven | `MODELED_BLOCKED` |
| `scenario_d_eligibility.json` | taint + selector; exact capacity equality | 3/3 | `MODELED_FEASIBLE` |
| `scenario_e_uncertain.json` | unmodeled anti-affinity, unknown resize request | 1/3 | `REVIEW_REQUIRED` |
| `scenario_f_pod_slots.json` | CPU and memory ample, one pod slot | 1/2, proven | `MODELED_BLOCKED` |
| `fixtures/m1/fixture_a_healthy.json` | M1 healthy | 4/4 | `MODELED_FEASIBLE` |
| `fixtures/m1/fixture_b_insufficient.json` | M1 shortage + review value + overcommitted node | 1/3 | `REVIEW_REQUIRED` + `KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES` + `POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE` |
| `fixtures/m1/fixture_c_constraints.json` | M1 constraint-heavy | 2/8 | `REVIEW_REQUIRED` |

All fixtures are synthetic.

## Test map

| File | Covers |
|---|---|
| `tests/test_placement_problem.py` | units; drain target excluded; eligible-only variables; overcommit exclusion with negatives kept; unknown request; unknown eligibility; review values; partial inventory; Job/unknown controller; candidate order; input-order independence; no M1 mutation |
| `tests/test_placement_greedy.py` | first-fit by name; capacity updates; exact equality; rejection reasons; zero-request pod; no eligible destination; empty set; the greedy trap |
| `tests/test_placement_exact.py` | **oracle on 300 random problems** (count, balance, exact assignment); exact ≥ greedy; primary objective kept; balance and tie-break; trap solved; proven partial; state limit (greedy-backed feasibility and inconclusive case), size limit, deadline; trivial problems; repeatability |
| `tests/test_placement_verify.py` | valid case; duplicate; source node; ineligible; unknown node; non-candidate; aggregate CPU, memory and pod overflow; unknown request; unknown headroom; negative headroom; review flag |
| `tests/test_placement_integration.py` | review regressions (policy exclusion vs blocked, unbound and terminating competing demand, zero-allocatable balance, script arguments); scenarios A–F; M1 review values; incomplete inventory; empty set; every solver result verified on 9 fixtures; assessment invariants; schema validation; determinism; committed artifacts; no mutation; no `SAFE_TO_DRAIN` |

## The oracle

`oracle()` in `tests/test_placement_exact.py` is written separately from the solver:

- It enumerates every combination of "unplaced or one allowed node" for each usable candidate (`itertools.product`).
- It checks capacity and computes the balance and tie-break with its own code.
- It keeps the best result.

It shares no search or pruning code with `placement_exact.py`. The random problems come from a fixed seed (`20261010`). They include zero-capacity nodes, zero-request pods and empty allowed sets.

## Reproducibility

| Property | Check |
|---|---|
| Same input → same `placement_problem.json` / `placement_results.json` bytes | `test_artifacts_schema_valid_deterministic_and_committed`, `--check` |
| Input object order irrelevant | `test_problem_independent_of_input_order_and_does_not_mutate_model` |
| Solver repeatability | `test_repeatable_under_fixed_configuration` |
| Runtime separated | only `benchmark_results.json → runtime_observations` changes between runs. `--check` ignores that block |
