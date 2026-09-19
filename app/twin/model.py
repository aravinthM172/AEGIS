"""Digital twin (pure): a simplified, explicitly-labelled model of how failures propagate.

Learned from measurements, never invented:
  coupling(edge, fault_class) = how much of a callee's degradation reaches its caller, taken from
  stored failure signatures. Where nothing was measured the twin ASSUMES a hard dependency
  (coupling 1.0) and says so in `assumptions`.

Outputs are always tagged: basis "measured" (aggregated from real experiments) or "simulated".
A simulated architecture change is a hypothesis to test with a real experiment, never a result.
"""
import statistics
from collections import defaultdict

FAULT_CLASS = {
    "stop_container": "outage", "pause_container": "outage", "restart_container": "outage",
    "latency": "latency", "cpu_stress": "degradation", "memory_stress": "degradation", "http_error": "errors",
}
ENTRY = "gateway"          # where user requests enter (the end-user proxy)
AFFECTED_PCT = 10.0


def fault_class(fault_type: str) -> str:
    return FAULT_CLASS.get(fault_type, fault_type)


class TwinModel:
    def __init__(self, edges: list[tuple[str, str]], signatures: list[dict], entry: str = ENTRY):
        """edges: (caller, callee) meaning caller depends on callee. signatures: [{"id","signature"}]."""
        self.edges = sorted(set(edges))
        self.entry = entry
        self.callees = defaultdict(list)
        for caller, callee in self.edges:
            self.callees[caller].append(callee)
        self.observations = [r["signature"] for r in signatures if r["signature"]["kind"] == "fault"]
        self._coupling = self._learn_coupling()

    # ---- measured facts ----------------------------------------------------------------------

    def measured(self, target: str, fault_type: str) -> dict | None:
        rows = [s for s in self.observations
                if s["trigger"]["target"] == target and s["trigger"]["fault_type"] == fault_type]
        if not rows:
            return None
        eu = [s["observed"]["end_user"]["impact_pct"] for s in rows
              if s["observed"].get("end_user") and s["observed"]["end_user"]["impact_pct"] is not None]
        services: dict[str, list[float]] = defaultdict(list)
        for s in rows:
            for name, e in s["observed"]["services"].items():
                if e["role"] not in ("client",) and e["impact_pct"] is not None:
                    services[name].append(e["impact_pct"])
        recov = [s["observed"]["recovery"]["recovery_s"] for s in rows
                 if s["observed"]["recovery"].get("recovery_s") is not None]
        return {
            "basis": "measured", "runs": len(rows),
            "end_user_impact_pct": round(statistics.median(eu), 1) if eu else None,
            "service_impact_pct": {n: round(statistics.median(v), 1) for n, v in sorted(services.items())},
            "recovery_s": round(statistics.median(recov), 1) if recov else None,
            "confidence": "low" if len(rows) < 3 else "medium" if len(rows) < 5 else "high",
        }

    # ---- coupling learned from measurements -------------------------------------------------------

    def _learn_coupling(self) -> dict[tuple[str, str, str], dict]:
        """(caller, callee, class) -> {"value", "runs", "basis"} from experiments where the callee was degraded."""
        samples: dict[tuple, list[float]] = defaultdict(list)
        for s in self.observations:
            cls = fault_class(s["trigger"]["fault_type"])
            impact = {n: e["impact_pct"] for n, e in s["observed"]["services"].items() if e["impact_pct"] is not None}
            impact[s["trigger"]["target"]] = impact.get(s["trigger"]["target"], 100.0)
            silent_or_target = {n for n, e in s["observed"]["services"].items() if e.get("silent") or e["role"] == "target"}
            for caller, callee in self.edges:
                if caller not in impact:
                    continue
                callee_impact = impact.get(callee)
                if callee_impact is None:
                    continue
                if callee not in silent_or_target and callee_impact < AFFECTED_PCT:
                    continue  # the callee was not degraded here: says nothing about coupling
                samples[(caller, callee, cls)].append(min(1.0, max(0.0, impact[caller] / max(callee_impact, 1e-6))))
            # a service that was the target degrades fully (100) even if it emits no telemetry
        out = {}
        for key, values in samples.items():
            out[key] = {"value": round(statistics.median(values), 2), "runs": len(values), "basis": "measured"}
        return out

    def coupling(self, caller: str, callee: str, cls: str) -> dict:
        if (caller, callee, cls) in self._coupling:
            return self._coupling[(caller, callee, cls)]
        # any class, if the same edge was observed degraded under a different kind of fault
        others = [v for (c, e, k), v in self._coupling.items() if c == caller and e == callee]
        if others:
            best = max(others, key=lambda v: v["runs"])
            return {**best, "basis": "measured_other_class"}
        return {"value": 1.0, "runs": 0, "basis": "assumed_hard"}

    # ---- simulation ----------------------------------------------------------------------------------

    def simulate(self, target: str, fault_type: str, mutations: list[dict] | None = None) -> dict:
        cls = fault_class(fault_type)
        mutations = mutations or []
        assumptions: list[str] = []
        edge_factor: dict[tuple[str, str], float] = {}
        notes: list[str] = []
        target_impact = 100.0

        for m in mutations:
            kind = m["type"]
            if kind == "queue":
                edge_factor[(m["source"], m["target"])] = 0.0
                assumptions.append(f"{m['source']} hands work to a queue instead of calling {m['target']} synchronously: "
                                   "user requests no longer wait for it, and the work is processed later")
                notes.append(f"work for {m['target']} is deferred while it is down (backlog, not failed requests)")
            elif kind == "fallback":
                hit = float(m.get("hit_ratio", 0.8))
                edge_factor[(m["source"], m["target"])] = edge_factor.get((m["source"], m["target"]), 1.0) * (1 - hit)
                assumptions.append(f"{m['source']} serves a cached/degraded response for {m['target']} in {hit:.0%} of calls")
            elif kind == "replicas":
                n = int(m.get("count", 2))
                if m["service"] == target and n > 1:
                    target_impact = 100.0 / n
                    assumptions.append(f"{target} runs {n} replicas behind a load balancer and one replica fails ABRUPTLY "
                                       f"and stays in rotation: about 1/{n} of requests are affected until it is removed. "
                                       "A graceful removal (e.g. Kubernetes pod deletion, measured at 0% failed requests with "
                                       "2 replicas) is removed from rotation first, so this is a pessimistic bound")
            else:
                raise ValueError(f"unknown mutation type '{kind}'")

        impact: dict[str, float] = {target: target_impact}
        edge_notes: list[dict] = []
        changed = True
        while changed:  # propagate up the dependency chain until stable (graph is a DAG)
            changed = False
            for caller, callee in self.edges:
                if callee not in impact:
                    continue
                c = self.coupling(caller, callee, cls)
                factor = edge_factor.get((caller, callee), 1.0)
                value = round(impact[callee] * c["value"] * factor, 1)
                if value > impact.get(caller, 0.0) + 1e-9:
                    impact[caller] = value
                    changed = True
                    edge_notes.append({"edge": f"{caller} -> {callee}", "coupling": c["value"], "coupling_basis": c["basis"],
                                       "runs": c["runs"], "mutation_factor": factor})

        for e in edge_notes:
            if e["coupling_basis"] == "assumed_hard" and e["mutation_factor"] == 1.0:
                assumptions.append(f"{e['edge']}: never measured under a {cls} fault; assumed a HARD dependency "
                                   "(the caller inherits the callee's full impact). Verify with an experiment.")

        entry_impact = impact.get(self.entry, 0.0)
        return {
            "basis": "simulated", "target": target, "fault_type": fault_type, "fault_class": cls,
            "end_user_impact_pct": entry_impact,
            "service_impact_pct": {n: v for n, v in sorted(impact.items())},
            "affected_services": sorted(n for n, v in impact.items() if v >= AFFECTED_PCT and n != target),
            "propagation": sorted({e["edge"] for e in edge_notes if impact.get(e["edge"].split(" -> ")[0], 0) >= AFFECTED_PCT}),
            "edges": edge_notes, "assumptions": sorted(set(assumptions)), "notes": notes, "mutations": mutations,
        }

    def compare(self, target: str, fault_type: str, mutations: list[dict]) -> dict:
        """Measured baseline (if any) vs simulated baseline vs simulated architecture change."""
        before = self.simulate(target, fault_type)
        after = self.simulate(target, fault_type, mutations)
        measured = self.measured(target, fault_type)
        delta = round(after["end_user_impact_pct"] - before["end_user_impact_pct"], 1)
        return {
            "measured_baseline": measured, "simulated_baseline": before, "simulated_with_change": after,
            "simulated_change_in_end_user_impact_pct": delta,
            "disclaimer": "The 'with change' figure is a SIMULATION built on stated assumptions, not a measurement. "
                          "Implement the change and rerun the same experiment to measure it.",
        }


def validate(edges: list[tuple[str, str]], signatures: list[dict], entry: str = ENTRY) -> dict:
    """Leave-one-combination-out: hide every run of (target, fault_type), predict its end-user impact
    from structure + the other experiments, and compare with what was measured."""
    combos = sorted({(s["signature"]["trigger"]["target"], s["signature"]["trigger"]["fault_type"])
                     for s in signatures if s["signature"]["kind"] == "fault"})
    rows = []
    for target, fault in combos:
        held_out = [s for s in signatures
                    if not (s["signature"]["trigger"]["target"] == target and s["signature"]["trigger"]["fault_type"] == fault)]
        truth = TwinModel(edges, signatures, entry).measured(target, fault)
        if not truth or truth["end_user_impact_pct"] is None:
            continue
        sim = TwinModel(edges, held_out, entry).simulate(target, fault)
        predicted = sim["end_user_impact_pct"]
        measured = truth["end_user_impact_pct"]
        rows.append({
            "target": target, "fault_type": fault, "runs_hidden": truth["runs"],
            "measured_end_user_impact_pct": measured, "predicted_end_user_impact_pct": predicted,
            "abs_error_pct": round(abs(predicted - measured), 1),
            "affected_class_correct": (predicted >= AFFECTED_PCT) == (measured >= AFFECTED_PCT),
            "assumed_edges": [e["edge"] for e in sim["edges"] if e["coupling_basis"] == "assumed_hard"],
        })
    n = len(rows)
    return {
        "method": "leave-one-combination-out: all runs of a (target, fault) are hidden, then its end-user impact is "
                  "predicted from the graph and the remaining experiments",
        "combinations_tested": n,
        "mean_abs_error_pct": round(sum(r["abs_error_pct"] for r in rows) / n, 1) if n else None,
        "affected_or_not_accuracy": round(sum(r["affected_class_correct"] for r in rows) / n, 3) if n else None,
        "rows": rows,
        "caveats": ["small sample: a handful of fault/target combinations, one workload",
                    "the simplified model has no queueing, retries or capacity: it propagates degradation only"],
    }
