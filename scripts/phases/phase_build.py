"""
BUILD phase: run DEV model, stage and commit all changes.

The DEV model writes files directly into *workdir*; this phase runs it with the
spec on stdin, then stages and commits everything. A zero-change build still
advances history (``commit_all`` forces an empty commit), so the loop branch
always gets a build commit.
"""
from collections.abc import Mapping
from typing import Any

from adversarial_common import NoProviderAvailable, gitops, run_phase_cmd
from scripts.phases.runtime import (
    merge_provider_history,
    raise_no_provider_available,
    runtime_metadata,
)

__all__ = ["run_build"]


def _short_summary(spec_text: str, limit: int = 60) -> str:
    """Derive a one-line commit summary from the first non-empty spec line."""
    for line in (spec_text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:limit]
    return "implementation"


def run_build(
    spec_text: str,
    dev_cmd: str,
    workdir: str,
    timeout: int,
    feature: str,
    resolver: Any = None,
    *,
    explicit_cmd: str | None = None,
    force: bool = False,
    force_provider: str | None = None,
    execution: Mapping[str, Any] | None = None,
    ledger: Any = None,
) -> dict:
    """
    Run the DEV model with the spec as input.

    - ``run_phase_cmd()`` resolves the DEV provider and executes the prompt
    - ``gitops.commit_all(workdir, "build: <feature> — <summary>")``

    Returns ``{"phase": "build", "exit_code": 0, "commit_sha": sha_or_None}``.
    On failure returns ``{"phase": "build", "exit_code": 1, "error": "..."}``.
    """
    try:
        prompt = f"Implement the specification:\n\n{spec_text}"
        execution_args = dict(execution or {})
        if execution is not None or ledger is not None:
            execution_args["phase"] = "build"
        if ledger is not None:
            execution_args["ledger"] = ledger
        command_args = {}
        if resolver is None and explicit_cmd is None:
            command_args["cmd"] = dev_cmd
        provider_result = run_phase_cmd(
            phase_name="build",
            role="dev",
            workdir=workdir,
            resolver=resolver,
            explicit_cmd=explicit_cmd,
            force=force,
            force_provider=force_provider,
            stdin_text=prompt,
            timeout=timeout,
            persona="builder",
            **command_args,
            **execution_args,
        )
        raise_no_provider_available(provider_result, "dev")
        stdout, stderr, code = provider_result[:3]
        runtime = runtime_metadata(provider_result)
        provider_history = merge_provider_history([provider_result])
        if code != 0:
            return {
                "phase": "build",
                "exit_code": 1,
                "error": f"DEV exited {code}: {(stderr or '')[:200]}",
                "stdout": stdout,
                "execution": runtime,
                "provider_history": provider_history,
            }
        summary = _short_summary(spec_text)
        gitops.commit_all(workdir, f"build: {feature} — {summary}")
        return {
            "phase": "build",
            "exit_code": 0,
            "commit_sha": gitops.head_sha(workdir),
            "stdout": stdout,
            "execution": runtime,
            "provider_history": provider_history,
        }
    except NoProviderAvailable:
        raise
    except Exception as exc:  # defensive: never leak an exception to the loop
        return {"phase": "build", "exit_code": 1, "error": str(exc)}
