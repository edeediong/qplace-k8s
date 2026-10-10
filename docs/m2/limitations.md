# M2 limitations

**In short:** M2 answers one question: could the modeled replacement pods be packed onto eligible destinations by their requested CPU, memory and pod slots? It does not predict what the Kubernetes scheduler will do. It does not simulate controllers. It does not decide whether a drain will succeed. **`MODELED_FEASIBLE` is never `SAFE_TO_DRAIN`.**

## 1. Problem size

| Limit | Value | Effect |
|---|---|---|
| Exact solver candidates | 8 usable candidates (configurable) | more → `SIZE_LIMIT_EXCEEDED`, `INDETERMINATE`, `REVIEW_REQUIRED` |
| Exact solver destinations | 6 usable destinations (configurable) | same |
| State budget | 2,000,000 search nodes | `STATE_LIMIT_REACHED`, no optimality claim |
| Greedy | none | always runs, never claims optimality |

These are starting operational limits, not runtime promises. Real drains often involve more pods. Raising the limits is safe for correctness. Only runtime grows.

## 2. Inherited model coverage

Everything in [M1 limitations](../m1/limitations.md) still applies. Any pair that M1 marks `UNKNOWN` cannot be assigned. Common causes are required affinity, topology spread, PVC topology, GPUs and extended resources, host ports, custom schedulers and RuntimeClass. Any candidate with an unknown request cannot be assigned. Both cases make the assessment `REVIEW_REQUIRED`, so many real clusters will get `REVIEW_REQUIRED`.

## 3. Deliberate planning restrictions

| Restriction | Detail |
|---|---|
| Overcommitted destinations excluded | Any destination with negative remaining CPU, memory or pods (or M1 `overcommitted`) is unusable. **This differs from Kubernetes**, which would still admit a zero-request pod on a node with negative CPU headroom. Negative values are kept in the artifacts. Affected pairs are listed in `policy_excluded_pairs`, and their presence prevents `MODELED_BLOCKED` |
| Only `ELIGIBLE` pairs | There is no optimistic mode. `UNKNOWN` is never promoted |
| `REVIEW_REQUIRED` values | used in the calculation, but the assessment stays `REVIEW_REQUIRED` |
| Incomplete inventory | node headroom is unknown, so those destinations are unusable. The assessment cannot become `MODELED_FEASIBLE` |

## 4. Why the tool does not guarantee drain success

| Not modeled | Consequence |
|---|---|
| Real scheduler choices (scoring, spreading, preemption, other pending pods) | the scheduler may pick other nodes, or other pods may take the capacity first. Unscheduled pods are not reserved. Their presence adds the `COMPETING_DEMAND_UNBOUND_PODS` condition, which keeps the result at `REVIEW_REQUIRED`. A managed terminating pod on the target adds `TERMINATING_TARGET_POD` for the same reason |
| Controller reconciliation | replacements are modeled from the current pod spec (`REPLACEMENT_TEMPLATE_UNKNOWN`). Jobs and unknown controllers are conditional |
| Eviction ordering and PodDisruptionBudgets | PDB findings are advisory only. A feasible placement is not eviction approval |
| Pods that are not candidates (DaemonSet, mirror, standalone, terminating) | listed under `NON_CANDIDATE_TARGET_PODS`. Not placed |
| Timing | the snapshot is a point in time |

## 5. Exact search limits and the assessment

When the exact search is skipped or interrupted, only optimality is lost. If greedy found a verified complete assignment, the assessment can still be `MODELED_FEASIBLE`, backed by greedy. If greedy did not, the result is `REVIEW_REQUIRED`. A shortage is never reported as `MODELED_BLOCKED` without a completed exact search. On clusters with more than 6 usable destinations, or more than 8 usable candidates, `MODELED_BLOCKED` is therefore not reachable with the default limits.

## 6. Objective limits

- The secondary "balance" objective is one documented choice (leximin of free fractions). It is not a measure of real-world quality.
- `MODELED_BLOCKED` is a proof only inside this restricted model. It is not proof that Kubernetes would fail.

- The leximin balance term is not linear or quadratic in `x`. M3 needs a surrogate, such as a count-only or quadratic penalty, and must compare results with `Objective.better_than`.

## 7. Other

- The package version stays `0.1.0`. Bumping it would change the `generator.version` field inside the committed M1 reference artifacts, which the M1 gate requires to stay byte-identical.
