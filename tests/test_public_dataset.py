"""Public-data instance derived from the Google cluster trace (clusterdata-2011-2, CC-BY 4.0)."""

import json
import re
import subprocess
import sys

from qplace import build_cluster_model, load_snapshot
from qplace.placement_assess import Assessment, assess_placement
from qplace.quantum_formulation import build_quantum_formulation, validate_formulation
from tests.conftest import ROOT

FIXTURE = ROOT / "fixtures" / "m5" / "google_trace_drain.json"
A, B, C = ("google-trace/job-3244832720-task-217", "google-trace/job-3996125377-task-22",
           "google-trace/job-4476806578-task-24")
X, Y = "m-369002391", "m-5774811"


def test_every_object_is_traceable_to_a_trace_row():
    data = json.loads(FIXTURE.read_text())
    assert data["snapshot"]["source_type"] == "public_trace_derived"
    assert "CC-BY 4.0" in data["snapshot"]["description"]
    for obj in data["nodes"] + data["pods"]:
        source = obj["metadata"]["annotations"]["qplace.dev/source"]
        assert re.match(r"(machine|task)_events part-\d{5}-of-\d{5} line \d+: ", source), source


def test_public_instance_is_a_greedy_trap_with_a_unique_complete_plan():
    model = build_cluster_model(load_snapshot(FIXTURE))
    r = assess_placement(model)
    assert model.resources.nodes[X].remaining_cpu_millicores == 22623  # 0.5 - five tasks, x100000, exact
    assert model.resources.nodes[Y].remaining_memory_bytes == 8143
    assert len(r.greedy.result.assignments) == 2  # first-fit decreasing strands task C
    assert r.exact.result.assignment_map() == {A: Y, B: X, C: X} and r.exact.result.optimality_proven
    # Only 3 of the trace's machines are modelled, so the honest assessment stays conditional.
    assert r.assessment is Assessment.REVIEW_REQUIRED
    assert {str(c.code) for c in r.problem.conditions} == {"CAPACITY_ASSESSMENT_INCOMPLETE"}


def test_public_instance_qubo_is_exhaustively_valid():
    model = build_cluster_model(load_snapshot(FIXTURE))
    form = build_quantum_formulation(assess_placement(model).problem)
    ev = validate_formulation(form)
    assert form.required_qubits == 5 and ev.valid and ev.max_feasible_pods == 3 and ev.minimum_states == 1


def test_derivation_script_rejects_bad_arguments():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "derive_google_trace_fixture.py")],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "clusterdata-2011-2" in proc.stderr
