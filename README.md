# QPlace: Quantum-Assisted Kubernetes Pre-Drain Feasibility

**This effort is supported by Qollab & IonQ**

Before you drain a Kubernetes node for maintenance, can its pods fit somewhere else? QPlace models the cluster from a saved snapshot. It solves the placement exactly with classical methods, encodes it as an exhaustively validated QUBO, and runs QAOA with Qiskit on Qollab. Every quantum sample is checked by an independent verifier. It is read-only and never touches a cluster.

| Headline result | |
|---|---|
| Greedy-trap instance | greedy places 1/2 pods; exact places 2/2 |
| QAOA on Qollab (hosted, 256 shots) | 2/2 placement sampled **84×** on the built-in simulator and **83×** on IonQ's simulator (Aria 1 noise model). Random guessing gets 12.5% (32×) |
| Public data | on a Google cluster trace instance (CC-BY 4.0), greedy places 2/3 and exact places 3/3. QAOA samples the unique optimum (local simulator) |
| Claim | a working, verified hybrid pipeline. **No quantum advantage is claimed** |

The Qollab project page and demo are in [`qollab/`](qollab/README.md). Hosted evidence: [`docs/m3/hosted-evidence.md`](docs/m3/hosted-evidence.md). Provenance: [`docs/m5/provenance.md`](docs/m5/provenance.md).

**Status:** M1 (cluster state modeling), M2 (classical placement engine) and M3 (QAOA placement experiment) are complete. M3 includes hosted Qollab runs. M4 (CLI and live cluster) is not started.

| Done | Not yet |
|---|---|
| M1: snapshot schema, exact quantities, effective requests, node headroom, drain classification, PDB relevance, `ELIGIBLE`/`INELIGIBLE`/`UNKNOWN` eligibility | CLI, reporting (M4+) |
| M2: placement problem (binary `x[pod,node]` form), first-fit-decreasing greedy, exact branch-and-bound (≤8 candidates × 6 destinations by default), independent verifier | Live Kubernetes connector, kubectl plugin / CLI |
| M2: assessment `MODELED_FEASIBLE` / `MODELED_BLOCKED` / `REVIEW_REQUIRED` | Any cluster mutation (never) |
| M3: QUBO/Ising with exact constraint encodings and exhaustive proof, QAOA (p=1, p=2) on Qiskit simulators, verified sample decoding, one-job Qollab demo in `qollab/` | Quantum hardware results; any quantum-advantage claim |

`MODELED_FEASIBLE` means the modeled pods fit by requested resources. It is **not** "safe to drain". See `docs/m2/limitations.md`.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest                                  # run the test suite
.venv/bin/python scripts/generate_m1_artifacts.py --check   # M1 reference artifacts are current
.venv/bin/python scripts/generate_m2_artifacts.py --check   # M2 reference artifacts are current

.venv/bin/pip install -e '.[test,quantum]'                  # Qiskit 2.x + qiskit-optimization 0.7 (M3)
.venv/bin/python scripts/generate_m3_artifacts.py check     # M3 artifacts and qollab/placement_data.py are current
(cd qollab && ../.venv/bin/python main.py)                  # Qollab demo, local fallback simulator
```

```python
from qplace import load_snapshot, build_cluster_model, write_artifacts

model = build_cluster_model(load_snapshot("fixtures/m1/fixture_a_healthy.json"))
write_artifacts(model, "out/")
print(model.eligible_destinations("shop/cart-0"))

from qplace.placement_assess import assess_placement
report = assess_placement(model)
print(report.assessment, report.exact.result.assignment_map())
```

## Layout

| Path | Content |
|---|---|
| `src/qplace/` | M1 library (`snapshot`, `quantity`, `nodes`, `pods`, `resources`, `eligibility`, `drain`, `model`, `serialize`) |
| `src/qplace/placement_*.py` | M2 (`problem`, `greedy`, `exact`, `verify`, `result`, `assess`, `serialize`) |
| `src/qplace/quantum_*.py`, `qollab_export.py` | M3 (`formulation`, `qaoa`, `decode`, `experiment`) |
| `qollab/` | standalone Qollab project (one QAOA job on the selected backend) |
| `src/qplace/schemas/` | JSON Schemas: snapshot input, M1, M2 and M3 artifacts |
| `fixtures/m1/`, `fixtures/m2/`, `fixtures/m3/` | synthetic reference snapshots (3 + 6 + 1) |
| `fixtures/m5/` | public-data instance derived from the Google cluster trace (`scripts/derive_google_trace_fixture.py`) |
| `artifacts/m1/`, `artifacts/m2/`, `artifacts/m3/` | generated reference artifacts |
| `docs/m1/`, `docs/m2/`, `docs/m3/` | contracts, semantics, optimization and quantum models, limitations, validation |
| `reports/m1/`, `reports/m2/`, `reports/m3/` | milestone reports |

## Acknowledgements

Built for the Qollab × IonQ hackathon. The quantum demo targets the Qollab runtime and IonQ remote simulators available there. It uses Qiskit and qiskit-optimization (Apache-2.0) as libraries.

## License

MIT. See [LICENSE](LICENSE).

## Safety

QPlace contains no Kubernetes client and no network code. It never cordons, evicts, deletes or deploys anything. All fixtures are synthetic except `fixtures/m5/google_trace_drain.json`, which is derived from Google's public cluster trace (CC-BY 4.0).
