# M1 validation

**In short:** run `pytest` and `generate_m1_artifacts.py --check`. Both must pass. The suite needs no cluster and no network. It covers quantity parsing, effective requests, node accounting, eligibility, drain classification, snapshot validation, the three reference fixtures end to end, determinism and the read-only guarantee.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -v                               # all tests
.venv/bin/python scripts/generate_m1_artifacts.py --check   # committed artifacts match the code
.venv/bin/python scripts/generate_m1_artifacts.py           # regenerate after an intended change
```

Runtime dependency: `jsonschema>=4.18`. Test dependency: `pytest>=7`. Python 3.11 or newer.

## Test map

| File | Covers |
|---|---|
| `tests/test_quantity.py` | integer, fractional and milli CPU; binary and decimal memory; exponents; `1E` vs `1E3`; invalid; negative; round-up; exactness; huge-exponent rejection |
| `tests/test_pod_requests.py` | single and multiple containers; request from limit; init max; sidecar overlap and ordering; overhead; pod-level (full, partial, version review); resize (max rule, Infeasible, missing status → UNKNOWN); invalid quantity isolation |
| `tests/test_node_accounting.py` | empty node; summed pods; pending bound; terminating; terminal; unbound; overcommit (negative kept); partial inventory bounds; unknown request; missing allocatable |
| `tests/test_eligibility.py` | selector match and mismatch; Equal/Exists/wildcard tolerations; hard taints by value and effect; soft taint; unsupported operator; cordon and its toleration; NotReady / missing Ready; unsupported affinity; unsupported catalogue; blocker beats unknown; current `nodeName` ignored; drain target excluded; capacity fit; zero-request pod on an overcommitted node |
| `tests/test_drain.py` | ReplicaSet, StatefulSet, DaemonSet, mirror (annotation, not name), standalone, non-controller owner ref, terminating, terminal, unknown kind, wrong API group, Job conditional, emptyDir, other-node pods, PDB verdicts, selector semantics, multiple PDBs, terminating DaemonSet pod, Pending pods outside PDB counts |
| `tests/test_snapshot.py` | provenance; unsupported versions; schema and consistency rejections (8 cases); partial-inventory warning; no aliasing of input |
| `tests/test_integration.py` | per fixture: schema-valid artifacts, byte determinism, input-order independence, committed artifacts current, no unsupported condition lost; fixture-specific expectations for A, B, C |
| `tests/test_safety.py` | no Kubernetes/HTTP/socket/subprocess imports in `src/qplace` |

Expected values in the unit tests are worked out by hand from the Kubernetes rules in [kubernetes-semantics.md](kubernetes-semantics.md). Each non-obvious expectation has a comment that gives the arithmetic.

## Reference fixtures

| Fixture | Target | Purpose | Key expected outcome |
|---|---|---|---|
| `fixture_a_healthy.json` (k8s 1.34.1) | `worker-03` | managed pods, ample headroom | 4 candidates, each `ELIGIBLE` + `FITS` on worker-01/02; all nodes `EXACT`; capacity assessment complete; PDB `ADVISORY_ONLY` |
| `fixture_b_insufficient.json` (k8s 1.33.2) | `worker-02` | not enough headroom | 1500m/2Gi pod `DOES_NOT_FIT` anywhere; worker-03 memory −512Mi (`OVERCOMMITTED`, kept negative); pod-level pod `REVIEW_REQUIRED` (1.33) |
| `fixture_c_constraints.json` (k8s 1.34.0) | `worker-03` | selectors, taints, cordon, NotReady, DaemonSet, mirror, standalone, terminating, unknown controller, PDBs, 8 kinds of unsupported constraint | only 3 `ELIGIBLE` pairs exist; every pod with an unsupported hard constraint has zero eligible destinations; kubectl flags `--delete-emptydir-data --force --ignore-daemonsets`; ledger PDB `BLOCKED_AT_SNAPSHOT` |

All fixture data is synthetic. Names, UIDs and image references are invented (`registry.example.invalid`).

## Reproducibility

| Guarantee | How it is checked |
|---|---|
| Same input → byte-identical output | `test_output_is_byte_for_byte_deterministic` |
| Input object order does not matter | `test_output_independent_of_input_object_order` (shuffled nodes, pods, PDBs) |
| Committed artifacts match code | `test_committed_reference_artifacts_are_current` and `--check` |
| No path or clock dependence | provenance stores the file basename and SHA-256 only. No timestamps are generated |
