"""Effective pod requests.

Expected values follow the documented Kubernetes rules:
- https://kubernetes.io/docs/concepts/workloads/pods/init-containers/#resource-sharing-within-containers
- https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/#resource-sharing-within-containers
- https://kubernetes.io/docs/concepts/scheduling-eviction/pod-overhead/
- https://kubernetes.io/docs/tasks/configure-pod-container/assign-pod-level-resources/
- https://kubernetes.io/docs/tasks/configure-pod-container/resize-container-resources/
"""

from qplace.diagnostics import Reason
from qplace.resources import AccountingStatus, calculate_pod_requests
from tests.conftest import GI, MI, container, make_pod

V134 = (1, 34)


def reqs(cpu=None, mem=None):
    out = {}
    if cpu is not None:
        out["cpu"] = cpu
    if mem is not None:
        out["memory"] = mem
    return out


def test_single_container():
    r = calculate_pod_requests(make_pod(containers=[container(requests=reqs("250m", "128Mi"))]), V134)
    assert (r.cpu_millicores, r.memory_bytes, r.status) == (250, 128 * MI, AccountingStatus.EXACT)


def test_multiple_containers_are_summed():
    pod = make_pod(containers=[container("a", reqs("250m", "128Mi")), container("b", reqs("1", "1Gi"))])
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (1250, 128 * MI + GI)


def test_missing_request_and_limit_is_zero():
    r = calculate_pod_requests(make_pod(containers=[container(requests={})]), V134)
    assert (r.cpu_millicores, r.memory_bytes, r.status) == (0, 0, AccountingStatus.EXACT)


def test_missing_request_defaults_to_limit_per_resource():
    # Defaulting copies the limit into a missing request, independently per resource.
    pod = make_pod(containers=[container(requests={"cpu": "100m"}, limits={"cpu": "500m", "memory": "256Mi"})])
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (100, 256 * MI)
    assert Reason.REQUEST_DEFAULTED_FROM_LIMIT in r.reasons


def test_ordinary_init_containers_use_max_not_sum():
    # Effective init request = max over init containers; pod request = max(sum(app), init max).
    pod = make_pod(
        containers=[container("app", reqs("200m", "100Mi"))],
        initContainers=[container("i1", reqs("1", "50Mi")), container("i2", reqs("500m", "300Mi"))],
    )
    r = calculate_pod_requests(pod, V134)
    assert r.cpu_millicores == 1000  # init peak 1000m > app 200m
    assert r.memory_bytes == 300 * MI  # init peak 300Mi > app 100Mi


def test_restartable_sidecars_overlap_with_later_init_and_app_containers():
    # Order: sidecar s1 (100m), init i1 (1 CPU), sidecar s2 (200m), app (300m).
    # i1 runs while s1 is running -> 1100m. Steady state: app + s1 + s2 = 600m. Result max = 1100m.
    pod = make_pod(
        containers=[container("app", reqs("300m", "100Mi"))],
        initContainers=[
            container("s1", reqs("100m", "50Mi"), restartPolicy="Always"),
            container("i1", reqs("1", "10Mi")),
            container("s2", reqs("200m", "70Mi"), restartPolicy="Always"),
        ],
    )
    r = calculate_pod_requests(pod, V134)
    assert r.cpu_millicores == 1100
    assert r.memory_bytes == (100 + 50 + 70) * MI  # steady state dominates: 220Mi > 60Mi
    assert r.breakdown["cpu"]["restartable_init_containers"] == 300


def test_init_container_before_any_sidecar_does_not_include_sidecars():
    pod = make_pod(
        containers=[container("app", reqs("100m", "10Mi"))],
        initContainers=[
            container("i1", reqs("1", "10Mi")),
            container("s1", reqs("500m", "10Mi"), restartPolicy="Always"),
        ],
    )
    # i1 alone = 1000m; steady = 100m + 500m = 600m.
    assert calculate_pod_requests(pod, V134).cpu_millicores == 1000


def test_pod_overhead_added_once():
    pod = make_pod(
        containers=[container("a", reqs("500m", "512Mi")), container("b", reqs("500m", "512Mi"))],
        overhead={"cpu": "250m", "memory": "120Mi"},
    )
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (1250, 1144 * MI)


def test_pod_level_requests_replace_container_aggregate():
    pod = make_pod(
        containers=[container("a", reqs("200m", "100Mi")), container("b", reqs("300m", "100Mi"))],
        resources={"requests": {"cpu": "1", "memory": "1Gi"}},
        overhead={"cpu": "100m"},
    )
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (1100, GI)  # pod-level + overhead
    assert r.status is AccountingStatus.EXACT


def test_pod_level_only_for_one_resource():
    pod = make_pod(containers=[container(requests=reqs("200m", "100Mi"))], resources={"requests": {"cpu": "2"}})
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (2000, 100 * MI)


def test_pod_level_requests_need_review_before_default_on_version():
    pod = make_pod(containers=[container(requests=reqs("200m"))], resources={"requests": {"cpu": "1"}})
    for version in ((1, 33), None):
        r = calculate_pod_requests(pod, version)
        assert r.status is AccountingStatus.REVIEW_REQUIRED
        assert Reason.POD_LEVEL_FEATURE_STATE_UNCERTAIN in r.reasons
        assert r.cpu_millicores == 1000


def test_resize_status_uses_max_of_spec_actual_and_allocated():
    # Upsize in progress: spec 1 CPU, kubelet still actuating 500m, allocated 1 CPU -> reserve 1 CPU.
    # Downsize in progress: spec 256Mi, actual 1Gi -> reserve 1Gi until actuated.
    pod = make_pod(
        containers=[container("app", reqs("1", "256Mi"))],
        status={
            "conditions": [{"type": "PodResizeInProgress", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "app",
                    "allocatedResources": {"cpu": "1", "memory": "256Mi"},
                    "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}},
                }
            ],
        },
    )
    r = calculate_pod_requests(pod, V134)
    assert (r.cpu_millicores, r.memory_bytes) == (1000, GI)
    assert Reason.RESIZE_STATUS_APPLIED in r.reasons


def test_infeasible_resize_ignores_spec():
    pod = make_pod(
        containers=[container("app", reqs("8", "256Mi"))],
        status={
            "conditions": [{"type": "PodResizePending", "status": "True", "reason": "Infeasible"}],
            "containerStatuses": [
                {"name": "app", "allocatedResources": {"cpu": "1", "memory": "256Mi"},
                 "resources": {"requests": {"cpu": "1", "memory": "256Mi"}}}
            ],
        },
    )
    assert calculate_pod_requests(pod, V134).cpu_millicores == 1000


def test_resize_without_status_resources_is_unknown_not_zero():
    pod = make_pod(
        containers=[container("app", reqs("1", "256Mi"))],
        status={"conditions": [{"type": "PodResizePending", "status": "True", "reason": "Deferred"}]},
    )
    r = calculate_pod_requests(pod, V134)
    assert r.cpu_millicores is None and r.memory_bytes is None
    assert r.status is AccountingStatus.UNKNOWN
    assert Reason.RESIZE_STATUS_INCOMPLETE in r.reasons


def test_invalid_quantity_makes_only_that_resource_unknown():
    pod = make_pod(containers=[container(requests={"cpu": "lots", "memory": "64Mi"})])
    r = calculate_pod_requests(pod, V134)
    assert r.cpu_millicores is None
    assert r.memory_bytes == 64 * MI
    assert r.status is AccountingStatus.UNKNOWN
    assert any(d.code is Reason.INVALID_QUANTITY for d in r.diagnostics)
