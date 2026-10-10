# QPlace: Quantum-Assisted Kubernetes Pre-Drain Feasibility

**This effort is supported by Qollab & IonQ**

> Before you drain a Kubernetes node for maintenance, can its pods fit somewhere else? QPlace answers that with an exact classical solver, then runs QAOA on Qollab to see whether a quantum sampler finds the same plan. Every quantum sample is checked by an independent verifier.

**Press Run** on the built-in simulator, or on IonQ's simulator. One job, 3 qubits, about 1 second.

| | |
|---|---|
| Theme | Quantum Impact (hybrid quantum-classical; strong classical baseline; public-data experiment) |
| Code | https://github.com/edeediong/qplace-k8s (MIT) |
| Result | QAOA sampled the complete placement that greedy misses in **84/256** (built-in) and **83/256** (IonQ simulator, Aria 1 noise model) shots. Random guessing gets 12.5% |
| Claim | a working, verified hybrid pipeline. **No quantum advantage is claimed** |

## 1. The problem

Draining a node evicts its pods, and controllers recreate them on other nodes. That only works if every pod fits somewhere:

- each node has limited CPU, memory and pod slots;
- some pods may only run on certain nodes (node selectors, taints).

A simple **greedy** plan can fail even when a valid plan exists. In the demo:

| | CPU free | Label |
|---|---|---|
| node-a | 2500m | disk = ssd |
| node-b | 2500m | disk = hdd |

| Pod | CPU request | Constraint |
|---|---|---|
| batch | 2000m | none |
| indexer | 1500m | needs disk = ssd, so node-a only |

1. Greedy (largest first, first node that fits) puts **batch on node-a**, leaving 500m.
2. Indexer then has nowhere to go: **1 of 2 placed ✗**.
3. The exact solver puts batch on **node-b** and indexer on **node-a**: **2 of 2 ✓**.

## 2. What QPlace does

```
saved cluster snapshot (JSON)
  → M1: model nodes, pods, effective requests (init containers, sidecars, overhead…), eligibility
  → M2: placement problem  x[pod,node] ∈ {0,1}
        greedy baseline  +  exact branch-and-bound (proven optimum)  +  independent verifier
  → M3: QUBO → Ising → QAOA (Qiskit) → measured bitstrings → decode → verify → compare
```

It is read-only. It never touches a cluster.

## 3. The quantum part

| Step | Detail |
|---|---|
| Variables | one qubit per allowed pod→node pair: q0 = batch→node-a, q1 = batch→node-b, q2 = indexer→node-a |
| QUBO | `−x0 − x1 − x2 + 3·x0·x1 + 3·x0·x2`. This rewards each placed pod and penalizes placing batch twice or overloading node-a's CPU |
| Correctness | before QAOA runs, all 2³ states are checked: the lowest-energy states are exactly the feasible placements with the most pods |
| Ising | `x = (1 − z)/2` gives `h = (−1, −¼, −¼)`, `J01 = J02 = ¾` |
| QAOA | p = 2, angles trained offline (exact statevector + COBYLA), then **one** circuit, one `backend.run` |
| Decoding | Qiskit bitstrings are little-endian: `110` = q1 and q2 = batch→node-b, indexer→node-a |
| Verification | every sampled placement is checked for at-most-one and CPU/memory/pod-slot limits. The repository tests prove this check equals QPlace's independent verifier on every bitstring |

## 4. Results

**Hosted on Qollab, 2026-10-10, 256 shots each** (run by the project owner):

| Backend | Samples placing 2/2 | Feasible samples | Best verified placement |
|---|---|---|---|
| Built-in simulator (`basic_simulator`) | **84 / 256 (32.8%)** | 234 / 256 | batch → node-b, indexer → node-a |
| IonQ simulator, Aria 1 noise model (`ionq_simulator`) | **83 / 256 (32.4%)** | 229 / 256 | batch → node-b, indexer → node-a |
| Random guessing (1 of 8 bitstrings) | 12.5% | 62.5% | – |

The IonQ result comes from a **simulator** with a hardware noise model, not quantum hardware. The IonQ run printed a 29-qubit circuit because the simulator's target is 29 qubits wide and transpilation pads the circuit. Only 3 qubits are active, and the console now says so.

**Local experiments** (repository, noiseless simulator, 11 instances) found:

- The best QAOA sample matched the exact solver on every instance.
- At p = 2, QAOA beat random guessing on every non-trivial instance.
- At p = 1, it was **worse** than random on two instances.

On a **public Google cluster trace (CC-BY 4.0)** instance, greedy places 2 of 3 tasks and exact places 3 of 3. QAOA (5 qubits, local) samples the unique optimum at 2.2× random.

## 5. How to run

**On Qollab:** open the project, choose the **built-in simulator** (or IonQ's simulator), and press **Run**. Qollab supplies `backend` and calls `main()` in `main.py`. The demo:

1. prints the classical baselines;
2. submits one QAOA circuit;
3. prints the shots, the feasible and optimal counts, the random-guess baseline and the best verified placement.

**Locally:** `pip install qiskit`, then `python main.py`. This uses Qiskit's BasicSimulator and labels the run `LOCAL FALLBACK`.

| File | Role |
|---|---|
| `main.py` | entry point (`main(shots=256)`) |
| `quantum_demo.py` | circuit, decoding, feasibility check, one-job runner |
| `placement_data.py` | generated from the repository: qubit map, Ising coefficients, capacities, baselines, trained angles |
| `requirements.txt` | `qiskit` |

## 6. Reproduce and extend

```bash
git clone https://github.com/edeediong/qplace-k8s && cd qplace-k8s
python3 -m venv .venv && .venv/bin/pip install -e '.[test,quantum]'
.venv/bin/python -m pytest -q                               # full suite: M1–M3 plus public-data tests
.venv/bin/python scripts/generate_m3_artifacts.py check     # verify committed quantum artifacts
```

| Output | Behavior |
|---|---|
| QUBO, Ising, problem files | deterministic, byte-checked |
| Trained angles, sampled counts, timings | observational: they vary with platform and seed |

To add an instance, write a snapshot JSON (see `docs/m1/data-contract.md`) and run `generate_m3_artifacts.py generate <name>`.

## 7. Limitations

- **Small instances:** one qubit per pod→node pair, with a 12-qubit budget. Real drains are far larger.
- **No quantum advantage:** the exact classical solver is faster and proves optimality.
- **Inputs:** synthetic snapshots, plus one public-trace instance (Borg tasks, which only stand in for Kubernetes pods). There is no live cluster connection.
- **Requested-resource model only:** QPlace doesn't predict the scheduler's choices or simulate controllers or PodDisruptionBudgets. A feasible result is **not** a safe-to-drain guarantee.
- **Unknown constraints:** affinity, storage topology, GPUs and similar give `UNKNOWN`. Those pods are never placed by assumption.
- **Shot noise:** each hosted run is one run of 256 shots.

## 8. Provenance and credits

- **Built during the hackathon window:** all code, data and documentation were created during the build window (first commit 2026-10-10). There is no earlier codebase.
- **AI assistance:** written with AI coding agents (Anthropic's Claude Code), directed and reviewed by the author. Details are in the repository's `docs/m5/provenance.md`.
- **Libraries:** Qiskit and qiskit-optimization (Apache-2.0), used as libraries. The QAOA circuit is the standard construction (Farhi, Goldstone & Gutmann, 2014).
- **Data:** Google cluster-usage trace `clusterdata-2011-2` (CC-BY 4.0; Reiss, Wilkes & Hellerstein, 2011). Kubernetes semantics follow the official Kubernetes documentation.

MIT License © 2026 Edidiong Etuk. **This effort is supported by Qollab & IonQ**
