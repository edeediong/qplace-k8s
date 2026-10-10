# M2 solver contract

**In short:** build one `PlacementProblem` from the M1 model. Run any solver that returns a `SolverResult`. Then trust nothing until `verify_placement(model, pairs)` passes. M3's quantum solver plugs in at the solver step and changes nothing else.

```python
from qplace import load_snapshot, build_cluster_model
from qplace.placement_problem import build_placement_problem
from qplace.placement_greedy import solve_greedy
from qplace.placement_exact import solve_exact, ExactConfig
from qplace.placement_verify import verify_placement
from qplace.placement_assess import assess_placement, evaluate

model   = build_cluster_model(load_snapshot(path))
problem = build_placement_problem(model)                    # PlacementProblem
greedy  = solve_greedy(problem)                             # SolverResult
exact   = solve_exact(problem, ExactConfig())               # SolverResult
check   = verify_placement(model, [(a.pod, a.node) for a in exact.assignments])
report  = assess_placement(model)                           # both solvers + verification + assessment
```

## 1. Public types

| Type | Module | Fields |
|---|---|---|
| `PlacementProblem` | `placement_problem` | `target_node`, `provenance`, `candidates: tuple[CandidateSpec]`, `destinations: tuple[DestinationSpec]`, `conditions`, `limitations`, `policy_excluded_pairs`; `usable_candidates`, `usable_destinations`, `variables`, `candidate(pod)`, `destination(node)`, `is_conditional` |
| `CandidateSpec` | `placement_problem` | `pod`, `cpu_millicores`, `memory_bytes` (`int \| None`), `request_status`, `replacement`, `allowed_nodes`, `unknown_nodes`, `ineligible_nodes`, `exclusion_reasons`, `usable` |
| `DestinationSpec` | `placement_problem` | `node`, remaining `cpu_millicores`/`memory_bytes`/`pod_slots` (may be negative or `None`), `allocatable_*`, `accounting_status`, `exclusion_reasons`, `usable` |
| `Objective` | `placement_problem` | `assigned`, `balance`, `tiebreak`; `better_than(other)` |
| `SolverResult` | `placement_result` | `solver`, `config`, `assignments: tuple[Assignment]`, `unplaced: tuple[UnplacedPod]`, `objective`, `optimality_proven`, `termination`, `stats` |
| `VerificationResult` | `placement_verify` | `valid`, `errors: tuple[VerificationError]`, `review_required`, `usage: tuple[NodeUsage]` |
| `PlacementReport` | `placement_assess` | `problem`, `greedy`, `exact` (`PlacementEvaluation(result, verification, outcome)`), `assessment`, `findings`, `greedy_gap`, `supporting` (`"exact"`/`"greedy"`/`None`); `verified_assignments` |

Helper functions: `evaluate_objective(problem, {pod: node})`, `placement_order(problem)`, `evaluate(model, problem, result)`.

## 2. Rules every solver must follow

1. Read only the `PlacementProblem`. Never read or modify the M1 model.
2. Assign only pairs in `problem.variables`.
3. Return every candidate exactly once: either in `assignments` or in `unplaced`.
4. Sort `assignments` and `unplaced` by pod key.
5. Set `objective = evaluate_objective(problem, assignment)`. Never compute your own score for comparison.
6. Set `optimality_proven = True` only after an exhaustive or otherwise proven search. Heuristics and samplers (greedy, QAOA) must set it to `False`.
7. Report how the run ended in `termination`. Interrupted runs must not claim optimality.
8. `stats.duration_seconds` is observational. It never goes into deterministic artifacts.

## 3. Verifier rules (`verify_placement`)

It takes the **M1 model** and a list of `(pod, node)` pairs. It does not use `PlacementProblem`.

| # | Check | Violation code |
|---|---|---|
| 1 | pod is an M1 placement candidate | `NOT_A_PLACEMENT_CANDIDATE` |
| 2 | node exists | `UNKNOWN_DESTINATION` |
| 3 | node is not the drain target | `DESTINATION_IS_DRAIN_TARGET` |
| 4 | M1 reports the pair `ELIGIBLE` | `DESTINATION_NOT_ELIGIBLE` |
| 5 | pod appears at most once | `DUPLICATE_ASSIGNMENT` |
| 6–7 | cpu and memory requests are known and non-negative | `CPU_REQUEST_UNKNOWN`, `MEMORY_REQUEST_UNKNOWN`, `NEGATIVE_REQUEST` |
| 8 | destination headroom is known | `HEADROOM_UNKNOWN` |
| 9–11 | per-node **aggregate** cpu, memory, pod count ≤ remaining (checked after all pairs are collected) | `CPU_CAPACITY_EXCEEDED`, `MEMORY_CAPACITY_EXCEEDED`, `POD_SLOTS_EXCEEDED` |

Negative remaining headroom fails check 9, 10 or 11 for any assignment to that node. So the verifier also enforces M2's overcommitment exclusion, through its own arithmetic. `REVIEW_REQUIRED` requests or nodes do not fail verification. They are listed in `review_required`.

## 4. M3 integration requirements

- Consume `placement_problem.json`, or the `PlacementProblem` object, exactly as is. `variables[]` gives the binary vector. §2 of [optimization-model.md](optimization-model.md) gives the constraints.
- A quantum solver returns a `SolverResult` with its own `solver` id and `optimality_proven = False`.
- Decode bitstrings to pairs. Infeasible samples are dropped or repaired before they are returned, but they are **always** checked with `verify_placement`.
- Compare against `exact` with `Objective.better_than`. Report the greedy and exact baselines from `placement_results.json` / `benchmark_results.json`.
- M3 must not change M1 semantics, eligibility, the verifier or this contract. Any change in meaning needs a new artifact schema version.
