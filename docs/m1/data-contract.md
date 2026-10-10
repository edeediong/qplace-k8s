# M1 data contract

**In short:** one JSON snapshot goes in (schema `1.0`). Two JSON artifacts come out (`cluster_model.json`, `resource_inventory.json`, artifact schema `1.0`). CPU is integer millicores. Memory is integer bytes. Unknown is always `null` with a reason code, never `0`.

| Contract | Schema file | Version |
|---|---|---|
| Input snapshot | `src/qplace/schemas/snapshot-1.0.schema.json` | `1.0` |
| `cluster_model.json` | `src/qplace/schemas/cluster-model-1.0.schema.json` | `1.0` |
| `resource_inventory.json` | `src/qplace/schemas/resource-inventory-1.0.schema.json` | `1.0` |

Versioning rule: a field never changes meaning inside a version. Any change in meaning needs a new `schema_version`. The loader rejects versions it does not know.

---

## 1. Input snapshot (schema 1.0)

```json
{
  "schema_version": "1.0",
  "snapshot": {
    "id": "fixture-a-healthy",
    "captured_at": "2026-10-01T09:00:00Z",
    "source_type": "synthetic_fixture",
    "kubernetes_version": "v1.34.1",
    "description": "optional free text",
    "inventory": {
      "nodes": "complete",
      "pods": "complete",
      "pod_disruption_budgets": "complete",
      "notes": "optional"
    }
  },
  "target_node": "worker-03",
  "nodes": [ /* v1 Node objects */ ],
  "pods": [ /* v1 Pod objects */ ],
  "pod_disruption_budgets": [ /* policy/v1 PodDisruptionBudget objects */ ]
}
```

| Field | Required | Rule |
|---|---|---|
| `schema_version` | yes | must be `"1.0"` |
| `snapshot.id` | yes | `[A-Za-z0-9._-]+` |
| `snapshot.captured_at` | yes | ISO-8601 in UTC (`Z` or `+00:00`) |
| `snapshot.source_type` | yes | `synthetic_fixture` or `kubernetes_export` |
| `snapshot.kubernetes_version` | yes, nullable | `v1.34.1`, `1.34` or `null` when not known |
| `snapshot.inventory.nodes` / `.pods` | yes | `complete` or `partial` |
| `snapshot.inventory.pod_disruption_budgets` | yes | `complete`, `partial` or `not_collected` |
| `target_node` | yes | must name a node in `nodes` |
| `nodes[]` | yes | `metadata.name`, `status` required |
| `pods[]` | yes | `metadata.{name,namespace,uid}`, `spec.containers` (≥1) required |
| `pod_disruption_budgets[]` | yes (may be empty) | `metadata.{name,namespace}`, `spec` required |

`kind` / `apiVersion` are optional on each object. When they are present, they must be `Node`/`v1`, `Pod`/`v1` and `PodDisruptionBudget`/`policy/v1`.

### Integrity checks (load fails with `SnapshotValidationError`)

| Check | Result |
|---|---|
| JSON Schema violation | rejected, every problem listed |
| Unsupported `schema_version` | rejected |
| Duplicate node name or node UID | rejected |
| Duplicate pod `namespace/name` or pod UID | rejected |
| Duplicate container names inside one pod | rejected |
| Duplicate PDB `namespace/name` | rejected |
| `target_node` not in `nodes` | rejected |
| Pod bound to a node missing from a **complete** node inventory | rejected |
| Pod bound to a node missing from a **partial** node inventory | accepted, `POD_BOUND_TO_UNKNOWN_NODE` warning |
| PDB inventory `not_collected` but PDB objects present | rejected |

A partial pod inventory is never read as complete. It makes node reservations lower bounds and remaining headroom upper bounds. See §3.

### Provenance kept

`snapshot_id`, `captured_at`, `source_type`, `kubernetes_version`, `description`, the source file **basename** and the source file SHA-256. The full path is not stored, so artifacts do not depend on where the repo is checked out.

### Exported Pod vs unadmitted manifest

M1 assumes pods come from the API server (`kubectl get pods -o json`). Admission and defaulting have then already run. If a container has a limit but no request, M1 applies the API defaulting rule (request = limit) and records `REQUEST_DEFAULTED_FROM_LIMIT`. That pattern suggests the input was never admitted. M1 does **not** invent LimitRange defaults.

---

## 2. Quantity handling

| Rule | Detail |
|---|---|
| Grammar | `<signedNumber><suffix>`, where suffix is `Ki Mi Gi Ti Pi Ei`, `n u m "" k M G T P E`, or `e<int>` / `E<int>` |
| Precision | exact `fractions.Fraction`, no floats |
| CPU | `ceil(value × 1000)` millicores (`Quantity.MilliValue`) |
| Memory | `ceil(value)` bytes (`Quantity.Value`) |
| Rounding | always up, so a positive request never rounds to 0 (`0.1m` → `1`) |
| Negative | rejected, `NEGATIVE_QUANTITY` |
| Malformed | rejected, `INVALID_QUANTITY` (`1K`, `1KB`, `1 Gi`, booleans, exponent beyond ±64) |
| `1E` vs `1E3` | `1E` = 10^18 (exa); `1E3` = 1000 (exponent) |

An invalid quantity inside a pod makes only that resource of that pod `null` / `UNKNOWN`. The rest of the snapshot is still modeled.

---

## 3. Output artifacts

Both artifacts share a header:

| Key | Content |
|---|---|
| `artifact` | `cluster_model` or `resource_inventory` |
| `artifact_schema_version` | `"1.0"` |
| `generator` | `{name: "qplace", version, milestone: "M1"}` |
| `snapshot` | provenance plus `inventory` completeness |
| `target_node` | drain target |
| `data_completeness` | `capacity_assessment_complete` (bool) plus `reasons[]` |

The artifacts contain no wall-clock timestamp. The same input and the same code give byte-identical files. Serialization is `json.dumps(sort_keys=True, indent=2, ensure_ascii=False)` plus a trailing newline. Lists are sorted by node name, by pod `namespace/name`, or by `(pod, node)`.

### `cluster_model.json`

| Key | Content |
|---|---|
| `nodes[]` | name, uid, labels, annotations, taints, `capacity` and `allocatable` (`cpu_millicores`, `memory_bytes`, `pods`), `other_allocatable` (verbatim extended resources), `ready` (`True`/`False`/`Unknown`/`null`), `unschedulable`, `kubelet_version` |
| `pods[]` | identity, `node_name`, phase, `deletion_timestamp`, owner refs, `controller`, `is_mirror`, labels, annotations, containers and init containers (`role`: `container`/`init`/`sidecar`, raw requests and limits, host ports), overhead, pod-level requests and limits, node selector, tolerations, raw `affinity`, raw `topology_spread_constraints`, volumes, scheduler name, runtime class, resource claims, priority, resize state, `constraints[]` (`supported`/`unsupported`/`soft`) |
| `drain_inventory` | per target pod: `drain_class`, `controller{kind,api_version,name,known}`, `replacement`, `placement_candidate`, `kubectl_drain_flags`, `findings[]`, `matching_pdbs`. Also `placement_candidates[]`, `excluded[]`, `kubectl_drain_flags_required[]`, `pdb_assessments[]` |
| `eligibility[]` | one row per (placement candidate × node): `state`, `blockers[]`, `unknowns[]`, `soft[]`, `capacity_fit{fit, reasons[]}` |
| `candidate_destinations[]` | per candidate: `eligible`, `eligible_and_individually_fits`, `unknown`, `ineligible` node lists |
| `unsupported_constraints[]` | flat `(pod, code, detail)` list |
| `diagnostics[]` | `{code, severity, subject, message, details}` |
| `summary` | counts. `placement_decision` is always `null` in M1 |

### `resource_inventory.json`

| Key | Content |
|---|---|
| `accounting_policy` | the rules in force, as text |
| `nodes[]` | `allocatable`, `reserved`, `reserved_lower_bound`, `remaining`, `remaining_upper_bound`, `overcommitted[]`, `status`, `reasons[]`, `reserving_pods[]` |
| `pods[]` | `cpu_millicores`, `memory_bytes`, `status`, `method[]`, `reasons[]`, `breakdown` (per resource: containers, sidecars, init peak, aggregate, pod-level, overhead, effective), `reservation`, `placement_candidate` |
| `summary` | node status counts, overcommitted nodes, Ready+schedulable destinations, their clamped remaining total, candidate demand total, request status counts |

### Status and enum values

| Enum | Values |
|---|---|
| Accounting status | `EXACT`, `REVIEW_REQUIRED` (value present, semantics ambiguous), `INCOMPLETE` (partial inventory), `UNKNOWN` |
| Eligibility | `ELIGIBLE`, `INELIGIBLE`, `UNKNOWN` |
| Capacity fit | `FITS`, `DOES_NOT_FIT`, `UNKNOWN` |
| Drain class | `CONTROLLER_MANAGED`, `UNKNOWN_CONTROLLER`, `DAEMONSET`, `MIRROR`, `UNMANAGED`, `TERMINAL`, `TERMINATING` |
| Replacement | `ASSUMED`, `CONDITIONAL`, `NOT_EXPECTED`, `UNKNOWN` |
| Reservation | `RESERVED`, `RESERVED_TERMINATING`, `RESERVED_UNKNOWN_PHASE`, `NOT_RESERVED_TERMINAL`, `NOT_RESERVED_UNBOUND` |

All reason codes are listed in `qplace.diagnostics.Reason`.

### Unknown and incomplete values

| Situation | Encoding |
|---|---|
| Pod request cannot be determined | `cpu_millicores` / `memory_bytes` = `null`, status `UNKNOWN`, reason in `reasons` |
| Node allocatable missing | `allocatable.*` = `null`, `ALLOCATABLE_MISSING` |
| Any reserving pod unknown | `reserved.*` and `remaining.*` = `null` for that resource; `*_lower_bound` / `*_upper_bound` still filled |
| Partial pod inventory | `reserved` and `remaining` = `null`, status `INCOMPLETE`, bounds filled |
| Overcommitted node | negative `remaining` kept; `OVERCOMMITTED` |

---

## 4. Python interface (stable for M2)

```python
from qplace import (
    load_snapshot,             # (path) -> Snapshot
    load_snapshot_data,        # (dict, *, source_file=None, source_sha256=None) -> Snapshot
    normalize_node,            # (raw Node dict) -> NodeModel
    normalize_pod,             # (raw Pod dict) -> PodModel
    calculate_pod_requests,    # (PodModel, kubernetes_minor: tuple[int,int] | None) -> PodRequests
    build_resource_inventory,  # (nodes, pods, InventoryCompleteness, target_node, kubernetes_minor) -> ResourceInventory
    classify_drain_pods,       # (pods, target_node, pdbs=(), inventory=None) -> DrainInventory
    evaluate_node_eligibility, # (PodModel, NodeModel, target_node) -> EligibilityResult
    evaluate_capacity_fit,     # (PodRequests, NodeAccount) -> CapacityFitResult
    build_cluster_model,       # (Snapshot) -> ClusterModel
    cluster_model_dict, resource_inventory_dict, write_artifacts,
)
```

| Type | Main fields |
|---|---|
| `ClusterModel` | `snapshot`, `nodes`, `pods`, `resources: ResourceInventory`, `drain: DrainInventory`, `eligibility: tuple[EligibilityRecord]`, `completeness`, `diagnostics`; `destination_nodes`, `eligible_destinations(pod_key)` |
| `PodRequests` | `cpu_millicores`, `memory_bytes` (`int \| None`), `status`, `method`, `reasons`, `breakdown` |
| `NodeAccount` | `allocatable_*`, `reserved_*`, `remaining_*` (`int \| None`), `*_lower_bound`, `*_upper_bound`, `status`, `overcommitted` |
| `DrainInventory` | `pods: tuple[DrainPodRecord]`, `pdbs`, `placement_candidates`, `kubectl_drain_flags` |
| `EligibilityRecord` | `pod`, `node`, `eligibility: EligibilityResult`, `capacity: CapacityFitResult` |

Errors: `SnapshotValidationError` (with `.problems`) and `QuantityError` (with `.reason`). Both subclass `QPlaceError`. No error is caught and turned into a "successful" model.

Reference artifacts are regenerated with `python scripts/generate_m1_artifacts.py`. This is a developer script, not a product CLI.
