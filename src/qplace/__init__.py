"""QPlace M1: read-only Kubernetes cluster-state modeling for pre-drain feasibility.

This package only reads saved snapshots. It contains no Kubernetes client and
no code path that cordons, evicts, deletes or otherwise mutates a cluster.
"""

__version__ = "0.1.0"

from qplace.diagnostics import (  # noqa: E402
    Diagnostic,
    QPlaceError,
    QuantityError,
    Reason,
    Severity,
    SnapshotValidationError,
)
from qplace.drain import DrainClass, DrainInventory, Replacement, classify_drain_pods  # noqa: E402
from qplace.eligibility import (  # noqa: E402
    CapacityFit,
    Eligibility,
    evaluate_capacity_fit,
    evaluate_node_eligibility,
)
from qplace.model import ClusterModel, build_cluster_model  # noqa: E402
from qplace.nodes import NodeModel, normalize_node  # noqa: E402
from qplace.pods import PodModel, normalize_pod  # noqa: E402
from qplace.quantity import parse_cpu_millicores, parse_memory_bytes, parse_quantity  # noqa: E402
from qplace.resources import (  # noqa: E402
    AccountingStatus,
    NodeAccount,
    PodRequests,
    ResourceInventory,
    build_resource_inventory,
    calculate_pod_requests,
)
from qplace.serialize import cluster_model_dict, resource_inventory_dict, write_artifacts  # noqa: E402
from qplace.snapshot import Snapshot, load_snapshot, load_snapshot_data  # noqa: E402

__all__ = [
    "AccountingStatus",
    "CapacityFit",
    "ClusterModel",
    "Diagnostic",
    "DrainClass",
    "DrainInventory",
    "Eligibility",
    "NodeAccount",
    "NodeModel",
    "PodModel",
    "PodRequests",
    "QPlaceError",
    "QuantityError",
    "Reason",
    "Replacement",
    "ResourceInventory",
    "Severity",
    "Snapshot",
    "SnapshotValidationError",
    "build_cluster_model",
    "build_resource_inventory",
    "calculate_pod_requests",
    "classify_drain_pods",
    "cluster_model_dict",
    "evaluate_capacity_fit",
    "evaluate_node_eligibility",
    "load_snapshot",
    "load_snapshot_data",
    "normalize_node",
    "normalize_pod",
    "parse_cpu_millicores",
    "parse_memory_bytes",
    "parse_quantity",
    "resource_inventory_dict",
    "write_artifacts",
]
