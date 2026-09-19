"""Fault catalog and request validation for the experiment engine (pure, no DB).

`implemented` says whether a REAL injector exists. Until then only dry_run experiments
are accepted for that fault — the engine never pretends to have injected anything.
"""
from dataclasses import dataclass

# The measurement plane must survive experiments so results can be recorded.
PROTECTED_TARGETS = frozenset({"control-plane", "telemetry-consumer"})

LIMITS = {
    "duration_s": (5, 300),
    "baseline_s": (0, 120),
    "recovery_s": (5, 300),
}

ALL_KINDS = frozenset({"service", "database", "cache", "broker"})


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # "int" | "float"
    minimum: float
    maximum: float
    required: bool = True
    default: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class FaultSpec:
    fault_type: str
    description: str
    target_kinds: frozenset
    params: tuple = ()
    implemented: bool = False


FAULTS: dict[str, FaultSpec] = {f.fault_type: f for f in (
    FaultSpec(
        "stop_container",
        "docker stop the target for the duration, then start it. The container's DNS name "
        "disappears, so callers see slow name-resolution failures.",
        ALL_KINDS),
    FaultSpec(
        "pause_container",
        "Freeze the target (docker pause). Callers see timeouts instead of refusals.",
        ALL_KINDS),
    FaultSpec(
        "latency",
        "Add network latency to traffic to/from the target.",
        ALL_KINDS,
        (ParamSpec("latency_ms", "int", 10, 10000, unit="ms"),
         ParamSpec("jitter_ms", "int", 0, 5000, required=False, default=0, unit="ms"))),
    FaultSpec(
        "http_error",
        "Make the target's HTTP endpoints fail a fraction of requests (needs a fault hook in the service).",
        frozenset({"service"}),
        (ParamSpec("error_rate", "float", 0.01, 1.0),
         ParamSpec("status_code", "int", 500, 599, required=False, default=500))),
    FaultSpec(
        "cpu_stress",
        "Consume CPU inside the target's container.",
        ALL_KINDS,
        (ParamSpec("cpu_percent", "int", 10, 100, unit="%"),)),
    FaultSpec(
        "memory_stress",
        "Consume memory inside the target's container.",
        ALL_KINDS,
        (ParamSpec("memory_mb", "int", 16, 1024, unit="MB"),)),
)}


def catalog_as_dict() -> dict:
    return {
        "limits": {k: {"min": lo, "max": hi} for k, (lo, hi) in LIMITS.items()},
        "protected_targets": sorted(PROTECTED_TARGETS),
        "faults": [
            {
                "fault_type": f.fault_type,
                "description": f.description,
                "target_kinds": sorted(f.target_kinds),
                "implemented": f.implemented,
                "parameters": [
                    {"name": p.name, "type": p.type, "min": p.minimum, "max": p.maximum,
                     "required": p.required, "default": p.default, "unit": p.unit}
                    for p in f.params
                ],
            }
            for f in FAULTS.values()
        ],
    }


def _is_number(value, kind: str) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) if kind == "int" else isinstance(value, (int, float))


def validate_request(
    *,
    target: str,
    fault_type: str,
    parameters: dict,
    duration_s: int,
    baseline_s: int,
    recovery_s: int,
    dry_run: bool,
    services: dict[str, dict],
) -> tuple[list[str], dict]:
    """Return (errors, normalized_parameters). Empty errors means the request is acceptable.

    services: {name: {"kind": ..., "container": ...}} from the service registry.
    """
    errors: list[str] = []

    for field, value in (("duration_s", duration_s), ("baseline_s", baseline_s), ("recovery_s", recovery_s)):
        lo, hi = LIMITS[field]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            errors.append(f"{field} must be an integer between {lo} and {hi}")

    service = services.get(target)
    if service is None:
        errors.append(f"unknown target '{target}' (not in the service registry)")
    elif target in PROTECTED_TARGETS:
        errors.append(f"target '{target}' is protected: it is part of the measurement plane")
    elif not service.get("container"):
        errors.append(f"target '{target}' has no container mapping, so it cannot be a fault target")

    spec = FAULTS.get(fault_type)
    if spec is None:
        errors.append(f"unknown fault_type '{fault_type}' (see GET /api/experiments/catalog)")
        return errors, {}

    if service is not None and service["kind"] not in spec.target_kinds:
        errors.append(
            f"fault '{fault_type}' cannot target a {service['kind']} "
            f"(allowed: {', '.join(sorted(spec.target_kinds))})")

    if not dry_run and not spec.implemented:
        errors.append(
            f"fault '{fault_type}' has no real injector yet; only dry_run=true is accepted")

    normalized: dict = {}
    allowed = {p.name for p in spec.params}
    for key in parameters:
        if key not in allowed:
            errors.append(f"unexpected parameter '{key}' for fault '{fault_type}'")

    for p in spec.params:
        if p.name not in parameters:
            if p.required:
                errors.append(f"parameter '{p.name}' is required for fault '{fault_type}'")
            elif p.default is not None:
                normalized[p.name] = p.default
            continue
        value = parameters[p.name]
        if not _is_number(value, p.type):
            errors.append(f"parameter '{p.name}' must be a {p.type}")
        elif not p.minimum <= value <= p.maximum:
            errors.append(f"parameter '{p.name}' must be between {p.minimum:g} and {p.maximum:g}")
        else:
            normalized[p.name] = value

    return errors, normalized
