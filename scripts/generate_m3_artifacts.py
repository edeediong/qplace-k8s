"""Developer script for M3 artifacts and the Qollab demo data.

Usage:
    python scripts/generate_m3_artifacts.py generate   # run all experiments, write artifacts and qollab/placement_data.py
    python scripts/generate_m3_artifacts.py check      # deterministic files byte-for-byte; observational files by schema

Not a product CLI. Unknown arguments are rejected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

from qplace import build_cluster_model, load_snapshot
from qplace.quantum_experiment import ARTIFACTS, quantum_problem_dict, run_experiment
from qplace.serialize import dumps
from qplace.snapshot import load_schema

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = (sorted((ROOT / "fixtures" / "m2").glob("scenario_*.json"))
            + sorted((ROOT / "fixtures" / "m3").glob("scenario_*.json"))
            + sorted((ROOT / "fixtures" / "m1").glob("fixture_*.json")))
OUT = ROOT / "artifacts" / "m3"
DEMO_FIXTURE = "scenario_b_greedy_trap"
DEMO_REPS = 2


def schema_for(name: str) -> dict:
    return load_schema("m3-" + name.replace(".json", "").replace("_", "-") + "-1.0.schema.json")


def generate() -> int:
    for path in FIXTURES:
        exp = run_experiment(path.stem, build_cluster_model(load_snapshot(path)))
        out = OUT / path.stem
        out.mkdir(parents=True, exist_ok=True)
        for name, (builder, _deterministic) in ARTIFACTS.items():
            doc = builder(exp)
            jsonschema.Draft202012Validator(schema_for(name)).validate(doc)
            (out / name).write_text(dumps(doc), encoding="utf-8")
        print(f"wrote    artifacts/m3/{path.stem}/ ({len(ARTIFACTS)} files)")
        if path.stem == DEMO_FIXTURE:
            from qplace.qollab_export import write_placement_data
            write_placement_data(exp, DEMO_REPS, ROOT / "qollab" / "placement_data.py")
            print("wrote    qollab/placement_data.py")
    return 0


def check() -> int:
    from qplace.quantum_experiment import ising_dict, qubo_dict
    from qplace.quantum_formulation import build_quantum_formulation, validate_formulation  # noqa: F401
    bad = 0
    for path in FIXTURES:
        out = OUT / path.stem
        model = build_cluster_model(load_snapshot(path))
        # Deterministic files: rebuild without running QAOA.
        exp = run_experiment(path.stem, model, reps_list=())
        for name, builder in (("quantum_problem.json", quantum_problem_dict), ("qubo.json", qubo_dict),
                              ("ising_hamiltonian.json", ising_dict)):
            ok = (out / name).exists() and (out / name).read_text(encoding="utf-8") == dumps(builder(exp))
            print(f"{'ok      ' if ok else 'MISMATCH'} artifacts/m3/{path.stem}/{name}")
            bad += not ok
        for name, (_b, deterministic) in ARTIFACTS.items():
            if deterministic:
                continue
            try:
                doc = json.loads((out / name).read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator(schema_for(name)).validate(doc)
                ok = doc["fixture"] == path.stem and doc["source"]["source_sha256"] == exp.problem.provenance["source_sha256"]
            except (OSError, ValueError, jsonschema.ValidationError):
                ok = False
            print(f"{'ok      ' if ok else 'MISMATCH'} artifacts/m3/{path.stem}/{name} (observational: schema)")
            bad += not ok
    from qplace.qollab_export import check_placement_data
    ok = check_placement_data(ROOT / "qollab" / "placement_data.py", OUT / DEMO_FIXTURE, DEMO_REPS)
    print(f"{'ok      ' if ok else 'MISMATCH'} qollab/placement_data.py")
    bad += not ok
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if argv == ["generate"]:
        return generate()
    if argv == ["check"]:
        return check()
    print(f"usage: generate | check (got {argv!r})\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
