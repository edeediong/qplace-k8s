# M1 limitations

**In short:** M1 is a state model. It is not an advisor yet. It makes no placement decision and no joint-fit claim. It says nothing about live readiness. Any hard constraint it cannot evaluate gives `UNKNOWN`, never `ELIGIBLE`.

## 1. Not implemented (by design)

| Out of M1 | Where it belongs |
|---|---|
| Greedy / exact placement, QUBO, QAOA, Qiskit benchmarks | M2+ |
| Live Kubernetes connector, kubectl plugin, product CLI | later milestones |
| Any cluster mutation (cordon, evict, delete, deploy) | never. QPlace is read-only |
| Web UI, autoscaling, cross-cluster, AI recommendations, remediation | out of scope |

`tests/test_safety.py` checks that the package imports no Kubernetes client, HTTP, socket or subprocess module.

## 2. Situations that return `UNKNOWN`

### Eligibility (`state = UNKNOWN`)

| Reason code | Trigger |
|---|---|
| `REQUIRED_NODE_AFFINITY_NOT_MODELED` | `nodeAffinity.requiredDuringScheduling…` |
| `REQUIRED_POD_AFFINITY_NOT_MODELED` | required `podAffinity` |
| `REQUIRED_POD_ANTI_AFFINITY_NOT_MODELED` | required `podAntiAffinity` |
| `TOPOLOGY_SPREAD_NOT_MODELED` | `topologySpreadConstraints` with `DoNotSchedule` |
| `PERSISTENT_VOLUME_TOPOLOGY_NOT_MODELED` | PVC, generic ephemeral, inline CSI, in-tree cloud disks, or any volume type not known to be topology-neutral |
| `EXTENDED_RESOURCE_NOT_MODELED` | any request or limit other than cpu/memory/ephemeral-storage/hugepages (GPUs and similar) |
| `UNMODELED_RESOURCE_REQUEST` | `ephemeral-storage`, `hugepages-*` |
| `DYNAMIC_RESOURCE_CLAIM_NOT_MODELED` | `spec.resourceClaims` |
| `HOST_PORT_NOT_MODELED` | any `hostPort` |
| `CUSTOM_SCHEDULER` | `schedulerName` ≠ `default-scheduler` |
| `RUNTIME_CLASS_NOT_MODELED` | `runtimeClassName` set (runtime may be missing on the node) |
| `UNSUPPORTED_TOLERATION_OPERATOR` | e.g. `Lt`/`Gt` comparison operators |
| `UNSUPPORTED_TAINT_EFFECT` | effect not NoSchedule/NoExecute/PreferNoSchedule |
| `NODE_READY_CONDITION_MISSING` | node has no Ready condition |

A known blocker still wins: if a node also fails a modeled hard check, the state is `INELIGIBLE`. The unknowns are still listed on that row.

### Resource accounting (`null` value)

| Reason code | Trigger |
|---|---|
| `INVALID_QUANTITY` / `NEGATIVE_QUANTITY` | malformed quantity in that resource |
| `RESIZE_STATUS_INCOMPLETE` | resize indicated but container status lacks `resources` |
| `POD_LEVEL_RESIZE_NOT_MODELED` | pod-level resources + resize |
| `ALLOCATABLE_MISSING` | node allocatable absent |
| `POD_REQUEST_UNKNOWN` (node) | any reserving pod has an unknown request |
| `INVENTORY_INCOMPLETE` | pod inventory `partial`. Reserved/remaining are `null`, bounds are given |

### Values present but marked `REVIEW_REQUIRED`

| Reason code | Trigger |
|---|---|
| `POD_LEVEL_FEATURE_STATE_UNCERTAIN` | pod-level requests and Kubernetes < 1.34 or unknown |
| `POD_LEVEL_BELOW_CONTAINER_AGGREGATE` | inconsistent pod-level request (larger value used) |
| `SIDECAR_FEATURE_STATE_UNCERTAIN` | sidecars and Kubernetes < 1.29 |
| `RESIZE_FEATURE_STATE_UNCERTAIN` | resize and Kubernetes < 1.33 or unknown |
| `REQUEST_DEFAULTED_FROM_LIMIT` (pod-level) | pod-level limit without request |

## 3. Known modeling gaps

| Gap | Effect |
|---|---|
| Replacement template unknown | replacements are modeled from the current pod spec. A Deployment rollout in flight, or a changed template, could differ. Every candidate is flagged `REPLACEMENT_TEMPLATE_UNKNOWN` |
| Job replacement | `CONDITIONAL`. It depends on backoffLimit and podFailurePolicy, which are not in the snapshot |
| Unknown controllers | counted as demand (conservative). The controller may not recreate the pod |
| Unbound / nominated pods | reported, but not reserved against any node. They may take capacity first |
| Node Ready policy | M1 rejects NotReady nodes outright. This is stricter than the scheduler |
| Scheduler extenders, plugins, profiles, `NodeResourcesFit` scoring | not modeled |
| Volume limits per node (`NodeVolumeLimits`), image locality, inter-pod rules | not modeled |
| PDB | snapshot-time and advisory only. `PDB_ADVISORY_ONLY` is not "safe to drain". `unhealthyPodEvictionPolicy` is recorded but not evaluated |
| kubectl flags | an approximation of `kubectl drain` filters, for information |
| Eviction readiness | never inferred from capacity |
| Resize accounting | per-container max, then sum. Can overstate (never understate) a resizing pod |
| Quantity exponents | `e`/`E` exponents beyond ±64 are rejected as `INVALID_QUANTITY` |

## 4. Capacity statements M1 does not make

- `eligible_and_individually_fits` means one pod fits on its own. It is not a joint fit.
- `summary.remaining_total_ready_schedulable` vs `placement_candidate_demand` is a necessary condition only. Bin-packing can fail even when the totals look sufficient.
- `summary.placement_decision` is always `null`.

## 5. Future enhancements (recorded, not built)

- Evaluate required node affinity (`In`/`NotIn`/`Exists`/`DoesNotExist`/`Gt`/`Lt`) instead of returning `UNKNOWN`.
- Host-port conflict check against pods already on the destination.
- Ephemeral-storage and extended-resource accounting.
- Optional controller objects (Deployments, ReplicaSets, StatefulSets) in the snapshot, to model the real replacement template.
- PDB simulation across a sequence of evictions.
