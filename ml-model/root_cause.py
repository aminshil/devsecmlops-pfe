"""
Root cause scoring for cascading anomalies.

Given a set of machines currently flagged as anomalous, ranks them by
likelihood of being the true ROOT CAUSE versus a DOWNSTREAM VICTIM of
a cascading failure, using the dependency graph built by
build_dependency_graph.py.

Approach (simplified from MicroHECL-style dependency-graph RCA):
  1. Graph position: a router whose downstream machines are ALSO
     anomalous is a strong root-cause candidate -- it explains multiple
     alerts at once, not just its own.
  2. Fan-out: the more anomalous downstream machines a router has, the
     higher its root-cause score (explains more of the alert burst).
  3. Severity: the candidate's own anomaly_score still matters -- a
     router with a weak anomaly score explaining many downstream
     effects can still outrank a machine with a strong anomaly score
     explaining nothing but itself.

Score = own_anomaly_score + (anomalous_downstream_count * WEIGHT)

This is intentionally simple and explainable -- not a black box. Every
machine's score is fully attributable to two numbers you can verify by
hand: its own severity, and how many of its dependents are also lit up.

Known limitation: only models the network-layer (router) dependency.
The service-tier (web->app->db) graph was tested and found to degrade
model accuracy (see README, "cascading failures" section) -- this
scorer intentionally does NOT use it.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = ROOT / "models" / "dependency_graph.json"

DOWNSTREAM_WEIGHT = 0.15   # score bonus per anomalous downstream dependent


def load_graph():
    with open(GRAPH_PATH) as f:
        return json.load(f)


def score_root_causes(anomalies: dict, graph: dict | None = None):
    """
    anomalies: {machine_name: anomaly_score, ...} -- ONLY machines
               currently flagged is_anomaly=1. anomaly_score should be
               the value returned by /predict (higher = more anomalous).

    Returns a list of dicts, sorted by root-cause likelihood (highest first):
      [{"machine": ..., "own_score": ..., "downstream_anomalous": [...],
        "root_cause_score": ..., "role": "likely_root_cause" | "downstream_effect" | "isolated"}]
    """
    if graph is None:
        graph = load_graph()

    downstream_of = graph["downstream_of"]     # router -> [machines]
    depends_on    = graph["depends_on"]         # machine -> router
    routers       = set(graph["routers"])

    results = []
    for machine, own_score in anomalies.items():
        is_router = machine in routers
        downstream_anomalous = []

        if is_router:
            for dep in downstream_of.get(machine, []):
                if dep in anomalies:
                    downstream_anomalous.append(dep)

        rc_score = own_score + DOWNSTREAM_WEIGHT * len(downstream_anomalous)

        if downstream_anomalous:
            role = "likely_root_cause"
        elif is_router:
            role = "isolated"   # a router with no anomalous dependents
        elif machine in depends_on and depends_on[machine] in anomalies:
            role = "downstream_effect"   # its router is ALSO anomalous
        else:
            role = "isolated"   # anomalous on its own, no graph signal either way

        results.append({
            "machine": machine,
            "own_score": round(own_score, 4),
            "is_router": is_router,
            "upstream_router": depends_on.get(machine),
            "downstream_anomalous": downstream_anomalous,
            "downstream_anomalous_count": len(downstream_anomalous),
            "root_cause_score": round(rc_score, 4),
            "role": role,
        })

    results.sort(key=lambda r: r["root_cause_score"], reverse=True)
    return results


if __name__ == "__main__":
    # Demo scenario: router-01 fails, drags down 4 of its dependents,
    # plus one totally unrelated machine has its own isolated anomaly.
    graph = load_graph()
    router = graph["routers"][0]
    dependents = graph["downstream_of"][router][:4]

    demo_anomalies = {
        router:          0.55,   # router itself: moderate anomaly score
        dependents[0]:   0.62,
        dependents[1]:   0.58,
        dependents[2]:   0.60,
        dependents[3]:   0.59,
        "mystery-01":    0.71,   # unrelated, higher score, no graph link
    }

    print(f"Demo: {router} anomalous, {len(dependents)} of its dependents also anomalous,")
    print("plus one unrelated machine with a higher raw score.\n")

    ranked = score_root_causes(demo_anomalies, graph)
    print(f"{'Rank':<5}{'Machine':<15}{'Own score':<12}{'Downstream':<12}{'RC score':<10}{'Role'}")
    for i, r in enumerate(ranked, 1):
        print(f"{i:<5}{r['machine']:<15}{r['own_score']:<12}{r['downstream_anomalous_count']:<12}"
              f"{r['root_cause_score']:<10}{r['role']}")
