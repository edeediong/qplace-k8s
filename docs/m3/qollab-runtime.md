# Qollab runtime

**In short:** `qollab/` is a self-contained Python/Qiskit project. Qollab injects the selected backend as the global `backend` and calls `main()`. The demo builds the greedy-trap QAOA circuit with angles trained offline, submits it with **exactly one** `backend.run(...)` call, then decodes and checks the counts. Hosted execution **succeeded** on Qollab's built-in simulator and on the IonQ remote simulator (Aria 1 noise model), as run and reported by the project owner. See [hosted-evidence.md](hosted-evidence.md).

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

## 5. Hosted run 1: `__future__` import rejected (fixed)

**Observed in Qollab** on the first hosted upload, during "Compiling / initializing runtime…":

```text
Traceback (most recent call last):
  File "<code>", line 11
SyntaxError: from __future__ imports must occur at the beginning of the file
```

**Cause:**

- `qollab/main.py` line 11 was `from __future__ import annotations`.
- `qollab/quantum_demo.py` had the same import.
- Python accepts a `__future__` import only at the very top of a compilation unit, after at most a docstring.
- The hosted runtime compiles the project as `<code>` in a way that does not treat the import as first. Most likely it runs setup code in the same compilation unit.

The reported line number equals the line in our unmodified file. We cannot see Qollab's internals, so how the host wraps the source is an **assumption**. Removing the imports avoids the problem in either case.

**Fix (commit "fix(qollab): remove incompatible future imports from hosted runtime"):**

- Removed `from __future__ import annotations` from both files.
- The remaining annotations (`str`, `int`, `bool`, `dict`, `list`, `tuple`, `QuantumCircuit`) are evaluated eagerly. All of them are defined at definition time.
- No circuit, angle, coefficient, decoding or feasibility logic changed. `placement_data.py` is unchanged (it never had the import).

**Regression tests (`tests/test_quantum_experiment.py`):**

| Test | What it checks |
|---|---|
| `test_qollab_files_have_no_future_imports` | AST scan of `main.py`, `quantum_demo.py` and `placement_data.py` |
| `test_qollab_files_compile_after_a_runtime_prelude` | each file compiles alone, and also with executable setup code prepended (a simulated host) |
| `test_runtime_prelude_check_detects_the_original_failure` | the same prepend check reproduces the exact hosted `SyntaxError` on the pre-fix pattern, so the test can fail |

These tests are compatibility checks. They are **not** proof of hosted execution.

## 6. Hosted validation procedure (for the submitter)

1. Create a Python/Qiskit project in Qollab. Upload exactly `main.py`, `quantum_demo.py`, `placement_data.py`, `requirements.txt` and `README.md` from `qollab/`. Do not upload `__pycache__/` or `.DS_Store`.
2. Select the **built-in simulator**, press Run, and copy the console output.
3. Select **IonQ (remote simulator)**, press Run once, and copy the output.
4. Record both runs in `reports/m3/m3_summary.md` §E and `docs/m3/hosted-evidence.md`. Done for commit `a82dc48` on 2026-10-10.

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
