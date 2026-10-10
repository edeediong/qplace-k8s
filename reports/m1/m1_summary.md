# M1 summary: Cluster State Modeling

**MILESTONE 1 STATUS: PASS.** All 14 gate conditions are met (see §A). 130 tests pass, with 0 failures and 0 skips, on Python 3.14.2 and 3.13.6. M2 can start against the interface in §G.

| Metric | Value |
|---|---|
| Tests collected / passed / failed / skipped | 130 / 130 / 0 / 0 |
| Test functions (before parametrization) | 78 |
| Reference fixtures | 3, all load, all artifacts schema-valid |
| Artifact reproducibility | byte-identical, independent of input order, committed copies verified |
| Independent review findings | 7: 4 fixed, 2 documented as conservative deviations, 1 kept by design |
| New runtime dependencies | 1 (`jsonschema`) |

---

## A. Executive summary

**Built:**

- Snapshot schema 1.0 with an integrity check.
- Exact quantity parsing.
- Effective pod requests: init containers, native sidecars, overhead, pod-level requests and in-place resize.
- Node reservations, with bounds for partial inventories.
- Drain-target classification based on owner metadata.
- Advisory PDB relevance.
- Node eligibility with three states: `ELIGIBLE`, `INELIGIBLE`, `UNKNOWN`.
- Per-pod capacity fit.
- Two deterministic JSON artifacts, each with its own JSON Schema.

**Main limitations:**

- No placement decision and no joint-fit claim.
- Required node affinity, pod affinity, topology spread, PV topology, extended resources, DRA, host ports, custom schedulers and RuntimeClass all return `UNKNOWN`.
- Replacements are modeled from the current pod spec. Controller templates are not in the snapshot.

**M2 readiness:** ready. The public Python types and artifact schemas are fixed at version 1.0.

| # | Gate condition | Result | Evidence |
|---|---|---|---|
| 1 | Snapshot schema documented and validated | met | `snapshot-1.0.schema.json`, `docs/m1/data-contract.md` §1, `test_snapshot.py` (15) |
| 2 | All reference fixtures load | met | `test_fixture_produces_schema_valid_artifacts` ×3 |
| 3 | Node and pod normalization | met | `nodes.py`, `pods.py`, exercised by every test |
| 4 | CPU/memory parsing | met | `test_quantity.py` (37) |
| 5 | Effective pod requests | met | `test_pod_requests.py` (15) |
| 6 | Node reservations | met | `test_node_accounting.py` (10), fixture A/B numbers |
| 7 | Drain classification | met | `test_drain.py` (15), fixture C |
| 8 | Node eligibility | met | `test_eligibility.py` (16) |
| 9 | Unsupported conditions produce diagnostics | met | `test_unsupported_constraint_catalogue`, `test_no_unsupported_condition_is_lost_in_serialization` |
| 10 | Stable, reproducible artifacts | met | determinism, shuffle and committed-artifact tests; `--check` |
| 11 | All tests pass | met | §D |
| 12 | No mutation capability | met | `test_safety.py`; there is no client code |
| 13 | Docs match behavior | met | docs updated after the review fixes |
| 14 | M2 interface stable | met | §G |

---

## B. Repository changes

Initial state: the project directory was **empty** and was not a git repository. No M0 existed. I ran `git init` locally (no remote, nothing pushed). I also created the minimum foundation that M1 needs: the package, `pyproject.toml`, pytest config, the README and the snapshot schema. **M0 is not claimed as complete.**

| Added | Purpose |
|---|---|
| `pyproject.toml`, `.gitignore`, `README.md` | project foundation |
| `src/qplace/diagnostics.py` | `Reason` codes, `Diagnostic`, exceptions |
| `src/qplace/quantity.py` | exact Quantity parser |
| `src/qplace/snapshot.py` | loader, schema and consistency checks, provenance |
| `src/qplace/nodes.py`, `pods.py` | normalization and pod-intrinsic constraint detection |
| `src/qplace/resources.py` | effective requests and node accounting |
| `src/qplace/eligibility.py` | node eligibility and per-pod capacity fit |
| `src/qplace/drain.py` | drain classification and PDB assessment |
| `src/qplace/model.py`, `serialize.py` | `build_cluster_model` and the artifacts |
| `src/qplace/schemas/*.json` | 3 JSON Schemas |
| `fixtures/m1/*.json` | 3 synthetic snapshots |
| `artifacts/m1/*/` | 6 generated reference artifacts |
| `scripts/generate_m1_artifacts.py` | developer regenerate/`--check` script (not a CLI) |
| `tests/*.py` | 8 test modules |
| `docs/m1/*.md`, `reports/m1/m1_summary.md` | documentation |

Modified files: none, because the repo started empty.

**Architectural decisions:**

- Every module is a pure function over frozen dataclasses. There is no global state.
- Unknown is `None` plus a reason code. It is never 0.
- Eligibility and capacity fit are separate verdicts.
- Pod identity is `namespace/name`.
- Artifacts carry no clock data. Provenance stores the file basename and SHA-256.

**Dependencies:**

- `jsonschema` checks both the input and the output against declared schemas.
- `pytest` is used for tests only.
- Nothing else. There is no Kubernetes client and no optimization library.

---

## C. Kubernetes correctness review

The full rules, with links to the official docs, are in `docs/m1/kubernetes-semantics.md`. The accounting was checked against `k8s.io/component-helpers/resource/helpers.go`.

| Topic | Handling | Deviation |
|---|---|---|
| CPU/memory requests | request, else limit (API defaulting), else 0; exact parse, rounded up | exponents beyond ±64 rejected |
| Init containers and sidecars | `max(Σapp + Σsidecars, max(init_k + sidecars before k))` | none |
| Pod overhead | added once, last | none |
| Pod-level requests | replace the container aggregate for cpu/memory | `REVIEW_REQUIRED` when Kubernetes < 1.34 or the version is unknown |
| In-place resize | `max(spec, status.resources, allocatedResources)`; spec ignored if Infeasible | `UNKNOWN` without status; per-container max can overstate (conservative) |
| Bound and terminating pods | bound Pending/Running/terminating/Unknown pods reserve; Succeeded/Failed do not | Unknown phase counted (conservative) |
| Node eligibility | target excluded; cordon (with the unschedulable toleration); nodeSelector; Equal/Exists; NoSchedule/NoExecute hard; PreferNoSchedule soft | NotReady treated as a hard blocker (stricter than the scheduler) |
| Drain classification | mirror annotation, terminal, DaemonSet, terminating, unmanaged, unknown controller, known controller; kubectl flags | flags are an advisory approximation |

**Independent review.** A separate agent reviewed the code against the upstream Kubernetes source. Results:

| # | Finding | Action |
|---|---|---|
| F1 | A zero-request pod was reported `DOES_NOT_FIT` on an overcommitted node (`NodeResourcesFit` skips resources the pod does not request) | **fixed**, plus a test |
| F2 | A terminating DaemonSet pod lost `--ignore-daemonsets` | **fixed** (DaemonSet now checked before terminating), plus a test |
| F3 | Pending pods were counted against PDBs (the eviction API ignores PDBs for them) | **fixed**, plus a test |
| F6 | A huge exponent (`1e-99999999`) hung the parser | **fixed** (bound of ±64), plus a test |
| F4 | Resize uses per-container max; upstream uses max of totals | documented. Overstates only |
| F5 | NotReady is a hard blocker | documented conservative policy |
| F7 | Sidecar feature check skipped when the version is unknown | kept by design. A sidecar field in an exported object proves the API server accepted it, and sidecars are GA from 1.33 |

The reviewer found no path where an unknown value becomes 0, `ELIGIBLE` or `FITS`.

---

## D. Test evidence

Commands run (output shown is the actual tail):

```
$ .venv/bin/python -m pytest -q
130 passed in 0.26s

$ .venv/bin/python -m pytest --collect-only -q
130 tests collected in 0.01s

$ .venv/bin/python scripts/generate_m1_artifacts.py --check
ok       artifacts/m1/fixture_a_healthy/cluster_model.json
ok       artifacts/m1/fixture_a_healthy/resource_inventory.json
ok       artifacts/m1/fixture_b_insufficient/cluster_model.json
ok       artifacts/m1/fixture_b_insufficient/resource_inventory.json
ok       artifacts/m1/fixture_c_constraints/cluster_model.json
ok       artifacts/m1/fixture_c_constraints/resource_inventory.json

$ <python3.13 venv>/bin/python -m pytest -q -p no:cacheprovider
130 passed in 0.29s
```

| File | Passed |
|---|---|
| `test_quantity.py` | 37 |
| `test_integration.py` | 21 |
| `test_eligibility.py` | 16 |
| `test_pod_requests.py` | 15 |
| `test_drain.py` | 15 |
| `test_snapshot.py` | 15 |
| `test_node_accounting.py` | 10 |
| `test_safety.py` | 1 |
| **Total** | **130** (0 failed, 0 skipped) |

**History:**

- The first full run had 3 failures. All 3 were the same bug: my `cluster-model` schema left `data_completeness` inventory fields out of `properties` while it set `additionalProperties: false`. I fixed the schema. No test was weakened.
- The review fixes added 4 tests.

**Schema validation:** all 6 artifacts validate against `cluster-model-1.0` / `resource-inventory-1.0` (Draft 2020-12).

**Environment:**

- Python 3.14.2 is the main environment, with pytest 9.1.1 and jsonschema 4.26.0.
- Python 3.13.6 was run in a separate venv: 130 passed.
- Python 3.11 and 3.12 are not installed on this machine, so they were not tested. The code uses only 3.11+ features (`StrEnum`).

---

## E. Fixture results

| Fixture | Candidates | Eligibility (E / I / U) | Capacity fit (fits / not / unknown) | Notes |
|---|---|---|---|---|
| A, healthy (target worker-03) | 4 | 8 / 4 / 0 | 12 / 0 / 0 | all nodes `EXACT`; api pod 1000m from its init container; web pod 640Mi with its sidecar; Succeeded Job pod not reserved; PDB `ADVISORY_ONLY` |
| B, insufficient (target worker-02) | 3 | 6 / 3 / 0 | 3 / 6 / 0 | the 1500m/2Gi pod fits nowhere; worker-01 has 300m/1Gi left (terminating and Pending pods included); worker-03 memory **−512Mi** kept, `OVERCOMMITTED`; pod-level pod `REVIEW_REQUIRED` (1.33) |
| C, constraints (target worker-03) | 8 | 3 / 32 / 13 | 47 / 1 / 0 | classes: 7 managed, 1 each of unknown controller, DaemonSet, mirror, unmanaged, terminal, terminating; flags `--delete-emptydir-data --force --ignore-daemonsets`; ledger PDB `BLOCKED_AT_SNAPSHOT`, payments-all `MAY_BLOCK_SEQUENTIAL_EVICTION`; ledger-0 `MULTIPLE_PDBS_MATCH` |

**Unexpected but correct behavior:**

- In C, `ml/trainer` is `UNKNOWN` on worker-01 and worker-04. Those nodes have no GPU, so the real scheduler would reject the pod. M1 does not model extended resources, so it reports `UNKNOWN` rather than guess.
- In C, `analytics/etl` is `UNKNOWN` on worker-04 (zone-b) even though its required affinity asks for zone-a. The reason is the same: required node affinity is not modeled.
- No placement recommendation exists in any artifact. `summary.placement_decision` is `null`.

---

## F. Remaining risks

| Type | Risk |
|---|---|
| Correctness | Real `kubectl get -o json` exports are untested. All fixtures are synthetic |
| Correctness | Feature-gate state is inferred from the version only. Clusters with non-default gates may differ (flagged `REVIEW_REQUIRED` where it matters) |
| Modeling | Many real pods will be `UNKNOWN` (PVCs, required affinity, spread constraints are common). M2 must treat `UNKNOWN` as "not assignable" or report it |
| Modeling | The replacement template is assumed equal to the current pod spec |
| Modeling | Unbound and nominated pods are not reserved and may take capacity first |
| Compatibility | Upstream resize accounting is still changing (pod-level resize is alpha). Re-check `helpers.go` on each minor release |
| Deferred | Affinity evaluation, host-port conflicts, ephemeral-storage and extended-resource accounting, controller objects in the snapshot, PDB sequencing |

---

## G. M2 handoff

**Entry point:**

```python
from qplace import load_snapshot, build_cluster_model
model = build_cluster_model(load_snapshot(path))   # ClusterModel
```

**Inputs M2 consumes:**

```python
model.drain.placement_candidates                 # tuple[str]  "namespace/name"
model.destination_nodes                          # tuple[str]  all nodes except the target
model.eligible_destinations(pod_key)             # tuple[str]  nodes with state ELIGIBLE
model.resources.pod_requests[pod_key]            # PodRequests: cpu_millicores, memory_bytes (int|None), status
model.resources.nodes[node]                      # NodeAccount: remaining_cpu_millicores, remaining_memory_bytes,
                                                 #   remaining_pod_slots (int|None), status, *_upper_bound
model.eligibility                                # tuple[EligibilityRecord(pod, node, eligibility, capacity)]
model.completeness.capacity_assessment_complete  # bool
```

The artifact equivalents are `cluster_model.json` (`drain_inventory.placement_candidates`, `eligibility[]`, `candidate_destinations[]`) and `resource_inventory.json` (`nodes[].remaining`, `pods[].cpu_millicores` / `memory_bytes`). Both use artifact schema 1.0. Field definitions are in `docs/m1/data-contract.md`.

**Invariants M2 must hold:**

1. **Assign a pod only to a node in `model.eligible_destinations(pod)`.** `UNKNOWN` and `INELIGIBLE` pairs are not assignable. If M2 offers an "optimistic" mode, it must label any `UNKNOWN` pair it uses.
2. **Check aggregate capacity on its own account.** For every node, the sum of assigned pods' cpu, memory and count must be ≤ `remaining_*`. Per-pod `capacity_fit` is not a joint guarantee.
3. A `None` request or `None` remaining is never 0. A pod with an unknown request, or a node with unknown headroom, cannot be part of a proven-feasible plan.
4. `REVIEW_REQUIRED` values may be used, but the result must carry that status forward.
5. If `capacity_assessment_complete` is false, M2 must not report "feasible" without that caveat.
6. M2 must stay read-only and must not treat PDB `ADVISORY_ONLY` as drain approval.

---

## H. Git state

| Item | Value |
|---|---|
| Initial | directory empty, not a git repo |
| Action | `git init -b main` locally; no remote configured, nothing pushed |
| Pre-existing user changes | none existed |
| Excluded from commit | `.venv/`, caches (`.gitignore`) |
| Milestone commit | see the final response. The hash cannot be written into this file without changing it |
