"""Shared helpers for preserving provider runtime evidence across phases."""
from collections.abc import Mapping
from typing import Any

__all__ = ["merge_runtime", "merge_warnings", "runtime_metadata"]


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
