# Public dataset experiment: Google cluster trace

**In short:** besides the synthetic fixtures, QPlace runs one instance derived from a **public, CC-BY 4.0** dataset: Google's `clusterdata-2011-2` cluster trace. It drains a real trace machine with three real tasks onto two real trace machines. The trace data contains its own greedy trap: first-fit decreasing places 2 of 3 tasks, while the exact solver proves 3 of 3 is possible. QAOA on a local simulator samples the unique complete plan. It is not run on Qollab: the hosted demo uses the synthetic greedy-trap instance.

## Source and licence

| Item | Value |
|---|---|
| Dataset | Google cluster-usage traces, `clusterdata-2011-2` ([description](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)) |
| Licence | CC-BY 4.0 ([text](https://creativecommons.org/licenses/by/4.0/)). Attribution: C. Reiss, J. Wilkes, J. L. Hellerstein, *Google cluster-usage traces: format + schema*, Google Inc., 2011 |
| Files used | [`machine_events/part-00000-of-00001.csv.gz`](https://storage.googleapis.com/clusterdata-2011-2/machine_events/part-00000-of-00001.csv.gz) (SHA256 `fc44b8d2…2249664`), [`task_events/part-00000-of-00500.csv.gz`](https://storage.googleapis.com/clusterdata-2011-2/task_events/part-00000-of-00500.csv.gz) (SHA256 `eae977d5…f10f18`). Both match Google's published `SHA256SUM` |
| Changes made | rows at trace time 0 are converted into a Kubernetes-style snapshot (below). The raw files are not redistributed; only the derived values are in `fixtures/m5/google_trace_drain.json` |
| Re-derive | `python scripts/derive_google_trace_fixture.py machine_events.csv.gz task_events.csv.gz`. The script refuses files with a different checksum |

## Mapping and honesty notes

| Trace concept | QPlace model | Note |
|---|---|---|
| Machine (ADD event at time 0) | Node | capacity = allocatable |
| Task (SCHEDULE event at time 0) | Pod owned by a ReplicaSet named after its job | a Borg task is not a Kubernetes pod. It is an **analogous** public workload record |
| Normalized CPU / memory (largest machine = 1.0) | integer units = value × 100,000, exact `Decimal` arithmetic | capacities rounded down, requests rounded up, so the instance is never looser than the trace. Units are written as millicores and bytes only because the schema needs quantities. Placement feasibility does not depend on the per-resource scale |
| Pod slots | 110 per node | **assumption**: the trace has no pod limit |
| "Different machines" restriction | checked by the script: no same-job task is on a destination | so it cannot block this instance, and it is not encoded |
| Inventory | 3 of the trace's machines, `nodes: partial` | the M2 assessment therefore correctly stays `REVIEW_REQUIRED` |

Every node and pod carries a `qplace.dev/source` annotation with its file and line number.

## Instance and results

| | CPU units | Memory units | Rows (file line) |
|---|---|---|---|
| Drain node m-1335771 | | | machine_events 1997 |
| Task A (job 3244832720 #217) | 14180 | 7654 | task_events 152174 |
| Task B (job 3996125377 #22) | 12570 | 1271 | task_events 154376 |
| Task C (job 4476806578 #24) | 3436 | 8911 | task_events 159226 |
| Destination m-369002391: free after its 5 running tasks | 22623 | 14985 | machine_events 7413 |
| Destination m-5774811: free after its 5 running tasks | 17810 | 8143 | machine_events 2439 |

| Method | Result |
|---|---|
| Greedy (first-fit decreasing) | **2 / 3**: A → m-369002391, B → m-5774811. Then C fits neither node on memory |
| Exact branch-and-bound | **3 / 3**, proven optimal and unique: A → m-5774811, B and C → m-369002391 |
| QUBO | 5 qubits (C → m-5774811 is eliminated because it can never fit on memory), no slack, pairwise-exact capacity encodings. All 32 states checked exhaustively, with a unique minimum |
| QAOA, local noiseless simulator | best verified sample = the exact 3/3 plan at p = 1 and p = 2. P(optimal) = 0.010 at p = 1 (**below** random guessing, 1/32 = 0.031) and 0.069 at p = 2 (2.2× random) |
| M2 assessment | `REVIEW_REQUIRED` (partial node inventory). The placement is feasible *among the modelled machines* |

Artifacts: `artifacts/m3/google_trace_drain/`. Tests: `tests/test_public_dataset.py`.

## What this does and does not show

- **It shows** that the greedy-trap pattern occurs in a real, public trace, and that the full pipeline (snapshot → exact baseline → QUBO → QAOA → verification) runs on public data with full traceability.
- **It does not show** a Kubernetes production snapshot, absolute capacity sizes, or any quantum advantage. The exact classical solver is the strong baseline, and it is faster and exact.
