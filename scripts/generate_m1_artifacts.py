"""Developer script: regenerate the M1 reference artifacts from the reference fixtures.

Not a product CLI. Usage:  python scripts/generate_m1_artifacts.py [--check]
With --check, compares freshly generated artifacts with the committed ones and
exits non-zero on any difference.
"""

from __future__ import annotations

import sys
from pathlib import Path

from qplace import build_cluster_model, load_snapshot
from qplace.serialize import cluster_model_dict, dumps, resource_inventory_dict

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "m1"
ARTIFACTS = ROOT / "artifacts" / "m1"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    mismatches = 0
    for fixture in sorted(FIXTURES.glob("fixture_*.json")):
        model = build_cluster_model(load_snapshot(fixture))
        out_dir = ARTIFACTS / fixture.stem
        rendered = {
            "cluster_model.json": dumps(cluster_model_dict(model)),
            "resource_inventory.json": dumps(resource_inventory_dict(model)),
        }
        for name, text in rendered.items():
            path = out_dir / name
            if check:
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    print(f"MISMATCH {path.relative_to(ROOT)}")
                    mismatches += 1
                else:
                    print(f"ok       {path.relative_to(ROOT)}")
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                print(f"wrote    {path.relative_to(ROOT)}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
