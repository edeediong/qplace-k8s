# Provenance and originality statement

**In short:** QPlace was written from scratch during the Qollab × IonQ build window, with AI coding agents. The git history shows every step and has never been rewritten. No earlier codebase, template or tutorial code was used.

## Timeline (from `git log`, times in ADT = ET + 1h)

The build window opened 2026-10-09 19:00 ET (20:00 ADT).

| Commit | Time | Content |
|---|---|---|
| `14d5995` | 2026-10-10 12:56 | M1: cluster state modeling (first commit; empty directory before) |
| `7cc9a8b` | 2026-10-10 12:56 | MIT license, `.gitignore` |
| `0a7f06c` | 2026-10-10 13:21 | M2: classical placement engine |
| `3b9afce` | 2026-10-10 14:00 | M3: QUBO/QAOA, Qollab demo |
| `a82dc48` | 2026-10-10 14:57 | Qollab hotfix (`__future__` import) |
| later commits | 2026-10-10 onward | hosted evidence, public dataset, submission documents |

The first commit was rewritten once, before the repository was first published, to change the author email from a work address to a personal one (same content, new hash `14d5995`). The history has not been rewritten since publication, and nothing has ever been force-pushed.

## What existed before the window

Nothing from this project. The working directory was empty at the start.

## How it was built

| Aspect | Disclosure |
|---|---|
| AI assistance | The code, tests and documentation were written by AI coding agents (Anthropic's Claude Code, using Claude models). The author set the milestone specifications and acceptance gates, and reviewed and directed the work. Separate AI reviewer agents audited each milestone; their findings and fixes are in the milestone reports |
| Hosted runs | The author performed the Qollab hosted runs and supplied the results (`docs/m3/hosted-evidence.md`) |
| Demo video narration | synthetic text-to-speech voice (macOS) |

## Third-party material

| Material | Licence | Use |
|---|---|---|
| Qiskit 2.x | Apache-2.0 | library (circuits, primitives, transpiler) |
| qiskit-optimization 0.7 | Apache-2.0 | COBYLA optimizer; independent Ising cross-check |
| numpy, scipy | BSD-3-Clause | numerics (via Qiskit) |
| jsonschema | MIT | schema validation |
| pytest | MIT | tests |
| qiskit-ionq | Apache-2.0 | local investigation only (not a project dependency) |
| Google cluster trace `clusterdata-2011-2` | CC-BY 4.0 | derived values in `fixtures/m5/` (see `docs/m5/public-dataset.md`) |
| Kubernetes documentation | CC-BY 4.0 | consulted and linked for resource semantics (`docs/m1/kubernetes-semantics.md`); no text copied |
| QAOA | – | standard algorithm: E. Farhi, J. Goldstone, S. Gutmann, *A Quantum Approximate Optimization Algorithm*, arXiv:1411.4028 (2014) |
| Qollab runtime conventions | – | `backend` injection and the `main()` call were learned from public hackathon projects (e.g. github.com/aaasocial/quantum-magnav). No code was copied |

## Licence

The project is MIT-licensed (`LICENSE`), and every dependency licence above is compatible with MIT distribution.
