# M2 optimization model

**In short:** M2 solves a 0/1 multi-dimensional assignment problem. A variable exists only for M1-`ELIGIBLE` pairs. Each pod goes to at most one destination. Per-destination CPU, memory and pod-slot limits hold. The objective is lexicographic: most pods placed, then the most balanced free capacity, then a fixed tie-break. Two solvers exist: a deterministic first-fit-decreasing greedy, and an exact branch-and-bound. The branch-and-bound is checked against an exhaustive oracle.

## 1. Sets and data

| Symbol | Meaning | Source |
|---|---|---|
| `P` | usable placement candidates | M1 `drain.placement_candidates` with known cpu and memory requests and ≥1 allowed destination |
| `N` | usable destinations | non-target nodes with known allocatable and remaining cpu/memory/pods, and no negative remaining value |
| `A ⊆ P × N` | allowed pairs | M1 eligibility state `ELIGIBLE` on a usable destination |
| `c_p`, `m_p` | effective CPU millicores, memory bytes of `p` | M1 `PodRequests` |
| `C_n`, `M_n`, `S_n` | remaining CPU, memory, pod slots of `n` | M1 `NodeAccount.remaining_*` |
| `C*_n`, `M*_n`, `S*_n` | allocatable CPU, memory, pods of `n` | M1 `NodeAccount.allocatable_*` |

All values are Python integers. No floating point is used in any capacity decision. Fractions in the secondary objective are exact `fractions.Fraction`.

## 2. Formulation (the form M3 will use)

```
variables      x[p,n] ∈ {0,1}            for (p,n) ∈ A only

at most one    Σ_n x[p,n] ≤ 1            ∀ p ∈ P
cpu            Σ_p c_p · x[p,n] ≤ C_n    ∀ n ∈ N
memory         Σ_p m_p · x[p,n] ≤ M_n    ∀ n ∈ N
pod slots      Σ_p x[p,n]       ≤ S_n    ∀ n ∈ N
eligibility    implicit: x[p,n] does not exist for (p,n) ∉ A
```

`placement_problem.json` lists `A` as `variables[]` with a stable `index`. The order is candidates by pod key, then nodes by name.

## 3. Objective (lexicographic)

| Rank | Goal | Definition |
|---|---|---|
| 1 | **maximize pods placed** | `Σ x[p,n]` |
| 2 | maximize balance (leximin) | for each `n ∈ N`: `f_n = min( (C_n−used_c)/C*_n, (M_n−used_m)/M*_n, (S_n−used_s)/S*_n )`, skipping a term whose allocatable is 0. Sort the `f_n` ascending. A lexicographically larger vector is better: first raise the most-loaded destination's free fraction, then the next one, and so on |
| 3 | tie-break | vector over **all** candidates in pod-key order: `(0, node)` if placed, `(1, "")` if not. Smaller is better |

Rank 1 is never traded for rank 2 (`test_primary_objective_never_sacrificed_for_balance`).

Why this measure:

- Leximin protects the destination that would end up closest to full. That is the node most likely to cause trouble after a drain.
- It needs no weights.
- It is exact with rational arithmetic, so it is deterministic on every platform.

Why the tie-break: two optimal assignments must never be reported in an order that depends on hash seeds, dict order or input order. A total order over assignments guarantees one answer.

`Objective.better_than` in `placement_problem.py` is the single implementation of this order. Both solvers use it.

## 4. Greedy solver (`greedy-ffd-v1`)

1. Unusable candidates are reported unplaced, with their exclusion reasons.
2. Usable candidates are sorted by **CPU desc, memory desc, pod key asc** (first-fit decreasing).
3. Each pod goes to the **first allowed destination by name** whose temporary remaining CPU, memory and pod slots can hold it. The temporary remaining values are then reduced.
4. Every destination the pod could not use is recorded with a reason (`NOT_ELIGIBLE`, `ELIGIBILITY_UNKNOWN_PAIR`, `DESTINATION_EXCLUDED`, `INSUFFICIENT_*`).

Cost: O(|P|·|N|). Greedy always reports `optimality_proven = false`. A greedy shortfall proves nothing.

**Why greedy fails in scenario B:**

- First-fit decreasing takes the largest pod first: `batch` (2000m, no selector). It places that pod on `node-a`, the first allowed node by name.
- That leaves `node-a` with 500m.
- `indexer` (1500m, `nodeSelector disk=ssd`) is allowed only on `node-a`, so it has nowhere to go.
- The exact solver puts `batch` on `node-b` and `indexer` on `node-a`.

## 5. Exact solver (`exact-bnb-v1`)

Depth-first search over candidates, in greedy order. For each candidate, branch on every allowed destination with enough current capacity, then on "leave unplaced".

**Pruning:**

| Rule | Prune when | Why it is safe |
|---|---|---|
| Count bound | `placed + #(remaining candidates that fit some allowed destination alone, given current capacity) < incumbent.assigned` | capacity only shrinks deeper in the tree, so this overestimates what can still be placed |
| Balance bound | the count bound **equals** the incumbent count and the current balance vector is already lexicographically **smaller** than the incumbent's | placing more pods can only lower each `f_n`. The sorted vector can then only fall elementwise, so its lexicographic value can only fall |

Neither rule can remove an optimal solution. Ties on both count and balance are always explored, so the tie-break stays exact.

**Limits** (`ExactConfig`):

| Limit | Default | When exceeded |
|---|---|---|
| `max_candidates` (usable) | 8 | `SIZE_LIMIT_EXCEEDED`, no search |
| `max_destinations` (usable) | 6 | `SIZE_LIMIT_EXCEEDED`, no search |
| `max_states` | 2,000,000 DFS nodes | `STATE_LIMIT_REACHED` |
| `deadline_seconds` | none (keeps runs deterministic) | `DEADLINE_EXCEEDED` |

In all of these cases `optimality_proven = false`, the outcome is `INDETERMINATE`, and the assessment is `REVIEW_REQUIRED`. Worst-case work grows as `(|N|+1)^|P|` (6.6 million leaves at 8×7). The state budget turns the remaining worst cases into inconclusive results instead of long runs.

## 6. Outcome and assessment

| Outcome | Rule (applied after independent verification) |
|---|---|
| `INDETERMINATE` | verification failed, or the search was interrupted or skipped |
| `COMPLETE` | every M1 placement candidate is assigned (including unusable ones, so any unknown candidate prevents `COMPLETE`) |
| `NONE` | nothing assigned, and there were candidates |
| `PARTIAL` | otherwise |

| Assessment | Rule |
|---|---|
| `MODELED_FEASIBLE` | a verified `COMPLETE` assignment exists and the problem has **no conditions**. The assignment normally comes from the exact solver. If the exact search was interrupted, greedy's verified complete assignment is used (`verified_assignments_source: greedy`), with `EXACT_SEARCH_INCONCLUSIVE` and no optimality claim |
| `MODELED_BLOCKED` | exact proven optimal, verified, `PARTIAL`/`NONE`, **no conditions**, and **no policy-excluded pairs** |
| `REVIEW_REQUIRED` | everything else |

Conditions are:

- incomplete capacity assessment;
- unknown or review-required requests;
- unknown eligibility or headroom on a non-excluded destination;
- review-required destination accounting;
- Job or unknown-controller replacements;
- unscheduled pods competing for capacity (`COMPETING_DEMAND_UNBOUND_PODS`);
- managed terminating pods on the drain target (`TERMINATING_TARGET_POD`).

Policy-excluded pairs are non-`INELIGIBLE` pairs on overcommitted destinations (`policy_excluded_pairs`). They do not stop `MODELED_FEASIBLE`, because a plan that avoids those nodes is still a valid plan. They do stop `MODELED_BLOCKED`, because the shortage might come from the policy rather than from capacity (`POLICY_EXCLUSION_MAY_AFFECT_SHORTAGE`).

The findings keep the details. `KNOWN_SHORTAGE_AMONG_MODELED_CANDIDATES` can appear together with `CONDITIONAL_INPUTS`. `MODELED_BLOCKED` means "blocked in this requested-resource model under M2's planning policy". It does not mean Kubernetes would fail.
