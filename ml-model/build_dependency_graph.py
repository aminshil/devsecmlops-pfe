"""
Extracts the machine dependency graph implied by generate_telecom_fleet.py's
correlation logic (router_names[idx % len(router_names)]) and saves it as
a reusable JSON artifact for root cause analysis.

Each non-router/firewall/dns machine depends on exactly one router, matching
the SAME assignment the generator uses to inject correlated anomalies --
so this graph is ground truth for our synthetic data, not a guess.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_telecom_fleet import build_fleet, NETWORK_GEAR

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "models" / "dependency_graph.json"


def build_dependency_graph(n_machines=200):
    fleet = build_fleet(n_machines)
    router_names = [name for name, pname, _ in fleet if pname == "router"]

    graph = {"depends_on": {}, "routers": router_names, "downstream_of": {r: [] for r in router_names}}

    for idx, (name, pname, _) in enumerate(fleet):
        if pname not in ("router", "firewall", "dns") and router_names:
            router = router_names[idx % len(router_names)]
            graph["depends_on"][name] = router
            graph["downstream_of"][router].append(name)

    return graph


if __name__ == "__main__":
    graph = build_dependency_graph(200)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(graph, f, indent=2)
    print(f"Dependency graph saved: {OUTPUT}")
    print(f"  Routers: {len(graph['routers'])}")
    print(f"  Machines with dependencies: {len(graph['depends_on'])}")
    for r in graph["routers"][:3]:
        deps = graph["downstream_of"][r]
        print(f"  {r} -> {len(deps)} downstream: {deps[:5]}{'...' if len(deps) > 5 else ''}")
