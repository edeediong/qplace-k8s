# QPlace

QPlace is a quantum-assisted Kubernetes pre-drain feasibility advisor. It is read-only.

**Status: Milestone 1 (cluster state modeling) only.** QPlace loads a saved cluster snapshot. It models nodes, pods, effective resource requests, drain-target classification and per-node eligibility. It writes two deterministic JSON artifacts. There is no placement solver, no QAOA, no CLI and no live cluster access yet.

| Done in M1 | Not yet |
|---|---|
| Versioned snapshot schema and validation | Greedy / exact / QAOA placement (M2+) |
| Exact CPU/memory quantity parsing | Live Kubernetes connector |
| Effective requests: init containers, sidecars, overhead, pod-level resources, resize | kubectl plugin / CLI |
| Node reservations and headroom | Any cluster mutation (never) |
| Drain classification, advisory PDB relevance | |
| Node eligibility: `ELIGIBLE` / `INELIGIBLE` / `UNKNOWN` | |

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest                                  # run the test suite
.venv/bin/python scripts/generate_m1_artifacts.py --check   # reference artifacts are current
```

```python
from qplace import load_snapshot, build_cluster_model, write_artifacts

model = build_cluster_model(load_snapshot("fixtures/m1/fixture_a_healthy.json"))
write_artifacts(model, "out/")
print(model.eligible_destinations("shop/cart-0"))
```

## Layout

| Path | Content |
|---|---|
| `src/qplace/` | library (`snapshot`, `quantity`, `nodes`, `pods`, `resources`, `eligibility`, `drain`, `model`, `serialize`) |
| `src/qplace/schemas/` | JSON Schemas: snapshot input and both artifacts |
| `fixtures/m1/` | three synthetic reference snapshots |
| `artifacts/m1/` | generated reference artifacts |
| `docs/m1/` | data contract, Kubernetes semantics, limitations, validation |
| `reports/m1/` | milestone report |

## License

MIT. See [LICENSE](LICENSE).

## Safety

QPlace contains no Kubernetes client and no network code. It never cordons, evicts, deletes or deploys anything. All fixtures are synthetic.
