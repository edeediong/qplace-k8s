# M5 submission readiness: QPlace (Qollab × IonQ Hackathon 2026)

**STATUS: READY, NOT SUBMITTED.** The code, evidence, documentation, video files and upload bundle are all done. Four steps need the project owner's accounts, and an agent cannot perform them:

1. **Upload `qplace-qollab.zip` to the Qollab project and run it once on the built-in simulator.** The console wording changed after the hosted evidence was recorded.
2. **Upload the demo video** (YouTube unlisted, Loom, Vimeo or Drive) with public link access, and check it opens in a private window.
3. **Publish the Qollab project.** Fill in the description, theme (Quantum Impact), MIT license and video link.
4. **Submit from the registration page as team captain**, before **2026-10-11 19:00 ET (20:00 ADT)**.

## A. Executive status

| Item | State |
|---|---|
| Deadline | 2026-10-11 19:00 ET. About 26 hours left when this report was written (2026-10-10 ~17:30 ADT) |
| Code and tests | done, pushed (§C) |
| M3 | **PASS** (hosted evidence recorded) |
| Project page | written (`qollab/README.md`). Must be published on Qollab by the owner |
| Video | rendered locally (§E). Upload pending, by the owner |
| Submission | **not submitted.** Owner action required |

## B. Milestone 3 closure

- **Hosted runs on 2026-10-10, performed by the owner:**
  - Built-in simulator: 84/256 shots placed 2/2.
  - IonQ remote simulator with the Aria 1 noise model: 83/256.
  - Random guessing: 12.5%.
- Details and consistency checks: `docs/m3/hosted-evidence.md`. M3 status was changed to PASS in `reports/m3/m3_summary.md`.
- The IonQ "29 qubits" report is explained and reproduced: the IonQ simulator target is 29 qubits wide, transpilation pads the circuit, and 3 qubits are active. The console now prints logical, active and padded widths separately.
- **Remaining technical limits:** small instances, noiseless local training, one hosted run per backend.

## C. Code and validation

| Command | Result (2026-10-10, Python 3.14.2) |
|---|---|
| `.venv/bin/python -m pytest -q` | **351 passed**, 0 failed, 0 skipped |
| `scripts/generate_m1_artifacts.py --check` | 6/6 ok |
| `scripts/generate_m2_artifacts.py --check` | 27/27 ok |
| `scripts/generate_m3_artifacts.py check` | 67/67 ok (11 instances × 6 = 66 files + `qollab/placement_data.py`) |
| `cd qollab && python main.py` | local BasicSimulator run: QAOA sampled the 2/2 placement, random baseline printed |

**Commits this phase:**

| Commit | Content |
|---|---|
| `f333b2b` | M3 hosted evidence and 29-qubit wording |
| `4a9ec68` | public-dataset experiment |
| `f88e202` | project page, README, provenance, video script |

The final SHA is in §F.

## D. Documentation

| Item | Location | Status |
|---|---|---|
| Project page (what, problem, how to run, replicate) | `qollab/README.md`, shipped in the zip | done |
| Short description (227 chars) | below | done |
| Repository README | `README.md` (headline results, attribution) | done |
| Originality / AI disclosure | `docs/m5/provenance.md`, also summarized on the project page | done |
| Public dataset | `docs/m5/public-dataset.md` | done |
| License | MIT, `LICENSE` | done |
| Attribution "This effort is supported by Qollab & IonQ" | `qollab/README.md`, `README.md`, video end card | done |

Short description for Qollab:

> Before draining a Kubernetes node, can its pods fit elsewhere? QPlace solves the placement exactly, encodes it as a verified QUBO and runs QAOA on Qollab: it samples the plan greedy misses. Honest baselines, no advantage claim.

## E. Video

| Item | Value |
|---|---|
| File | `~/Documents/qplace-video/qplace-demo.mp4` (burned-in captions); also `qplace-demo-nocaptions.mp4` and `qplace-demo.srt` (copy in `docs/m5/video/captions.srt`) |
| Duration | **2:27.97** (ffprobe 147.97 s), within 2:00–3:00 |
| Format | 1920×1080, H.264 + AAC, 30 fps |
| Content | follows `docs/m5/video/script.md` word for word. Built from real data only: the circuit is drawn from the shipped demo circuit, the charts use the hosted-evidence numbers exactly, and the terminal excerpt is a real local run labeled "BasicSimulator fallback" |
| Accessibility | high-contrast slides, ≥ 40 px text (except gate labels inside the Qiskit circuit drawing), captions on a solid band, ✓/✗ labels and hatching, so no meaning depends on colour alone |
| Narration | **synthetic macOS voice (Samantha)**, disclosed on the end card. The owner may record their own voice-over instead |
| Not checked | how the synthetic voice pronounces "QUBO", "node-a" and "qplace-k8s" (nobody listened to the audio) |
| Public URL | **pending (owner upload)**. Must open without sign-in |
| Does not show | the Qollab web UI or live runs. Optionally, the owner can record a short screen capture of pressing Run on Qollab and splice it into segment 5 |

## F. Submission

| Item | State |
|---|---|
| Qollab project URL | **pending (owner)** |
| Published | **pending (owner)** |
| Track / node | Student track, St. Francis Xavier University. **To confirm by the owner** on the registration page |
| Theme | Quantum Impact (recommended) |
| Final repository SHA | see the final commit of this report |

## G. Independent review

The reviewer was a separate agent that wrote none of this code. It re-fetched the rules, re-ran the tests, the checks and the local demo, recomputed every hosted percentage, reproduced the 29-qubit width with qiskit-ionq 1.1.1, and checked every numeric claim against the artifacts.

| Gate | Result |
|---|---|
| No secrets or kubeconfig | **PASS after fix**: no keys, tokens or kubeconfig anywhere. A work GitHub account *name* appeared in `reports/m3/m3_summary.md` and was removed. It remains in the history of commit `3b9afce`, which cannot be rewritten without a force-push. It is an account name, not a credential. The author's local username appears in the history of `14d5995` (removed in `7cc9a8b`) |
| MIT license (GitHub reports `MIT`) | PASS |
| Upload zip = `qollab/` files, compiles, no `__future__` | PASS |
| Local demo | PASS |
| 351 tests; 6 / 27 / 67 artifact checks | PASS |
| Hosted-evidence arithmetic and 29-qubit explanation | PASS |
| Numeric claims match artifacts | **PASS after fix** (instance counts and ratio ranges corrected in the M3 report) |
| No advantage or hardware claims; synthetic data disclosed | **PASS after fix** ("all fixtures are synthetic" corrected for the public-trace instance) |
| Attribution text, provenance, commit timeline inside the window | PASS |
| Public-dataset traceability and CC-BY credit | PASS (author credit and licence link added to the fixture) |
| Console accessibility (no colour-only information) | PASS |
| Qollab page published and HEAD runs on Qollab; video public | **BLOCKED (owner actions)** |

**Open risks:**

1. In the IonQ distribution, bitstring `001` (84 shots) equals the built-in 2/2 count. The owner should confirm it against the raw console output.
2. The hosted runs used commit `a82dc48`. The current `main.py` only changes console wording, but it needs one confirming hosted run.
3. The whole codebase was built in about 3.5 hours with AI agents. This is disclosed in `docs/m5/provenance.md` and on the project page.

## Rubric self-assessment (evidence-based; not a prediction of scores)

| Criterion | Strengths (evidence) | Weaknesses | Highest-value improvement |
|---|---|---|---|
| Novelty & creativity | Kubernetes pre-drain planning is an unusual QAOA application. A greedy trap shows why joint placement matters, and it occurs in a public Google trace too. There is an independent verifier for every quantum sample | small instances; the QAOA itself is the standard construction | already addressed: the public-trace greedy trap |
| Technical execution | exact QUBO encodings with an exhaustive proof; Ising cross-checked against qiskit-optimization; 351 tests; exact and greedy baselines; random baseline; hosted runs on both backends with one job each | p = 1 is worse than random on 2 instances; noiseless training; one hosted run per backend | confirming hosted re-run of HEAD, with raw logs |
| Impact potential | a real SRE decision, a read-only advisor, explicit uncertainty (`REVIEW_REQUIRED`) | no live-cluster connector; tiny quantum size; no advantage | roadmap (§H) states the path honestly |
| Documentation quality | project page answers what, why, how to run and how to replicate; M1–M3 reports; docs per milestone; provenance; video with captions | a lot of material; judges may need the short path | the project page leads with the result table and "Press Run" |
| Theme fit (Quantum Impact) | public dataset (Google trace, CC-BY), strong classical baseline (exact B&B), quantum subroutine compared against it | "measurable gain" is only against random guessing, not against the classical baseline | disclosed; do not overclaim |

## H. Post-hackathon roadmap

| Area | Next step |
|---|---|
| M4 CLI | `qplace check-drain --snapshot file.json`: offline, read-only, classical assessment plus an optional QAOA comparison |
| Live cluster | read-only export (`kubectl get nodes,pods,pdb -o json`) into the snapshot schema. Never mutate |
| Constraints | evaluate required node affinity, host-port conflicts, ephemeral storage and extended resources instead of returning `UNKNOWN` |
| Benchmarks | more public-trace instances (Google 2011/2019, Azure), larger exact baselines, MIP solvers |
| Quantum research | better QUBO scaling (penalty tuning, slack-free encodings), warm-start / CVaR QAOA, noise studies on IonQ simulators and hardware, comparison with quantum annealing |
