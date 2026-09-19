"""Remediation policy engine (pure).

Decides whether a proposed action may run. It never executes anything and never accepts a
command: only an allowlisted action name plus a target service.

  allow           may run now (in execute mode)
  needs_approval  high risk: an administrator must approve first
  deny            refused, with reasons
  unsupported     allowlisted in principle but no mechanism exists in this environment
"""
from dataclasses import dataclass, field

ALLOWLIST = ("RESTART_SERVICE", "SCALE_SERVICE", "ROLLBACK_DEPLOYMENT", "CLEAR_CACHE", "ENABLE_FALLBACK")
SUPPORTED = ("RESTART_SERVICE", "SCALE_SERVICE", "CLEAR_CACHE")
UNSUPPORTED_REASON = {
    "ROLLBACK_DEPLOYMENT": "no deployment history exists in this environment (available once services run on Kubernetes)",
    "ENABLE_FALLBACK": "no feature-flag or fallback mechanism exists in the demo workload",
}

# The measurement plane is never remediated automatically: it must keep observing.
PROTECTED_TARGETS = frozenset({"control-plane", "telemetry-consumer"})
STATEFUL_KINDS = frozenset({"database", "broker", "cache"})
CACHE_PREFIX = "cache:"

COOLDOWN_S = 600
COOLDOWN_MAX_ACTIONS = 2


@dataclass
class Verdict:
    verdict: str
    risk: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "risk": self.risk, "reasons": self.reasons}


def evaluate(request: dict, registry: dict[str, dict], *, active_experiment: bool, running_remediation: bool,
             recent_actions: list[dict], now: float, approved: bool = False) -> Verdict:
    """
    request:  {action, target, params}
    registry: {service: {"kind", "container"}}
    recent_actions: [{"target", "ts", "status"}] executed remediations (any status that reached the executor)
    """
    action, target, params = request["action"], request["target"], request.get("params") or {}

    if action not in ALLOWLIST:
        return Verdict("deny", "n/a", [f"action '{action}' is not on the allowlist ({', '.join(ALLOWLIST)})"])
    if action not in SUPPORTED:
        return Verdict("unsupported", "n/a", [f"{action}: {UNSUPPORTED_REASON[action]}"])

    service = registry.get(target)
    if service is None:
        return Verdict("deny", "n/a", [f"unknown target '{target}' (not in the service registry)"])
    if target in PROTECTED_TARGETS:
        return Verdict("deny", "n/a", [f"target '{target}' is protected: the measurement plane is never remediated automatically"])

    reasons: list[str] = []
    if action == "CLEAR_CACHE":
        if service["kind"] != "cache":
            reasons.append(f"CLEAR_CACHE only applies to a cache, not a {service['kind']}")
        prefix = params.get("prefix", CACHE_PREFIX)
        if not isinstance(prefix, str) or not prefix.startswith(CACHE_PREFIX):
            reasons.append(f"CLEAR_CACHE may only delete keys under '{CACHE_PREFIX}' (got {prefix!r})")
    elif not service.get("container"):
        reasons.append(f"target '{target}' has no container mapping, so it cannot be remediated")

    if active_experiment:
        reasons.append("an experiment is active: remediation is blocked so the two do not interfere")
    if running_remediation:
        reasons.append("another remediation is already running (one at a time)")
    recent = [a for a in recent_actions if a["target"] == target and now - a["ts"] < COOLDOWN_S]
    if len(recent) >= COOLDOWN_MAX_ACTIONS:
        reasons.append(f"cooldown: {len(recent)} remediations on '{target}' in the last {COOLDOWN_S // 60} minutes "
                       "(possible remediation loop); escalate to a human")
    if reasons:
        return Verdict("deny", "n/a", reasons)

    risk = "high" if action == "RESTART_SERVICE" and service["kind"] in STATEFUL_KINDS else "low"
    if risk == "high" and not approved:
        return Verdict("needs_approval", risk,
                       [f"restarting a stateful {service['kind']} ('{target}') can interrupt every service that "
                        "depends on it; an administrator must approve"])
    return Verdict("allow", risk, ["action is allowlisted, target is eligible, no conflicting activity"])


def action_for_fault(fault_type: str) -> str | None:
    """Deterministic mapping from a recognised failure character to the allowlisted remedy, or None."""
    return {"stop_container": "RESTART_SERVICE", "pause_container": "RESTART_SERVICE",
            "restart_container": "RESTART_SERVICE", "http_error": "RESTART_SERVICE",
            "cpu_stress": "SCALE_SERVICE"}.get(fault_type)
