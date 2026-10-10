# QPlace

QPlace is a quantum-assisted Kubernetes pre-drain feasibility advisor. It is read-only.

**Status: M1 (cluster state modeling) and M2 (classical placement engine).** QPlace loads a saved cluster snapshot and models nodes, pods, effective requests, drain-target classification and node eligibility (M1). It then checks whether the replacement pods could be packed onto eligible destinations by requested CPU, memory and pod slots, using a greedy solver and an exact solver. Every answer goes through an independent verifier (M2). There is no QAOA, no CLI and no live cluster access yet.

| Done | Not yet |
|---|---|
| M1: snapshot schema, exact quantities, effective requests, node headroom, drain classification, PDB relevance, `ELIGIBLE`/`INELIGIBLE`/`UNKNOWN` eligibility | Qiskit / QAOA solver (M3) |
| M2: placement problem (binary `x[pod,node]` form), first-fit-decreasing greedy, exact branch-and-bound (≤8 candidates × 6 destinations by default), independent verifier | Live Kubernetes connector, kubectl plugin / CLI |
| M2: assessment `MODELED_FEASIBLE` / `MODELED_BLOCKED` / `REVIEW_REQUIRED` | Any cluster mutation (never) |

`MODELED_FEASIBLE` means the modeled pods fit by requested resources. It is **not** "safe to drain". See `docs/m2/limitations.md`.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest                                  # run the test suite
.venv/bin/python scripts/generate_m1_artifacts.py --check   # M1 reference artifacts are current
.venv/bin/python scripts/generate_m2_artifacts.py --check   # M2 reference artifacts are current
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
| `src/qplace/schemas/` | JSON Schemas: snapshot input, M1 artifacts, M2 artifacts |
| `fixtures/m1/`, `fixtures/m2/` | synthetic reference snapshots (3 + 6 scenarios) |
| `artifacts/m1/`, `artifacts/m2/` | generated reference artifacts |
| `docs/m1/`, `docs/m2/` | contracts, semantics, optimization model, limitations, validation |
| `reports/m1/`, `reports/m2/` | milestone reports |

## License

MIT. See [LICENSE](LICENSE).

## Safety

QPlace contains no Kubernetes client and no network code. It never cordons, evicts, deletes or deploys anything. All fixtures are synthetic.
