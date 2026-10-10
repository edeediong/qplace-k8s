# Qollab runtime

**In short:** `qollab/` is a self-contained Python/Qiskit project. Qollab injects the selected backend as the global `backend` and calls `main()`. The demo builds the greedy-trap QAOA circuit with angles trained offline, submits it with **exactly one** `backend.run(...)` call, then decodes and checks the counts. Hosted execution is **not verified by me**: I have no Qollab access. It must be run in the Qollab web app (see §5).

## 1. Files

| File | Role |
|---|---|
| `qollab/main.py` | entry point; `main(shots: int = 256)` |
| `qollab/quantum_demo.py` | circuit (`h`, `rz`, `rx`, `cx`, `measure` only), little-endian decoder, feasibility check in original integer units, one-job runner |
| `qollab/placement_data.py` | **generated** by `scripts/generate_m3_artifacts.py generate`: qubit map, Ising coefficients, capacity data, greedy/exact baselines, trained p=2 angles. `check` re-derives it from the committed artifacts |
| `qollab/requirements.txt` | `qiskit>=1.0` (Qollab provides the backend; no `qiskit-ionq` needed) |
| `qollab/README.md` | how to run |

The demo does not import `qplace`. Tests in `tests/test_quantum_experiment.py` keep it equivalent to the repository:

- the demo circuit's unitary equals the repository circuit (`Operator.equiv`);
- the demo's decoding and feasibility agree with qplace's decoder and `verify_placement` on **every** bitstring;
- `placement_data.py` matches the committed artifacts.

## 2. Backend handling

```python
injected = globals().get("backend")          # Qollab's selected backend
if injected is None and sys.platform == "emscripten":
    raise RuntimeError(...)                  # never silently substitute a different backend in the browser
# local only: qiskit BasicSimulator, printed as "LOCAL FALLBACK (not Qollab)"
```

- `main()` is called by Qollab. The `if __name__ == "__main__" and sys.platform != "emscripten": main()` guard runs it only for local command-line use. That way Qollab never triggers a second job.
- If the backend exposes a `target`, the circuit is transpiled against it. If transpiling fails, the basic-gate circuit is submitted unchanged, and the output says which happened.

## 3. One-job discipline (IonQ)

According to Qollab hackathon projects, Qollab's IonQ remote simulator accepts one single-circuit job per run.

- The demo calls `backend.run(circuit, shots=...)` once, with a single circuit, never a list.
- No parameter optimization happens against the backend.
- `test_demo_uses_injected_backend_with_exactly_one_job` wraps a real local simulator, counts calls, and asserts exactly 1. That is a mock-level check, **not** hosted evidence.

## 4. Pyodide compatibility

- Imports are `sys`, `qiskit` (`QuantumCircuit`, optional `transpile`) and local modules only.
- No numpy-specific code, network, files, threads or native extensions beyond Qiskit itself.
- Whether Qollab's Pyodide build ships a compatible Qiskit is **unverified**.

## 5. Hosted validation procedure (for the submitter)

1. Create a Python/Qiskit project in Qollab. Upload exactly `main.py`, `quantum_demo.py`, `placement_data.py`, `requirements.txt` and `README.md` from `qollab/`. Do not upload `__pycache__/` or `.DS_Store`.
2. Select the **built-in simulator**, press Run, and copy the console output.
3. Select **IonQ (remote simulator)**, press Run once, and copy the output.
4. Record both runs in `reports/m3/m3_summary.md` §E. Until then, the hosted gate is **BLOCKED**.

Expected output shape (the values come from the actual run):

```
QPLACE: QUANTUM POD PLACEMENT
...
Greedy: 1/2 pods placed
Exact:  2/2 pods placed
QUANTUM
Algorithm: QAOA (p=2, angles trained offline)
Backend: <name>  [Qollab-selected backend]
...
Quantum optimality: NOT PROVEN
```
