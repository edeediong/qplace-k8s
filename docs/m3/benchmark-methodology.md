# M3 benchmark methodology

**In short:** 10 synthetic instances (6 M2 scenarios, 1 new M3 stress scenario, 3 M1 fixtures). Each runs greedy, exact and QAOA at p = 1 and p = 2, with 5 sampling seeds × 1024 shots, all on a **local noiseless simulator**. Every number comes from `artifacts/m3/*/comparison_results.json`. Nothing here is quantum-hardware performance, and nothing here shows a quantum advantage.

## Design

| Experiment (spec) | Fixture | What it tests |
|---|---|---|
| A: small feasible | `scenario_a_feasible`, `fixture_a_healthy` | is the complete placement sampled? |
| **B: greedy trap** | `scenario_b_greedy_trap` | greedy 1/2, exact 2/2. How often does QAOA sample the 2/2 placement? |
| C: capacity shortage | `scenario_c_aggregate_shortage`, `scenario_f_pod_slots` | is the best partial placement (proven by M2) sampled? |
| D: eligibility | `scenario_d_eligibility` | forbidden pairs have no qubit. The decoder can't produce them |
| E: constraint stress | `scenario_g_triple_capacity` (new) | pairs fit, triples don't. The slack encoding is needed, and the verifier rejects triples |
| F: unknown inputs | `scenario_e_uncertain`, `fixture_b_insufficient`, `fixture_c_constraints` | M2's `REVIEW_REQUIRED` and conditions pass through unchanged |

## Metrics (all in `comparison_results.json`)

| Metric | Definition |
|---|---|
| pods placed | best **verified** sample (best of 5 seeds), next to greedy and exact |
| optimality gap | exact placed − QAOA best placed |
| M2 objective equal | `best_sample.objective == exact.objective`, comparing the full lexicographic M2 objective |
| feasible sample rate | verified shots / shots, averaged over seeds |
| exact-count sample rate | verified shots placing exactly as many pods as the exact solver |
| exact probability of optimal count | from the exact output distribution (no shot noise). Optimality is judged by `verify_placement` |
| **uniform baseline** | `uniform_random_baseline` gives the fraction of all `2^n` bitstrings that are verified-feasible, or that reach the exact count. That is what random guessing scores. `ratio_to_uniform` and `better_than_uniform` compare QAOA with it. Every QAOA rate should be read against this null model |
| seeds reaching exact count | how many of the 5 seeds sampled at least one optimal-count placement. Weak evidence: uniform noise would also reach it with near-certainty at 1024 shots (`probability_seed_hits_exact_count_in_shots`) |
| circuit cost | qubits, depth, two-qubit gates (RZZ) |
| classical effort | estimator evaluations during training |
| quantum jobs | sampler jobs (one per seed) |
| runtimes | training wall time; per-run sampling time; greedy and exact solve times (median of 5) |

## Fairness

- Greedy and exact times cover the solver call only. QAOA times are reported as **training** (wall time over all estimator evaluations) and **sampling** (one sampler job), separately. A fair end-to-end comparison would add up the whole QAOA workflow. Even the solver-only classical times are 10³–10⁴× faster here.
- All instances are tiny (1–10 qubits). On any of them a classical method can enumerate everything. They test **correctness and behavior**, not scaling.
- Some instances are trivial for QAOA:
  - `fixture_b_insufficient` has 1 qubit.
  - `scenario_d_eligibility` has no coupling terms, so the optimum is a product state.

  A 100% success rate on those says little.

## Results against random guessing

| Fixture | Uniform P(opt) | QAOA p=1 P(opt) (× uniform) | QAOA p=2 P(opt) (× uniform) |
|---|---|---|---|
| scenario_b_greedy_trap | 0.125 | 0.154 (1.23×) | 0.333 (2.67×) |
| scenario_c_aggregate_shortage | 0.094 | 0.135 (1.44×) | 0.578 (6.17×) |
| scenario_g_triple_capacity | 0.047 | **0.015 (0.32×, worse than random)** | 0.106 (2.26×) |
| fixture_c_constraints | 0.250 | 0.254 (1.02×) | 0.696 (2.78×) |
| fixture_a_healthy | 0.062 | 0.861 (13.8×) | 0.996 (15.9×) |
| scenario_a / d / e / f, fixture_b | 0.125–0.5 | 1.0 | 0.97–1.0 (trivial or near-trivial instances) |

Values are from the committed observational artifacts. They come from a re-run, so a new run may differ slightly.

## Statistics

- Training is deterministic, given the platform.
- Sampling depends on the seed. Five seeds × 1024 shots give a spread, reported per seed.
- The exact output probability is the seed-free quantity to compare across depths.
- No confidence intervals are claimed beyond the per-seed values.

## Reproduce

```bash
pip install -e '.[test,quantum]'
python scripts/generate_m3_artifacts.py generate   # re-runs everything; runtimes and floats will differ slightly
python scripts/generate_m3_artifacts.py check      # deterministic files byte-for-byte, observational files by schema
```
