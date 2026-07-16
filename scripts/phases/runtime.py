"""Shared helpers for preserving provider runtime evidence across phases."""
from collections.abc import Mapping
from typing import Any

from adversarial_common import NoProviderAvailable, collect_provider_history

__all__ = [
    "merge_provider_history",
    "merge_runtime",
    "merge_warnings",
    "raise_no_provider_available",
    "runtime_metadata",
]


def runtime_metadata(result: Any) -> dict:
    """Copy JSON-safe runner evidence without changing tuple compatibility."""
    metadata = getattr(result, "metadata", {})
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def merge_runtime(calls: list[dict]) -> dict:
    """Combine schema-retry calls into one auditable phase record."""
    attempts = []
    cap_events = []
    for index, call in enumerate(calls, 1):
        attempts.extend(
            {"call": index, **item} for item in call.get("attempts", [])
        )
        cap_events.extend(
            {"call": index, **item} for item in call.get("cap_events", [])
        )
    return {"calls": calls, "attempts": attempts, "cap_events": cap_events}


def merge_warnings(payload: dict, extra: list) -> list:
    """Preserve model and parser warnings across parse retries."""
    warnings = list(payload.get("warnings", []))
    for warning in extra:
        if warning not in warnings:
            warnings.append(warning)
    return warnings


def merge_provider_history(results: list[Any]) -> list[dict]:
    """Return provider decisions from phase calls in execution order."""
    return collect_provider_history(results)


def raise_no_provider_available(result: Any, role: str) -> None:
    """Restore ``NoProviderAvailable`` hidden by ``run_phase_cmd``'s tuple API.

    The canonical runner represents provider exhaustion as a tuple-compatible
    result so generic callers can persist it.  The code loop has a dedicated
    process-level exit contract, so phases turn that result back into the
    domain exception and let the orchestrator report the complete snapshot.
    """
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, Mapping):
        return
    snapshots = metadata.get("raw_snapshots")
    reasons = metadata.get("rejection_reasons")
    if not isinstance(snapshots, Mapping) or not isinstance(reasons, Mapping):
        return
    raise NoProviderAvailable(role, snapshots, reasons)
