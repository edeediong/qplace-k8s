"""Developer script: regenerate M2 artifacts for every benchmark fixture (M2 scenarios + M1 fixtures).

Not a product CLI. Usage:  python scripts/generate_m2_artifacts.py [--check]
--check compares placement_problem.json and placement_results.json byte-for-byte,
and benchmark_results.json excluding its ``runtime_observations`` block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from qplace import build_cluster_model, load_snapshot
from qplace.placement_assess import assess_placement
from qplace.placement_serialize import benchmark_results_dict, placement_problem_dict, placement_results_dict
from qplace.serialize import dumps

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "fixtures" / "m2").glob("scenario_*.json")) + sorted(
    (ROOT / "fixtures" / "m1").glob("fixture_*.json"))
ARTIFACTS = ROOT / "artifacts" / "m2"


def _without_runtime(text: str) -> dict:
    data = json.loads(text)
    data.pop("runtime_observations", None)
    return data


def main(argv: list[str]) -> int:
    unknown = [a for a in argv if a != "--check"]
    if unknown:
        print(f"unknown argument(s): {' '.join(unknown)}\n{__doc__}", file=sys.stderr)
        return 2
    check = "--check" in argv
    mismatches = 0
    for fixture in FIXTURES:
        model = build_cluster_model(load_snapshot(fixture))
        report = assess_placement(model)
        rendered = {
            "placement_problem.json": dumps(placement_problem_dict(report.problem)),
            "placement_results.json": dumps(placement_results_dict(report)),
            "benchmark_results.json": dumps(benchmark_results_dict(fixture.stem, model)),
        }
        out_dir = ARTIFACTS / fixture.stem
        for name, text in rendered.items():
            path = out_dir / name
            rel = path.relative_to(ROOT)
            if not check:
                out_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                print(f"wrote    {rel}")
                continue
            if not path.exists():
                same = False
            elif name == "benchmark_results.json":
                same = _without_runtime(path.read_text(encoding="utf-8")) == _without_runtime(text)
            else:
                same = path.read_text(encoding="utf-8") == text
            print(f"{'ok      ' if same else 'MISMATCH'} {rel}")
            mismatches += not same
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
