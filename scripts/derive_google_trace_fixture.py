"""Derive fixtures/m5/google_trace_drain.json from the public Google cluster trace (clusterdata-2011-2).

Source (CC-BY 4.0): https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md
Files (plain HTTPS, checked against the published SHA256SUM):
  https://storage.googleapis.com/clusterdata-2011-2/machine_events/part-00000-of-00001.csv.gz
  https://storage.googleapis.com/clusterdata-2011-2/task_events/part-00000-of-00500.csv.gz

Usage:  python scripts/derive_google_trace_fixture.py MACHINE_EVENTS.csv.gz TASK_EVENTS.csv.gz [OUT.json]

Mapping (documented in docs/m5/public-dataset.md):
- Borg machine -> Kubernetes node; Borg task -> pod owned by a ReplicaSet named after its job.
- Snapshot = trace time 0: machine ADD events and task SCHEDULE events (event type 1) at time 0.
- Trace values are normalized (largest machine = 1.0); there is no published absolute scale.
  Integer "trace units" = value x 100000, exact Decimal arithmetic. Capacities round DOWN and
  requests round UP, so the integer instance is never looser than the trace. Units are written
  as CPU millicores and memory bytes only because the snapshot schema needs Kubernetes quantities;
  placement feasibility is invariant to the per-resource scale, so no absolute size is implied.
- Pod slots per node are not in the trace: 110 (Kubernetes default) is a stated assumption.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

DRAIN = "1335771"
DESTINATIONS = ("369002391", "5774811")
SCALE = Decimal(100000)
SHA256 = {
    "machine_events": "fc44b8d2b33a96a261382488789855be7a0ce338888c99930ff45cb592249664",
    "task_events": "eae977d521bc6ed5d8deef56f1e06009d41844d4eb9a7e308e610d68e1b10f18",
}


def units(value: str, rounding: str) -> int:
    return int((Decimal(value) * SCALE).to_integral_value(rounding=rounding))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    machine_path, task_path = Path(argv[0]), Path(argv[1])
    out = Path(argv[2]) if len(argv) == 3 else Path(__file__).resolve().parent.parent / "fixtures/m5/google_trace_drain.json"
    for key, path in (("machine_events", machine_path), ("task_events", task_path)):
        if sha256(path) != SHA256[key]:
            print(f"checksum mismatch for {path}", file=sys.stderr)
            return 1

    wanted = {DRAIN, *DESTINATIONS}
    machines: dict[str, tuple[int, str, str]] = {}
    with gzip.open(machine_path, "rt", newline="") as fh:
        for line_no, row in enumerate(csv.reader(fh), start=1):
            if row[0] == "0" and row[2] == "0" and row[1] in wanted:  # ADD at time 0
                machines[row[1]] = (line_no, row[4], row[5])
    assert set(machines) == wanted, machines

    tasks: list[tuple[int, list[str]]] = []
    jobs_on_machine: dict[str, set[str]] = {m: set() for m in wanted}
    with gzip.open(task_path, "rt", newline="") as fh:
        for line_no, row in enumerate(csv.reader(fh), start=1):
            if row[0] == "0" and row[5] == "1" and row[4] in wanted:  # SCHEDULE at time 0
                tasks.append((line_no, row))
                jobs_on_machine[row[4]].add(row[2])

    nodes = []
    for mid in (DRAIN, *DESTINATIONS):
        line_no, cpu, mem = machines[mid]
        cap_cpu, cap_mem = units(cpu, ROUND_FLOOR), units(mem, ROUND_FLOOR)
        nodes.append({
            "apiVersion": "v1", "kind": "Node",
            "metadata": {
                "name": f"m-{mid}", "uid": f"google-2011-machine-{mid}",
                "labels": {"kubernetes.io/hostname": f"m-{mid}"},
                "annotations": {"qplace.dev/source": f"machine_events part-00000-of-00001 line {line_no}: "
                                                      f"CPUs={cpu} Memory={mem}"},
            },
            "spec": {},
            "status": {
                "capacity": {"cpu": f"{cap_cpu}m", "memory": str(cap_mem), "pods": "110"},
                "allocatable": {"cpu": f"{cap_cpu}m", "memory": str(cap_mem), "pods": "110"},
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        })

    pods = []
    for line_no, row in tasks:
        job, index, machine, cpu, mem, diff = row[2], row[3], row[4], row[9], row[10], row[12]
        if diff == "1" and machine == DRAIN:
            # Different-machines restriction: no other task of the same job may share a machine.
            clash = [m for m in DESTINATIONS if job in jobs_on_machine[m]]
            assert not clash, f"job {job} already on {clash}; the restriction would need modelling"
        pods.append({
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {
                "namespace": "google-trace", "name": f"job-{job}-task-{index}",
                "uid": f"google-2011-task-{job}-{index}",
                "labels": {"trace-job": job},
                "annotations": {"qplace.dev/source": f"task_events part-00000-of-00500 line {line_no}: "
                                                      f"CPU={cpu} memory={mem} different_machines={diff or 'unset'}"},
                "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": f"job-{job}",
                                     "controller": True}],
            },
            "spec": {
                "nodeName": f"m-{machine}",
                "containers": [{"name": "task", "resources": {"requests": {
                    "cpu": f"{units(cpu, ROUND_CEILING)}m", "memory": str(units(mem, ROUND_CEILING))}}}],
            },
            "status": {"phase": "Running"},
        })
    pods.sort(key=lambda p: (p["spec"]["nodeName"], p["metadata"]["name"]))

    snapshot = {
        "schema_version": "1.0",
        "snapshot": {
            "id": "m5-google-trace-drain",
            "captured_at": "2011-05-01T00:00:00Z",
            "source_type": "public_trace_derived",
            "kubernetes_version": None,
            "description": "Derived from the public Google cluster trace clusterdata-2011-2 (CC-BY 4.0) at trace time 0: "
                           "drain machine 1335771; destinations 369002391 and 5774811. Normalized trace values x 100000 "
                           "as integer units (capacities floored, requests ceiled). Not a Kubernetes cluster.",
            "inventory": {
                "nodes": "partial", "pods": "complete", "pod_disruption_budgets": "not_collected",
                "notes": "Pods: every task SCHEDULE event at time 0 on the three selected machines in task_events "
                         "part-00000-of-00500 (the first shard). Pod slots 110 per node are an assumption. "
                         "captured_at is nominal: the trace publishes relative times only and covers May 2011.",
            },
        },
        "target_node": f"m-{DRAIN}",
        "nodes": nodes,
        "pods": pods,
        "pod_disruption_budgets": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(nodes)} nodes, {len(pods)} pods)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
