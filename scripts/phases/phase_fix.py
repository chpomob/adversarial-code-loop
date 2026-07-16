"""
FIX phase: present findings to DEV model, commit fixes.

The DEV model receives the findings list and edits files on disk; this phase
then stages and commits the result as a new fix round.
"""
import json
from collections.abc import Mapping
from typing import Any

from adversarial_common import NoProviderAvailable, gitops, run_phase_cmd
from scripts.phases.runtime import (
    merge_provider_history,
    raise_no_provider_available,
    runtime_metadata,
)

__all__ = ["run_fix"]


def run_fix(
    findings: list,
    dev_cmd: str,
    workdir: str,
    timeout: int,
    feature: str,
    loop_n: int,
    resolver: Any = None,
    *,
    explicit_cmd: str | None = None,
    force: bool = False,
    force_provider: str | None = None,
    execution: Mapping[str, Any] | None = None,
    ledger: Any = None,
) -> dict:
    """
    Present the findings list to the DEV model.

    Stage and commit: ``"fix: <feature> — round {loop_n}"``.

    Returns ``{"phase": "fix", "loop": loop_n, "exit_code": 0,
               "commit_sha": ...}``.
    """
    try:
        prompt = (
            "Address each of the following review findings by editing the files "
            "on disk. Your changes will be committed as a new fix round.\n\n"
            f"Findings:\n```json\n{json.dumps(findings, indent=2)}\n```"
        )
        execution_args = dict(execution or {})
        if execution is not None or ledger is not None:
            execution_args["phase"] = f"fix_{loop_n}"
        if ledger is not None:
            execution_args["ledger"] = ledger
        command_args = {}
        if resolver is None and explicit_cmd is None:
            command_args["cmd"] = dev_cmd
        provider_result = run_phase_cmd(
            phase_name="fix",
            role="dev",
            workdir=workdir,
            resolver=resolver,
            explicit_cmd=explicit_cmd,
            force=force,
            force_provider=force_provider,
            stdin_text=prompt,
            timeout=timeout,
            persona="fixer",
            **command_args,
            **execution_args,
        )
        raise_no_provider_available(provider_result, "dev")
        stdout, stderr, code = provider_result[:3]
        runtime = runtime_metadata(provider_result)
        provider_history = merge_provider_history([provider_result])
        if code != 0:
            return {
                "phase": "fix",
                "loop": loop_n,
                "exit_code": 1,
                "error": f"DEV exited {code}: {(stderr or '')[:200]}",
                "stdout": stdout,
                "execution": runtime,
                "provider_history": provider_history,
            }
        gitops.commit_all(workdir, f"fix: {feature} — round {loop_n}")
        return {
            "phase": "fix",
            "loop": loop_n,
            "exit_code": 0,
            "commit_sha": gitops.head_sha(workdir),
            "stdout": stdout,
            "execution": runtime,
            "provider_history": provider_history,
        }
    except NoProviderAvailable:
        raise
    except Exception as exc:
        return {"phase": "fix", "loop": loop_n, "exit_code": 1, "error": str(exc)}
