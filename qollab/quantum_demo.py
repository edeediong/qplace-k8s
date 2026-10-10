"""Browser-safe QAOA demo logic for Qollab (Qiskit only; no qplace import, no network, no files).

Mirrors qplace.quantum_qaoa.build_qaoa_circuit and qplace.quantum_decode. The
qplace test suite checks that this circuit equals the repository circuit and
that this feasibility check agrees with qplace's independent verifier on every
bitstring of the demo instance.
"""

from qiskit import QuantumCircuit


def build_circuit(data: dict, measure: bool = True) -> QuantumCircuit:
    """QAOA circuit with the trained angles bound. RZZ is written as CX-RZ-CX (identical unitary)
    so the circuit only needs h, rz, rx, cx and measure."""
    n, s = data["num_qubits"], data["scale"]
    gammas, betas = data["qaoa"]["gamma"], data["qaoa"]["beta"]
    qc = QuantumCircuit(n, name="qplace_qaoa_demo")
    qc.h(range(n))
    for gamma, beta in zip(gammas, betas):
        for i, c in sorted((int(k), v) for k, v in data["h"].items()):
            qc.rz(2 * gamma * c / s, i)
        for key, c in sorted(data["J"].items(), key=lambda kv: tuple(int(x) for x in kv[0].split(","))):
            i, j = (int(x) for x in key.split(","))
            qc.cx(i, j)
            qc.rz(2 * gamma * c / s, j)
            qc.cx(i, j)
        for i in range(n):
            qc.rx(2 * beta, i)
    if measure:
        qc.measure_all()
    return qc


def decode(key: str, data: dict) -> list:
    """Little-endian counts key -> list of (pod, node). Qubit i is character [-1 - i]."""
    key = key.replace(" ", "")
    n = data["num_qubits"]
    if len(key) != n or set(key) - {"0", "1"}:
        raise ValueError(f"unexpected bitstring {key!r}")
    bits = [int(key[n - 1 - i]) for i in range(n)]
    return [tuple(data["assignment_qubits"][i]) for i in range(len(data["assignment_qubits"])) if bits[i]], bits


def feasible(bits: list, data: dict) -> bool:
    """At most one destination per pod, and every capacity limit, in original integer units."""
    pods = [data["assignment_qubits"][i][0] for i in range(len(data["assignment_qubits"])) if bits[i]]
    if len(pods) != len(set(pods)):
        return False
    for _node, _resource, cap, terms in data["capacity"]:
        if sum(a for v, a in terms if bits[v]) > cap:
            return False
    return True


def analyse(counts: dict, data: dict) -> dict:
    rows = []
    for key, count in counts.items():
        pairs, bits = decode(key, data)
        rows.append({"bitstring": key.replace(" ", ""), "count": int(count), "pairs": pairs,
                     "feasible": feasible(bits, data), "placed": len(pairs)})
    rows.sort(key=lambda r: (-r["count"], r["bitstring"]))
    shots = sum(r["count"] for r in rows)
    ok = [r for r in rows if r["feasible"]]
    best = max(ok, key=lambda r: (r["placed"], r["count"], [-ord(ch) for ch in r["bitstring"]]), default=None)
    exact = data["classical"]["exact_placed"]
    return {
        "shots": shots,
        "rows": rows,
        "best": best,
        "feasible_shots": sum(r["count"] for r in ok),
        "exact_count_shots": sum(r["count"] for r in ok if r["placed"] == exact),
    }


def uniform_baseline(data: dict) -> dict:
    """Fraction of all 2^n bitstrings that are feasible / reach the exact count: what random guessing scores."""
    n, exact = data["num_qubits"], data["classical"]["exact_placed"]
    feasible_states = optimal_states = 0
    for mask in range(1 << n):
        pairs, bits = decode(format(mask, f"0{n}b"), data)
        if feasible(bits, data):
            feasible_states += 1
            optimal_states += len(pairs) == exact
    return {"feasible": feasible_states / (1 << n), "optimal": optimal_states / (1 << n)}


def prepare_for_backend(qc: QuantumCircuit, backend) -> tuple:
    """Transpile against the backend's target when it exposes one; otherwise submit the basic-gate circuit."""
    if getattr(backend, "target", None) is None:
        return qc, "not transpiled (backend exposes no target)"
    from qiskit import transpile

    try:
        return transpile(qc, backend=backend, optimization_level=1, seed_transpiler=7), "transpiled for backend target"
    except Exception as exc:  # noqa: BLE001 - report and fall back to the portable circuit
        return qc, f"transpile failed ({type(exc).__name__}); submitted basic-gate circuit"


def active_qubits(qc: QuantumCircuit) -> int:
    """Qubits touched by any operation. A transpiled circuit is as wide as the backend target,
    so its width can exceed the qubits actually used (for example, 29 on a 29-qubit simulator target)."""
    try:
        return len({qc.find_bit(q).index for inst in qc.data if inst.operation.name != "barrier" for q in inst.qubits})
    except Exception:  # noqa: BLE001 - reporting only; never block the run
        return -1


def run_once(backend, data: dict, shots: int) -> tuple:
    """Exactly one backend.run call. Returns (counts, transpile_note, submitted_circuit)."""
    qc, note = prepare_for_backend(build_circuit(data), backend)
    job = backend.run(qc, shots=shots)
    return dict(job.result().get_counts()), note, qc
