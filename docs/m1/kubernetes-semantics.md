# Kubernetes semantics used by M1

**In short:** M1 copies the scheduler's *filter* arithmetic (`NodeResourcesFit`, `TaintToleration`, `NodeUnschedulable`, `nodeSelector`). It copies `kubectl drain` pod filtering. Anything outside that set returns `UNKNOWN`. Where M1 is more conservative than Kubernetes, this page says so.

| Topic | Kubernetes source | M1 behavior | Deviation |
|---|---|---|---|
| Quantities | [Quantity](https://kubernetes.io/docs/reference/kubernetes-api/common-definitions/quantity/) | exact parse, round up | exponents beyond ±64 rejected |
| Requests and limits | [Manage resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) | request, else limit (defaulting), else 0 | none |
| Init containers | [Init containers: resources](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/#resource-sharing-within-containers) | `max(sum(app), max(init))` | none |
| Sidecars | [Sidecar containers: resources](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/#resource-sharing-within-containers) | sidecars added to steady state and to every later init step | `REVIEW_REQUIRED` if version < 1.29 |
| Pod overhead | [Pod overhead](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-overhead/) | added once, after everything else | none |
| Pod-level resources | [Assign pod-level resources](https://kubernetes.io/docs/tasks/configure-pod-container/assign-pod-level-resources/) | replaces container aggregate for cpu/memory | `REVIEW_REQUIRED` if version < 1.34 or unknown |
| In-place resize | [Resize container resources](https://kubernetes.io/docs/tasks/configure-pod-container/resize-container-resources/) | `max(spec, status.resources, allocatedResources)` | `UNKNOWN` if status is missing |
| Taints and tolerations | [Taints and tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/) | `Equal`/`Exists`; NoSchedule/NoExecute are hard; PreferNoSchedule is soft | other operators → `UNKNOWN` |
| Node selection | [Assign pods to nodes](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/) | `nodeSelector` exact match | required affinity → `UNKNOWN` |
| Drain | [Safely drain a node](https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/), [kubectl drain](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_drain/) | kubectl filters, metadata only | advisory only |
| Disruption budgets | [Disruptions](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/), [Configure a PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/) | relevance and a snapshot-time verdict only | never "permits drain" |
| Mirror pods | [`kubernetes.io/config.mirror`](https://kubernetes.io/docs/reference/labels-annotations-taints/#kubernetes-io-config-mirror) | annotation decides | none |

Code reference for the accounting: `k8s.io/component-helpers/resource/helpers.go` (`PodRequests`, `AggregateContainerRequests`, `IsPodResizeInfeasible`). This code was read on 2026-10-10 to confirm the rules below.

---

## 1. Effective pod request

For each resource (cpu, memory) separately:

```
container_i      = request_i  if set
                 = limit_i    if only a limit is set   (API defaulting)
                 = 0          otherwise

steady           = Σ app containers + Σ sidecars
init_peak        = max over ordinary init containers k of
                     ( container_k + Σ sidecars declared before k )
                   and, for each sidecar, Σ sidecars up to and including it
aggregate        = max(steady, init_peak)
if pod-level request set for this resource:
    aggregate    = pod_level           (M1 uses max(pod_level, aggregate); see §1.2)
effective        = aggregate + overhead
```

### 1.1 Sidecars (restartable init containers)

An init container with `restartPolicy: Always` starts and keeps running. Kubernetes adds it to the running total for every later init container and for the app containers. M1 does the same. An ordinary init container that runs *before* a sidecar does not pay for that sidecar. The test `test_restartable_sidecars_overlap_with_later_init_and_app_containers` covers this.

### 1.2 Pod-level requests

`spec.resources.requests.{cpu,memory}` replaces the container aggregate for that resource. A resource with no pod-level request keeps the container aggregate. API validation requires pod-level ≥ container aggregate. If a snapshot breaks that rule, M1 uses the larger value and sets `POD_LEVEL_BELOW_CONTAINER_AGGREGATE` / `REVIEW_REQUIRED`.

Feature state: `PodLevelResources` is beta and on by default from 1.34. It was alpha in 1.32–1.33. A field in an exported object proves the API server accepted it. It does not prove the scheduler gate was on. So for versions before 1.34, or an unknown version, the value is used but marked `REVIEW_REQUIRED` (`POD_LEVEL_FEATURE_STATE_UNCERTAIN`).

A pod-level limit with no pod-level request: the limit is used as a conservative upper bound and marked `REVIEW_REQUIRED`.

### 1.3 In-place resize

With `InPlacePodVerticalScaling` (beta, on by default from 1.33), the scheduler reserves this for each app container and sidecar that reports `status.resources`:

| Resize state | Reserved |
|---|---|
| Normal / in progress | `max(spec request, status.resources.requests, status.allocatedResources)` |
| `PodResizePending` with reason `Infeasible` (or legacy `status.resize: Infeasible`) | `max(status.resources.requests, status.allocatedResources)`; spec ignored |

M1 applies this whenever status resources are present.

**Known conservative deviation:** M1 takes the max per container and then sums. Current upstream code sums each source first (spec, actuated, allocated) and then takes the max of the three totals. A sum of maxes is never smaller than a max of sums, so M1 can overstate a resizing pod's reservation, but never understate it.

A resize is indicated by `PodResizePending`, `PodResizeInProgress` or legacy `status.resize`. If a resize is indicated and any app container or sidecar has no `status.resources`, cpu and memory are **`null` / `UNKNOWN`** (`RESIZE_STATUS_INCOMPLETE`). A pod with pod-level resources that is also resizing is `UNKNOWN` (`POD_LEVEL_RESIZE_NOT_MODELED`). A resize on a version before 1.33, or an unknown version, is `REVIEW_REQUIRED`.

### 1.4 Not modeled in the request

| Item | Treatment |
|---|---|
| `ephemeral-storage`, `hugepages-*` | not accounted; `UNMODELED_RESOURCE_REQUEST` → eligibility `UNKNOWN` |
| Extended resources (`nvidia.com/gpu`, …) | not accounted; `EXTENDED_RESOURCE_NOT_MODELED` → eligibility `UNKNOWN` |
| DRA claims (`spec.resourceClaims`) | `DYNAMIC_RESOURCE_CLAIM_NOT_MODELED` → `UNKNOWN` |
| Scheduler scoring defaults (100m / 200Mi for request-less pods) | not used. They affect scoring only, not filtering |

---

## 2. Node reservation

| Pod state | Reserves capacity? | Why |
|---|---|---|
| Bound, `Pending` or `Running` | yes | in the scheduler cache |
| Bound, `deletionTimestamp` set | **yes** | stays in the cache until the API object is gone |
| Bound, phase `Unknown` or missing | yes (`RESERVED_UNKNOWN_PHASE`) | conservative |
| `Succeeded` / `Failed` | no | the scheduler's pod informer filters out terminal phases |
| Not bound (`spec.nodeName` empty) | no | reported as `UNBOUND_PENDING_PODS_PRESENT` because it competes for capacity |
| Not bound, `nominatedNodeName` set | no | `NOMINATED_POD_NOT_RESERVED` warning on that node |

`remaining = allocatable − reserved` per node, for cpu, memory and pod slots (`allocatable.pods`, a real `NodeResourcesFit` check). Raw `capacity` is never used for headroom. Negative headroom is kept and flagged `OVERCOMMITTED`. Requests are not utilization. M1 never reads metrics.

---

## 3. Eligibility rules

Each check applies to one candidate pod on one node:

| Check | Fails with | Notes |
|---|---|---|
| Node is the drain target | `DRAIN_TARGET` (blocker) | always |
| Ready condition `False` / `Unknown` | `NODE_NOT_READY` (blocker) | **conservative M1 policy.** The scheduler itself relies on the not-ready taints |
| Ready condition absent | `NODE_READY_CONDITION_MISSING` (unknown) | |
| `spec.unschedulable` | `NODE_UNSCHEDULABLE` (blocker) | unless the pod tolerates `node.kubernetes.io/unschedulable:NoSchedule`, as `NodeUnschedulable` allows |
| `nodeSelector` | `NODE_SELECTOR_MISMATCH` (blocker) | exact key and value |
| NoSchedule / NoExecute taint not tolerated | `UNTOLERATED_TAINT` (blocker) | |
| PreferNoSchedule taint not tolerated | `UNTOLERATED_PREFER_NO_SCHEDULE_TAINT` (soft) | does not change state |
| Unknown taint effect | `UNSUPPORTED_TAINT_EFFECT` (unknown) | |
| Toleration with an unsupported operator (for example `Lt`/`Gt`) is the only possible match | `UNSUPPORTED_TOLERATION_OPERATOR` (unknown) | |
| Any pod-level unsupported constraint | its own code (unknown) | see limitations.md |

Toleration matching copies `Toleration.ToleratesTaint`:

- An empty `effect` matches every effect.
- An empty `key` with `Exists` matches every key.
- `Equal` (or an empty operator) compares values.

State: any blocker gives `INELIGIBLE`. Otherwise, any unknown gives `UNKNOWN`. Otherwise the state is `ELIGIBLE`.

The existing pod's `spec.nodeName` is its current binding. It is **not** used as a constraint on the replacement.

Capacity fit is separate. It compares one pod's effective request with the node's `remaining`. As in `NodeResourcesFit`, a resource the pod requests none of (0) is not checked. The pod-slot check always applies. If `remaining` is unknown but the upper bound already rules the pod out, the result is `DOES_NOT_FIT`. Otherwise it is `UNKNOWN`.

---

## 4. Drain classification

Classes are checked in this order (first match wins). Ownership comes only from `metadata.ownerReferences[controller=true]`, by API group and kind, never from pod names.

| Order | Class | Test | Placement candidate | kubectl flag |
|---|---|---|---|---|
| 1 | `MIRROR` | `kubernetes.io/config.mirror` annotation | no | (skipped by kubectl) |
| 2 | `TERMINAL` | phase `Succeeded`/`Failed` | no | – |
| 3 | `DAEMONSET` | controller `apps/DaemonSet` (terminating or not; kubectl ignores `deletionTimestamp` here) | no | `--ignore-daemonsets` |
| 4 | `TERMINATING` | `deletionTimestamp` set | no | `--force` / `--delete-emptydir-data` if they apply |
| 5 | `UNMANAGED` | no controller ownerReference | no (deletion is permanent) | `--force` |
| 6 | `UNKNOWN_CONTROLLER` | controller kind/group not known | yes, replacement `UNKNOWN` | – |
| 7 | `CONTROLLER_MANAGED` | `apps/ReplicaSet`, `apps/StatefulSet`, `core/ReplicationController` → `ASSUMED`; `batch/Job` → `CONDITIONAL` | yes | – |

A pod with `emptyDir` needs `--delete-emptydir-data`. A `hostPath` volume gets the finding `HOST_PATH_VOLUME`. Every candidate carries `REPLACEMENT_TEMPLATE_UNKNOWN`: the replacement is modeled from the current pod spec, because controller templates are not in the snapshot.

Unknown-controller pods count as placement candidates because that is the conservative choice. Counting them adds demand, it never hides demand.

### PDBs (advisory)

Selector rules (policy/v1): `{}` matches all pods in the namespace, and a `null` selector matches none. `In`, `NotIn`, `Exists` and `DoesNotExist` are supported. With any other operator the pod is counted as possibly covered and `UNSUPPORTED_SELECTOR_OPERATOR` is set.

| Verdict | Condition |
|---|---|
| `PDB_EVICTION_BLOCKED_AT_SNAPSHOT` | `disruptionsAllowed == 0` |
| `PDB_MAY_BLOCK_SEQUENTIAL_EVICTION` | matching evictable target pods > `disruptionsAllowed` |
| `PDB_ADVISORY_ONLY` | matching pods ≤ `disruptionsAllowed`. **Still not a guarantee:** budgets change while replacements become healthy |
| `PDB_STATUS_MISSING` | no `status.disruptionsAllowed` |

Only evictable target pods are counted. Pending, terminal and terminating pods are left out, because the eviction API skips PDB checks for them (`canIgnorePDB`).

A pod covered by more than one PDB gets `MULTIPLE_PDBS_MATCH`, because the eviction API refuses such pods.
