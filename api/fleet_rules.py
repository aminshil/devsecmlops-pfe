"""How the Fleet tab reads a router's rack from live model verdicts. Pure logic, no I/O: the API
(/ui/fleet), its tests and scripts/rack_rule_eval.py all use this one implementation.

It reasons the way network monitoring does:
  * children that stop reporting behind a parent are UNREACHABLE and the parent is the suspect
    (Nagios parent/child reachability): they are cut off, not failing;
  * many dependents degraded together point upstream even when the router's own metrics look
    normal: a gray failure, i.e. silent packet loss the router does not report (Huang et al.,
    HotOS 2017);
  * a busy router with healthy dependents is a control-plane event or a failure still propagating.
"""

STALE_AFTER_S = 120     # four missed 30-second reports: the machine is not reaching the pods
SUSPECT_SHARE = 0.4     # share of a router's reporting dependents degraded together
MIN_GROUP = 4           # fewer machines than this is not a pattern
QUIET_SHARE = 0.15      # below this, a router's dependents count as healthy

FLAGGING = {"unreachable", "router_incident", "gray_suspected"}   # states that name the router as suspect


def classify(n_seen: int, n_stale: int, n_live: int, n_bad: int, hub_bad: bool,
             suspect_share: float = SUSPECT_SHARE, min_group: int = MIN_GROUP) -> str:
    """State of one router from counts: dependents seen, stale (not reporting), live (reporting),
    live and anomalous, and whether the router itself is reporting and anomalous."""
    share = n_bad / n_live if n_live else 0.0
    if n_stale >= min_group and n_stale >= 0.6 * n_seen:
        return "unreachable"
    if n_bad >= min_group and share >= suspect_share:
        return "router_incident" if hub_bad else "gray_suspected"
    if hub_bad and share < QUIET_SHARE:
        return "router_only"
    return "ok"


def classify_arrays(n_seen, n_stale, n_live, n_bad, hub_bad,
                    suspect_share: float = SUSPECT_SHARE, min_group: int = MIN_GROUP):
    """classify() over numpy arrays (one element per router-step), for offline evaluation.
    Same rules, same order; tests/test_rack_tools.py checks the two agree element for element."""
    import numpy as np
    n_seen, n_stale, n_live, n_bad = (np.asarray(x) for x in (n_seen, n_stale, n_live, n_bad))
    hub_bad = np.asarray(hub_bad, dtype=bool)
    share = np.divide(n_bad, n_live, out=np.zeros(np.broadcast(n_bad, n_live).shape, dtype=float),
                      where=np.asarray(n_live) > 0)
    unreachable = (n_stale >= min_group) & (n_stale >= 0.6 * n_seen)
    group = (n_bad >= min_group) & (share >= suspect_share)
    return np.select([unreachable, group & hub_bad, group, hub_bad & (share < QUIET_SHARE)],
                     ["unreachable", "router_incident", "gray_suspected", "router_only"], default="ok")


def router_states(machines: dict, routers: list, downstream_of: dict) -> dict:
    """machines: name -> {"anomalous": 0/1, "stale": bool, ...} from the pods' gauges."""
    out = {}
    for r in routers:
        deps = downstream_of.get(r, [])
        seen = [d for d in deps if d in machines]
        stale = [d for d in seen if machines[d].get("stale")]
        live = [d for d in seen if not machines[d].get("stale")]
        bad = [d for d in live if machines[d].get("anomalous") == 1]
        hub = machines.get(r, {})
        hub_bad = hub.get("anomalous") == 1 and not hub.get("stale")
        st = classify(len(seen), len(stale), len(live), len(bad), hub_bad)
        msg = {
            "unreachable": (f"{len(stale)} of {len(seen)} dependents stopped reporting: {r} down or its "
                            f"uplink lost is the likely cause (the machines are unreachable, not failing)"),
            "router_incident": (f"{r} and {len(bad)} of {len(live)} dependents are anomalous together: "
                                f"likely root cause {r}"),
            "gray_suspected": (f"{len(bad)} of {len(live)} dependents are degraded together while {r}'s own "
                               f"metrics look normal: suspected gray failure upstream (silent packet loss the "
                               f"router does not report). Likely root cause {r}"),
            "router_only": (f"{r} is anomalous but its dependents are healthy: a control-plane event, or a "
                            f"failure that has not propagated yet"),
        }.get(st, "")
        out[r] = {"state": st, "message": msg, "anomalous": len(bad), "reporting": len(live),
                  "stale": len(stale), "dependents": len(deps)}
    return out
