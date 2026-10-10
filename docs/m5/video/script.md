# QPlace demo video: script and storyboard

Target length: 2:30 (hard limits 2:00–3:00). Narration is about 150 words per minute.

**Title:** QPlace: Quantum-Assisted Kubernetes Pre-Drain Feasibility (Qollab × IonQ Hackathon 2026)

**Description (for YouTube, Loom or Vimeo):**
QPlace asks a question SREs face before node maintenance: if I drain this Kubernetes node, can its pods fit somewhere else? A greedy placement can fail even when a valid plan exists. QPlace models the cluster from a saved snapshot, solves the placement exactly with classical methods, encodes it as a QUBO, and runs QAOA with Qiskit on Qollab. Every quantum sample is checked by an independent verifier. On Qollab's built-in simulator and IonQ's remote simulator (Aria 1 noise model), QAOA sampled the complete 2-of-2 placement that greedy misses in about 33% of shots, against 12.5% for random guessing. No quantum advantage is claimed: this is a small, verified, hybrid experiment. Code (MIT): https://github.com/edeediong/qplace-k8s. This effort is supported by Qollab & IonQ.

---

## Segment 1: Problem (0:00–0:20)

**Visual:** title card, then a diagram. Node `source` is marked "draining". Two pods (`batch`, `indexer`) on it, with arrows toward `node-a` and `node-b`.

**Narration:**
Before maintenance, an engineer drains a Kubernetes node, and every pod on it must fit somewhere else. Each node has limited CPU, memory and pod slots, and some pods may only run on certain nodes. QPlace checks whether that is possible before anything is evicted.

## Segment 2: Greedy failure (0:20–0:45)

**Visual:** the two destinations, each with 2500 millicores free. `batch` needs 2000m and can go anywhere. `indexer` needs 1500m and must run on an SSD node; only `node-a` has an SSD. Animation in steps: greedy puts `batch` on `node-a` (500m left), then `indexer` has nowhere to go. Red ✗, "Greedy: 1 of 2 pods".

**Narration:**
Here is a small trap. Greedy placement handles the biggest pod first and puts batch on the first node that fits, node-a. That leaves 500 millicores. The indexer pod needs an SSD node, and node-a is the only one, so it has nowhere to go. Greedy places one pod out of two.

## Segment 3: Exact classical answer (0:45–1:10)

**Visual:** `batch` → `node-b`, `indexer` → `node-a`, with green ✓ and "Exact: 2 of 2 pods, proven optimal". Small caption: "independent verifier: CPU, memory, pod slots, eligibility ✓".

**Narration:**
A plan does exist. Our exact branch-and-bound solver puts batch on node-b and indexer on node-a, so both pods fit. An independent verifier checks every assignment against the cluster model: eligibility, CPU, memory and pod slots, added up per node.

## Segment 4: Quantum formulation (1:10–1:40)

**Visual:** a three-row table of qubits (q0 = batch→node-a, q1 = batch→node-b, q2 = indexer→node-a). The QUBO: `−x0 −x1 −x2 + 3·x0·x1 + 3·x0·x2`. The **actual** QAOA circuit diagram drawn from the repository circuit. A bitstring example: `110` = batch→node-b, indexer→node-a.

**Narration:**
For the quantum experiment, each allowed pod-to-node choice becomes one qubit, three in total. The QUBO rewards every placed pod and penalizes breaking a rule: putting one pod in two places, or overloading node-a's CPU. Before any quantum run, we check every bitstring to prove the lowest-energy states are exactly the best valid placements. QAOA at depth two then prepares a state, and each measured bitstring decodes to a placement.

## Segment 5: Hosted Qollab results (1:40–2:10)

**Visual:** bar chart of the IonQ remote simulator's measured distribution (8 bitstrings, 256 shots). `110` is highlighted (83 shots), and the infeasible bars (`011`, `101`, `111`) are greyed. A comparison table:

| | 2/2 placements |
|---|---|
| Qollab built-in simulator | 84 / 256 (32.8%) |
| IonQ simulator (Aria 1 noise model) | 83 / 256 (32.4%) |
| Random guessing | 12.5% |

Footer caption: "Hosted runs by the project owner on Qollab, 2026-10-10. IonQ result is a remote simulator with a hardware noise model, not quantum hardware."

**Narration:**
We ran the project on Qollab with a single job per run. On the built-in simulator, 84 of 256 shots gave the complete placement. On IonQ's simulator with the Aria 1 noise model, 83 of 256 did. Random guessing would hit it only 12.5 percent of the time. Every sample was decoded and checked; invalid ones are counted, never repaired.

## Segment 6: Verification and conclusion (2:10–2:30)

**Visual:** "What this shows / what it does not" card. Repository URL. MIT. **"This effort is supported by Qollab & IonQ"**.

**Narration:**
The classical solver is still faster and proves optimality, so we claim no quantum advantage. What QPlace shows is a working hybrid pipeline: from a cluster snapshot, to a verified quantum sample, with honest baselines. It is read-only and synthetic data only. The code is open source at github dot com slash edeediong slash qplace-k8s. This effort is supported by Qollab and IonQ.
