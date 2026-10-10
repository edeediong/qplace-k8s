# Hosted Qollab evidence (M3 gate 15)

**In short:** the Qollab demo ran successfully in Qollab's hosted runtime on two backends: the built-in simulator, and IonQ's remote simulator with the Aria 1 noise model. Both sampled the complete 2/2 placement that greedy misses, in about 33% of shots. Random guessing would get 12.5%.

**Provenance:** the project owner ran both jobs in the Qollab web app on **2026-10-10**, using the code at commit `a82dc48`. They supplied the results below. The agents did not repeat these hosted runs. The values are recorded as supplied, and every number was checked for internal consistency (§3).

## 1. Results as supplied

| Field | Built-in simulator | IonQ simulator |
|---|---|---|
| Selected in Qollab UI | built-in simulator | **IonQ Aria 1 (25q)** |
| Backend name printed | `basic_simulator` | `ionq_simulator` |
| Backend origin printed | Qollab-selected backend | Qollab-selected backend |
| Hardware? | no (local simulator) | **no: remote simulator using an IonQ hardware noise model** |
| QAOA depth | p = 2 | p = 2 |
| Logical variables (qubits carrying the problem) | 3 | 3 |
| Printed circuit width | 3 | **29** (see §2) |
| Transpiled depth | 16 | 18 |
| Shots | 256 | 256 |
| Feasible samples | 234 / 256 | 229 / 256 |
| Samples placing 2/2 pods | **84 / 256 (32.8125%)** | **83 / 256 (32.421875%)** |
| Random-guess baseline | 12.5% | 12.5% |
| Greedy / exact (offline) | 1/2 / 2/2 | 1/2 / 2/2 |
| Best verified placement | batch → node-b, indexer → node-a | batch → node-b, indexer → node-a |
| Quantum optimality | NOT PROVEN | NOT PROVEN |

IonQ simulator measured distribution (supplied):

| Bitstring | Decoded placement (qubit 0 is the rightmost bit) | Feasible | Shots | Share |
|---|---|---|---:|---:|
| 000 | nothing placed | yes | 1 | 0.39% |
| 001 | batch → node-a | yes | 84 | 32.81% |
| 010 | batch → node-b | yes | 33 | 12.89% |
| 011 | batch → node-a **and** node-b | no (pod placed twice) | 10 | 3.91% |
| 100 | indexer → node-a | yes | 28 | 10.94% |
| 101 | batch + indexer → node-a | no (CPU 3500m > 2500m) | 9 | 3.52% |
| **110** | **batch → node-b, indexer → node-a** | **yes, 2/2 (optimal)** | **83** | **32.42%** |
| 111 | all three | no | 8 | 3.13% |

The raw console text was not attached. If it is available, append it here verbatim.

## 2. Why the IonQ run printed 29 qubits

This is **explained, not an error**, and it was reproduced locally with `qiskit-ionq` 1.1.1 and qiskit 2.5.2:

- `qiskit-ionq`'s `ionq_simulator` backend exposes a **29-qubit target**. Choosing the Aria 1 noise model doesn't change the target size (`num_qubits` stays 29 after `set_options(noise_model="aria-1")`).
- The demo transpiles against `backend.target`. Qiskit's transpiler returns a circuit as wide as the target, so the extra qubits sit idle.
- Transpiling the demo circuit against that target locally gives **width 29, depth 18, 3 classical bits, and active physical qubits {0, 1, 2}**. Depth 18 matches the hosted report exactly.

So the problem used **3 qubits**. 29 is the padded register width, and "Aria 1 (25q)" names the noise model, not the simulator's width.

After these runs, the console wording was changed. It now prints `Logical qubits`, `active qubits` and `register width` separately (`test_demo_reports_active_qubits_separately_from_padded_width`). The circuit, angles and decoding are unchanged. The wording change needs one more hosted run on the built-in simulator to confirm it works there (see the submission checklist).

## 3. Consistency checks on the supplied numbers

| Check | Result |
|---|---|
| IonQ shots sum: 1+84+33+10+28+9+83+8 | 256 ✓ |
| IonQ feasible = 256 − (011 + 101 + 111) = 256 − 27 | 229 ✓ |
| IonQ 2/2 = shots of `110` | 83 ✓ |
| 83/256, 84/256 | 32.421875%, 32.8125% ✓ |
| Random baseline: 1 optimal state out of 2³ = 8 | 12.5% ✓ |
| Local exact distribution, trained angles: P(110) | 0.333 (`artifacts/m3/scenario_b_greedy_trap/comparison_results.json`). Both hosted rates are within sampling noise of it (256 shots: σ ≈ 2.9 percentage points) |
| Feasible-rate expectation (local runs: 0.92 mean) | 234/256 = 0.914, 229/256 = 0.895 ✓ |

The IonQ-noise run is about 1 percentage point lower than the noiseless one, well within shot noise. With 256 shots and one run each, these numbers cannot separate the effect of noise from sampling variation.
