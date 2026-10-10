"""Exact branch-and-bound solver, checked against an independent exhaustive oracle."""

import random
from collections import defaultdict
from fractions import Fraction
from itertools import product

from qplace.drain import Replacement
from qplace.placement_assess import Outcome, assess_placement, evaluate
from qplace.placement_exact import ExactConfig, solve_exact
from qplace.placement_greedy import solve_greedy
from qplace.placement_problem import (
    CandidateSpec, Code, DestinationSpec, PlacementProblem, build_placement_problem,
)
from qplace.placement_result import Termination
from qplace.resources import AccountingStatus
from tests.conftest import build_model, cand, raw_node

EXACT = AccountingStatus.EXACT


def dest(name, cpu, mem, slots, alloc_cpu=None, alloc_mem=None, alloc_pods=None):
    return DestinationSpec(name, cpu, mem, slots, alloc_cpu or max(cpu, 1), alloc_mem or max(mem, 1),
                           alloc_pods or max(slots, 1), EXACT, ())


def candidate(pod, cpu, mem, allowed):
    return CandidateSpec(pod, cpu, mem, EXACT, Replacement.ASSUMED, tuple(sorted(allowed)), (), (),
                         () if allowed else (Code.NO_ALLOWED_DESTINATION,))


def problem(cands, dests):
    return PlacementProblem("src", {}, tuple(sorted(cands, key=lambda c: c.pod)),
                            tuple(sorted(dests, key=lambda d: d.node)), (), ())


def oracle(p):
    """Independent exhaustive enumeration: best (count, leximin balance, tie-break) by brute force."""
    usable = [c for c in p.candidates if c.allowed_nodes]
    dests = {d.node: d for d in p.destinations}
    best = None
    for choice in product(*[[None, *c.allowed_nodes] for c in usable]):
        load = defaultdict(lambda: [0, 0, 0])
        for c, n in zip(usable, choice):
            if n is not None:
                load[n][0] += c.cpu_millicores
                load[n][1] += c.memory_bytes
                load[n][2] += 1
        if any(l[0] > dests[n].cpu_millicores or l[1] > dests[n].memory_bytes or l[2] > dests[n].pod_slots
               for n, l in load.items()):
            continue
        fractions = []
        for d in dests.values():
            l = load[d.node]
            fractions.append(min(Fraction(d.cpu_millicores - l[0], d.allocatable_cpu_millicores),
                                 Fraction(d.memory_bytes - l[1], d.allocatable_memory_bytes),
                                 Fraction(d.pod_slots - l[2], d.allocatable_pods)))
        chosen = dict(zip((c.pod for c in usable), choice))
        tie = tuple((0, chosen[c.pod]) if chosen.get(c.pod) else (1, "") for c in p.candidates)
        key = (sum(n is not None for n in choice), tuple(sorted(fractions)))
        if best is None or key > best[0] or (key == best[0] and tie < best[1]):
            best = (key, tie, {k: v for k, v in chosen.items() if v})
    return best


def random_problem(rng):
    nodes = [dest(f"n{i}", rng.choice([0, 500, 1000, 2000, 3000]), rng.choice([0, 1, 2, 4]) * 1024,
                  rng.randint(0, 3), alloc_cpu=4000, alloc_mem=4096, alloc_pods=4) for i in range(rng.randint(1, 3))]
    cands = [candidate(f"ns/p{i}", rng.choice([0, 250, 500, 1000, 1500]), rng.choice([0, 256, 512, 1024]),
                       [d.node for d in nodes if rng.random() < 0.7]) for i in range(rng.randint(0, 6))]
    return problem(cands, nodes)


def test_exact_matches_oracle_on_random_problems():
    rng = random.Random(20261010)
    greedy_worse = 0
    for _ in range(300):
        p = random_problem(rng)
        r = solve_exact(p)
        (count, balance), tie, assignment = oracle(p)
        assert r.optimality_proven and r.termination is Termination.SEARCH_COMPLETED
        assert r.objective.assigned == count  # optimal cardinality
        assert r.objective.balance == balance  # optimal secondary objective
        assert r.assignment_map() == assignment  # identical tie-break
        g = solve_greedy(p)
        assert len(g.assignments) <= count  # exact never places fewer pods than greedy
        greedy_worse += len(g.assignments) < count
    assert greedy_worse > 0  # the random set includes adversarial cases for greedy


def test_primary_objective_never_sacrificed_for_balance():
    p = problem([candidate(f"ns/{x}", 2000, 0, ["big", "small"]) for x in "abc"],
                [dest("big", 4000, 1, 9), dest("small", 2000, 1, 9)])
    r = solve_exact(p)
    assert r.objective.assigned == 3  # all three, although leaving one out would balance better


def test_balance_prefers_spreading_and_tiebreak_is_lexicographic():
    two = problem([candidate("ns/a", 1000, 1, ["n1", "n2"]), candidate("ns/b", 1000, 1, ["n1", "n2"])],
                  [dest("n1", 2000, 4, 4), dest("n2", 2000, 4, 4)])
    assert solve_exact(two).assignment_map() == {"ns/a": "n1", "ns/b": "n2"}
    one = problem([candidate("ns/a", 1000, 1, ["n1", "n2"])], [dest("n2", 2000, 4, 4), dest("n1", 2000, 4, 4)])
    assert solve_exact(one).assignment_map() == {"ns/a": "n1"}


def test_finds_complete_assignment_greedy_misses():
    nodes = [raw_node("src", labels={"disk": "ssd"}), raw_node("node-a", cpu="2500m", labels={"disk": "ssd"}),
             raw_node("node-b", cpu="2500m", labels={"disk": "hdd"})]
    p = build_placement_problem(build_model(nodes, [cand("big", "2"), cand("ssd", "1500m",
                                                                         nodeSelector={"disk": "ssd"})]))
    assert solve_exact(p).assignment_map() == {"ns/big": "node-b", "ns/ssd": "node-a"}
    assert len(solve_greedy(p).assignments) == 1


def test_aggregate_shortage_best_partial_is_proven():
    p = problem([candidate(f"ns/{x}", 1500, 1, ["n1", "n2"]) for x in "abc"],
                [dest("n1", 2000, 8, 9), dest("n2", 2000, 8, 9)])
    r = solve_exact(p)
    assert r.objective.assigned == 2 and r.optimality_proven
    assert [u.pod for u in r.unplaced] == ["ns/c"]


def test_state_limit_gives_indeterminate_without_optimality():
    m = build_model([raw_node("src", cpu="16"), raw_node("n1"), raw_node("n2")],
                    [cand(x, "500m", "128Mi") for x in "abcd"])
    p = build_placement_problem(m)
    r = solve_exact(p, ExactConfig(max_states=3))
    assert r.termination is Termination.STATE_LIMIT_REACHED and not r.optimality_proven
    assert evaluate(m, p, r).outcome is Outcome.INDETERMINATE
    # Review F2: greedy's verified complete assignment still proves feasibility; optimality stays unclaimed.
    report = assess_placement(m, ExactConfig(max_states=3))
    assert str(report.assessment) == "MODELED_FEASIBLE" and report.greedy_gap is None
    assert report.supporting == "greedy" and not report.exact.result.optimality_proven
    assert {"EXACT_SEARCH_INCONCLUSIVE", "COMPLETE_ASSIGNMENT_FOUND_BY_GREEDY"} <= set(map(str, report.findings))


def test_interrupted_search_without_complete_greedy_is_review_required():
    m = build_model([raw_node("src", cpu="16"), raw_node("n1", cpu="1")], [cand(x, "600m", "128Mi") for x in "abc"])
    report = assess_placement(m, ExactConfig(max_states=2))
    assert str(report.assessment) == "REVIEW_REQUIRED"  # never MODELED_BLOCKED without a completed proof
    assert report.supporting is None and report.verified_assignments == ()


def test_size_limit_and_deadline_are_inconclusive():
    p = problem([candidate(f"ns/{x}", 1, 1, ["n1"]) for x in "ab"], [dest("n1", 10, 10, 10)])
    r = solve_exact(p, ExactConfig(max_candidates=1))
    assert r.termination is Termination.SIZE_LIMIT_EXCEEDED and r.assignments == () and not r.optimality_proven
    r = solve_exact(p, ExactConfig(max_destinations=0))
    assert r.termination is Termination.SIZE_LIMIT_EXCEEDED
    r = solve_exact(p, ExactConfig(deadline_seconds=0.0))
    assert r.termination is Termination.DEADLINE_EXCEEDED and not r.optimality_proven


def test_trivial_problems():
    assert solve_exact(problem([], [dest("n1", 1, 1, 1)])).optimality_proven
    single = solve_exact(problem([candidate("ns/a", 1, 1, ["n1"])], [dest("n1", 1, 1, 1)]))
    assert single.assignment_map() == {"ns/a": "n1"} and single.optimality_proven


def test_repeatable_under_fixed_configuration():
    rng = random.Random(5)
    for _ in range(20):
        p = random_problem(rng)
        a, b = solve_exact(p), solve_exact(p)
        assert (a.assignments, a.unplaced, a.objective, a.stats.states_explored) == (
            b.assignments, b.unplaced, b.objective, b.stats.states_explored)
