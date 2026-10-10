"""M3 experiment artifacts, uncertainty propagation and the Qollab demo contract."""

import importlib
import json
import sys
from collections import Counter

import jsonschema
import pytest
from qiskit.providers.basic_provider import BasicSimulator
from qiskit.quantum_info import Operator

from qplace import build_cluster_model, load_snapshot
from qplace.placement_verify import verify_placement
from qplace.qollab_export import check_placement_data
from qplace.quantum_experiment import ARTIFACTS, comparison_dict, quantum_problem_dict, run_experiment
from qplace.quantum_qaoa import QAOAConfig, bind, build_qaoa_circuit
from qplace.serialize import dumps
from qplace.snapshot import load_schema
from tests.conftest import ROOT

ART = ROOT / "artifacts" / "m3"
FAST = QAOAConfig(grid_points=6, maxiter=40, shots=256, sampling_seeds=(1, 2))


def fixture(name):
    return next(p for p in (ROOT / "fixtures").glob(f"*/{name}.json"))


@pytest.fixture(scope="module")
def demo():
    sys.path.insert(0, str(ROOT / "qollab"))
    try:
        for mod in ("placement_data", "quantum_demo", "main"):
            sys.modules.pop(mod, None)
        yield importlib.import_module("main"), importlib.import_module("quantum_demo"), \
            importlib.import_module("placement_data").DATA
    finally:
        sys.path.remove(str(ROOT / "qollab"))
        for mod in ("placement_data", "quantum_demo", "main"):
            sys.modules.pop(mod, None)


def test_deterministic_artifacts_are_current_and_schema_valid():
    for d in sorted(ART.iterdir()):
        exp = run_experiment(d.name, build_cluster_model(load_snapshot(fixture(d.name))), reps_list=())
        for name, (builder, deterministic) in ARTIFACTS.items():
            committed = json.loads((d / name).read_text())
            schema = load_schema("m3-" + name[:-5].replace("_", "-") + "-1.0.schema.json")
            jsonschema.Draft202012Validator(schema).validate(committed)
            if deterministic:
                assert (d / name).read_text() == dumps(builder(exp)), (d.name, name)


def test_unknown_inputs_are_preserved_not_resolved():
    exp = run_experiment("scenario_e_uncertain", build_cluster_model(load_snapshot(fixture("scenario_e_uncertain"))),
                         qaoa_config=FAST, reps_list=(1,))
    qp = quantum_problem_dict(exp)
    pods = {q.get("pod") for q in qp["qubits"]}
    assert pods == {"apps/plain-3c4d5e-aaaaa"}  # unknown-eligibility and unknown-request pods get no qubits
    assert qp["m2_assessment"] == "REVIEW_REQUIRED" and any("ELIGIBILITY_UNKNOWN" in c for c in qp["m2_conditions"])
    cmp = comparison_dict(exp)
    assert cmp["claims"]["operational_assessment"] == "REVIEW_REQUIRED"
    assert cmp["claims"]["quantum_optimality_proven"] is False


def test_comparison_against_classical_baselines_on_greedy_trap():
    exp = run_experiment("scenario_b_greedy_trap", build_cluster_model(load_snapshot(fixture("scenario_b_greedy_trap"))),
                         qaoa_config=FAST, reps_list=(2,))
    cmp = comparison_dict(exp)
    assert cmp["classical"]["greedy"]["placed"] == 1 and cmp["classical"]["exact"]["placed"] == 2
    (q,) = cmp["quantum"]
    assert q["solver_result"]["optimality_proven"] is False and q["sampler_jobs"] == 2
    if q["best_verified_sample"] is not None:
        pairs = [(a["pod"], a["node"]) for a in q["best_verified_sample"]["assignment"]]
        assert verify_placement(exp.model, pairs).valid
        assert q["optimality_gap_pods"] == 2 - q["best_verified_sample"]["placed"]


def test_placement_data_matches_committed_artifacts():
    assert check_placement_data(ROOT / "qollab" / "placement_data.py", ART / "scenario_b_greedy_trap", 2)


def test_demo_circuit_is_the_repository_circuit(demo):
    _, qd, data = demo
    exp = run_experiment("scenario_b_greedy_trap",
                         build_cluster_model(load_snapshot(fixture("scenario_b_greedy_trap"))), reps_list=())
    repo = bind(build_qaoa_circuit(exp.formulation.ising, data["qaoa"]["reps"], measure=False),
                data["qaoa"]["gamma"], data["qaoa"]["beta"])
    assert Operator(qd.build_circuit(data, measure=False)).equiv(Operator(repo))
    assert data["assignment_qubits"] == [list(v) for v in exp.formulation.assignment_vars]


def test_demo_decoding_and_feasibility_match_repository_on_every_bitstring(demo):
    _, qd, data = demo
    exp = run_experiment("scenario_b_greedy_trap",
                         build_cluster_model(load_snapshot(fixture("scenario_b_greedy_trap"))), reps_list=())
    n = data["num_qubits"]
    for mask in range(1 << n):
        key = format(mask, f"0{n}b")
        pairs, bits = qd.decode(key, data)
        from qplace.quantum_decode import bits_from_key
        repo_pairs = exp.formulation.decode(bits_from_key(key, n))
        assert pairs == repo_pairs
        assert qd.feasible(bits, data) == verify_placement(exp.model, repo_pairs).valid


class CountingBackend:
    """Wraps a real local simulator and records every run() call."""

    def __init__(self):
        self.inner = BasicSimulator()
        self.calls = Counter()
        self.name = "counting_basic_simulator"

    @property
    def target(self):
        return self.inner.target

    def run(self, circuits, **kwargs):
        self.calls["run"] += 1
        assert not isinstance(circuits, list), "Qollab IonQ accepts single-circuit jobs only"
        return self.inner.run(circuits, **kwargs)


def test_demo_uses_injected_backend_with_exactly_one_job(demo, capsys):
    main_mod, _, data = demo
    fake = CountingBackend()
    main_mod.backend = fake
    try:
        result = main_mod.main(shots=128)
    finally:
        del main_mod.backend
    assert fake.calls["run"] == 1
    assert result["origin"] == "Qollab-selected backend" and result["shots"] == 128
    out = capsys.readouterr().out
    assert "Quantum optimality: NOT PROVEN" in out and "not a safe-to-drain guarantee" in out


def test_demo_refuses_to_substitute_backend_in_browser(demo, monkeypatch):
    main_mod, _, _ = demo
    monkeypatch.setattr(sys, "platform", "emscripten")
    with pytest.raises(RuntimeError, match="refusing to substitute"):
        main_mod.main(shots=8)


def test_demo_reports_failure_when_no_sample_is_feasible(demo, capsys):
    main_mod, qd, data = demo

    class AllInfeasible:
        name = "fake"
        target = None

        def run(self, qc, shots):
            class Job:
                def result(self_inner):
                    class R:
                        def get_counts(self_r):
                            return {"101": shots}
                    return R()
            return Job()

    main_mod.backend = AllInfeasible()
    try:
        result = main_mod.main(shots=16)
    finally:
        del main_mod.backend
    assert result["best"] is None and "No feasible placement was sampled" in capsys.readouterr().out


def test_quantum_code_has_no_network_or_cluster_access():
    import ast
    banned = {"kubernetes", "requests", "httpx", "urllib", "http", "socket", "subprocess", "aiohttp", "qiskit_ionq"}
    files = list((ROOT / "src" / "qplace").glob("quantum_*.py")) + list((ROOT / "qollab").glob("*.py")) + [
        ROOT / "src" / "qplace" / "qollab_export.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            assert not [n for n in names if n.split(".")[0] in banned], path


# --- regression tests for the independent M3 review ---------------------------------------


def test_review_f1_uniform_random_baseline_is_reported_and_correct():
    exp = run_experiment("scenario_g_triple_capacity",
                         build_cluster_model(load_snapshot(fixture("scenario_g_triple_capacity"))),
                         qaoa_config=FAST, reps_list=(1,))
    cmp = comparison_dict(exp)
    u = cmp["uniform_random_baseline"]
    f = exp.formulation
    n = f.qubo.num_variables
    from qplace.quantum_decode import bits_from_key
    optimal = sum(1 for m in range(1 << n)
                  if verify_placement(exp.model, f.decode(bits_from_key(format(m, f"0{n}b"), n))).valid
                  and sum(bits_from_key(format(m, f"0{n}b"), n)[:f.num_assignment_qubits]) == 3)
    assert u["states"] == 1024 and u["exact_count_rate"] == optimal / 1024
    (q,) = cmp["quantum"]
    assert q["uniform_probability_of_optimal_count"] == u["exact_count_rate"]
    assert q["better_than_uniform"] == (q["exact_probability_of_optimal_count"] > u["exact_count_rate"])


class UniformNoiseBackend:
    """A broken backend that returns uniformly random bitstrings."""
    name = "noise"
    target = None

    def __init__(self):
        self.calls = 0

    def run(self, qc, shots):
        import random
        self.calls += 1
        rng = random.Random(3)
        counts = Counter(format(rng.randrange(8), "03b") for _ in range(shots))

        class Job:
            def result(self_inner):
                class R:
                    def get_counts(self_r):
                        return dict(counts)
                return R()
        return Job()


def test_review_f2_demo_makes_no_advantage_claim_on_noise(demo, capsys):
    main_mod, _, _ = demo
    noise = UniformNoiseBackend()
    main_mod.backend = noise
    try:
        result = main_mod.main(shots=256)
    finally:
        del main_mod.backend
    out = capsys.readouterr().out
    assert noise.calls == 1
    assert "beat" not in out.lower() and "Random-guess baseline: 12.5%" in out
    assert "not an advantage" in out and result["uniform_optimal_rate"] == 0.125


def test_review_f3_empty_placement_is_counted_separately():
    model = build_cluster_model(load_snapshot(fixture("scenario_b_greedy_trap")))
    from qplace.placement_problem import build_placement_problem
    from qplace.quantum_decode import decode_quantum_samples
    from qplace.quantum_formulation import build_quantum_formulation
    problem = build_placement_problem(model)
    d = decode_quantum_samples(model, problem, build_quantum_formulation(problem), {"000": 5, "110": 3}, 2)
    s = d.summary()
    assert s["feasible_shots"] == 8 and s["empty_placement_shots"] == 5


def test_review_f5_solver_result_comes_from_the_best_seed():
    exp = run_experiment("scenario_g_triple_capacity",
                         build_cluster_model(load_snapshot(fixture("scenario_g_triple_capacity"))),
                         qaoa_config=FAST, reps_list=(1,))
    (q,) = comparison_dict(exp)["quantum"]
    if q["best_verified_sample"] is not None:
        assert q["solver_result"]["assignments"] == q["best_verified_sample"]["assignment"]
