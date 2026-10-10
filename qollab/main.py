"""QPlace on Qollab: one QAOA job for a Kubernetes pre-drain placement problem.

Qollab injects the selected quantum backend as the global ``backend`` and calls
``main()``. This file submits exactly one circuit with one ``backend.run`` call,
so it respects IonQ's one-job-per-run limit. QAOA angles were trained offline
(see placement_data.py); nothing is optimized against the remote backend.

Run locally:  python main.py   (falls back to Qiskit's BasicSimulator, and says so)
"""

import sys

from placement_data import DATA
from quantum_demo import active_qubits, analyse, run_once, uniform_baseline


def _short(pod: str) -> str:
    name = pod.split("/", 1)[-1]
    parts = name.split("-")
    return parts[0] if len(parts) > 2 else name


def _resolve_backend():
    injected = globals().get("backend")
    if injected is not None:
        return injected, "Qollab-selected backend"
    if sys.platform == "emscripten":
        raise RuntimeError("No Qollab backend was injected; refusing to substitute a different backend.")
    from qiskit.providers.basic_provider import BasicSimulator

    return BasicSimulator(), "LOCAL FALLBACK (not Qollab)"


def main(shots: int = 256):
    selected, origin = _resolve_backend()
    name = getattr(selected, "name", type(selected).__name__)
    name = name() if callable(name) else name
    c = DATA["classical"]
    n_cand = len(DATA["candidates"])

    print("QPLACE: QUANTUM POD PLACEMENT")
    print()
    print("Scenario: Kubernetes pre-drain planning (synthetic snapshot)")
    print(f"Target node: {DATA['target_node']}")
    print(f"Placement candidates: {n_cand}")
    print(f"Destination nodes: {len(DATA['destinations'])}  ({', '.join(DATA['destinations'])})")
    print()
    print("CLASSICAL (computed offline by the qplace repository)")
    print(f"Greedy: {c['greedy_placed']}/{n_cand} pods placed")
    print(f"Exact:  {c['exact_placed']}/{n_cand} pods placed")
    print()

    counts, note, submitted = run_once(selected, DATA, shots)  # the only backend job
    result = analyse(counts, DATA)

    print("QUANTUM")
    print("Algorithm: QAOA (p=%d, angles trained offline)" % DATA["qaoa"]["reps"])
    print(f"Backend: {name}  [{origin}]")
    used = active_qubits(submitted)
    print(f"Logical qubits: {DATA['num_qubits']} (one per allowed pod->node pair)")
    print(f"Submitted circuit: {used if used >= 0 else 'unknown'} active qubits, register width "
          f"{submitted.num_qubits}, depth {submitted.depth()}  ({note})")
    if submitted.num_qubits > DATA["num_qubits"]:
        print("  (width = backend target size after transpilation; the extra qubits are idle)")
    shots = result["shots"]
    uniform = uniform_baseline(DATA)
    print(f"Measured shots: {shots}")
    print(f"Feasible samples (incl. empty placement): {result['feasible_shots']}/{shots}")
    print(f"Samples placing {c['exact_placed']}/{n_cand} pods: {result['exact_count_shots']}/{shots}"
          f"  ({100 * result['exact_count_shots'] / shots:.1f}%)")
    print(f"Random-guess baseline: {100 * uniform['optimal']:.1f}% of uniformly random bitstrings place "
          f"{c['exact_placed']}/{n_cand} pods")
    print()
    best = result["best"]
    if best is None:
        print("No feasible placement was sampled. Quantum result: FAILED for this run.")
    else:
        print(f"Best verified sampled placement ({best['placed']}/{n_cand} pods, seen {best['count']} times):")
        for pod, node in best["pairs"]:
            print(f"  {_short(pod):<10} -> {node}")
        print()
        print("Placement feasibility: VERIFIED by the demo's constraint check (at-most-one + CPU/memory/pod-slot),")
        print("  which the qplace test suite proves equal to its independent verifier on all bitstrings")
        print(f"Greedy placed {c['greedy_placed']}/{n_cand}; this sampled placement places {best['placed']}/{n_cand}.")
        print(f"With only {DATA['num_qubits']} qubits, random guessing also finds it: this run shows the "
              "encoding and pipeline, not an advantage.")
    print("Quantum optimality: NOT PROVEN")
    print()
    print("This is a constrained placement-model")
    print("experiment, not a safe-to-drain guarantee.")
    return {"backend": name, "origin": origin, "shots": result["shots"], "counts": counts,
            "logical_qubits": DATA["num_qubits"], "active_qubits": used, "register_width": submitted.num_qubits,
            "best": None if best is None else {"placed": best["placed"], "pairs": best["pairs"]},
            "feasible_shots": result["feasible_shots"], "exact_count_shots": result["exact_count_shots"],
            "uniform_optimal_rate": uniform["optimal"]}


if __name__ == "__main__" and sys.platform != "emscripten":
    main()
