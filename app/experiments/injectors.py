"""Fault injectors. The engine only talks to this interface.

Contract:
  inject(exp)    apply the fault; return a dict describing what was ACTUALLY done
  rollback(exp)  undo it; must be idempotent and safe to call even if inject never ran
                 or only partly ran (the engine calls it on every exit path)

Phase 5 adds the Docker injector. Until then only DryRunInjector exists, and its
results are labelled applied=False so a dry run can never be mistaken for a real one.
"""
from typing import Protocol


class InjectorUnavailable(RuntimeError):
    pass


class Injector(Protocol):
    name: str

    def inject(self, exp: dict) -> dict: ...

    def rollback(self, exp: dict) -> dict: ...


class DryRunInjector:
    name = "dry_run"

    def inject(self, exp: dict) -> dict:
        return {
            "mode": "dry_run",
            "applied": False,
            "fault_type": exp["fault_type"],
            "target": exp["target"],
            "parameters": exp["parameters"],
            "note": "no fault was applied",
        }

    def rollback(self, exp: dict) -> dict:
        return {"mode": "dry_run", "applied": False, "note": "nothing to roll back"}


def injector_for(exp: dict) -> Injector:
    if exp["dry_run"]:
        return DryRunInjector()
    raise InjectorUnavailable(f"no real injector for fault '{exp['fault_type']}'")
