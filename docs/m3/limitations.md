# M3 limitations

**In short:** M3 shows a correct, verified QAOA placement experiment on tiny instances. It shows **no quantum advantage**. The classical exact solver is faster, proves optimality, and is the authority on every result. Hosted Qollab runs (built-in simulator and IonQ remote simulator, Aria 1 noise model) succeeded. The project owner performed and reported them (`hosted-evidence.md`).

| Area | Limitation |
|---|---|
| Qubit scaling | one qubit per eligible (pod, node) pair, plus slack bits. Real drains (tens of pods × tens of nodes) need hundreds of qubits. The budget is 12 |
| Slack growth | constraints that are not pairwise-exact need `⌈log₂(C′+1)⌉` slack qubits after gcd reduction. Memory limits that aren't Mi-aligned can need 20 or more, so the instance becomes `UNSUPPORTED_SIZE` |
| Objective | the QUBO encodes only M2's primary objective (pods placed). The leximin balance and tie-break are applied afterwards, by scoring verified samples with `evaluate_objective` |
| Training | noiseless exact-statevector training. It does not model noise or shot-based gradients. COBYLA from a grid start can stall: scenario G at p = 1 reaches an optimal-count placement with 1.5% probability, which is **worse than uniform random guessing (4.7%)** |
| Penalty stiffness | slack penalties grow as `P·C′²`, and the circuit divides `H` by its largest coefficient. On wide slack constraints the −1 placement rewards become tiny after scaling (scenario G scale 9.5). This flattens the training landscape |
| Small-instance results | with 3 qubits, random guessing samples the greedy-trap optimum 12.5% of the time. The demo prints this baseline next to its own rate and makes no advantage claim |
| Sampling variability | results depend on the seed. Five seeds are a small sample |
| Invalid samples | many shots decode to infeasible placements. They are counted and rejected, never repaired |
| Overhead | training needs about 180–300 estimator evaluations. The classical exact solver takes < 1 ms on every instance |
| Trivial instances | 1-qubit and coupling-free instances succeed trivially |
| Simulation only | all measured results come from local noiseless simulators. None come from hardware |
| Hosted runtime | Confirmed by the owner's hosted runs: `backend` injection and the `main()` call both work. Each hosted backend ran once with 256 shots, so the success rates are imprecise. If a runtime ran `main.py` as `__main__` on non-browser CPython **and** auto-called `main()`, two jobs would be sent. The guard prevents this only under Pyodide (`emscripten`) |
| Kubernetes model | all M1/M2 limitations still apply: unknown constraints, the requested-resource model, no PDB or scheduler simulation. A verified quantum placement is not a drain approval |
| Advantage | **no quantum advantage is claimed or supported** |

## Attribution and originality

- **Prior work by this team:** M1/M2 (the cluster model and classical solvers) were developed earlier in this repository.
- **External code:** M3 uses Qiskit and qiskit-optimization APIs (Apache-2.0) as libraries. It does not copy example code. The QAOA circuit structure is the textbook construction.
- **Platform and sponsor:** the demo targets Qollab, and IonQ remote simulators as available on Qollab.
