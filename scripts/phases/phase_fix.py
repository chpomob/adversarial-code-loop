"""
FIX phase: present findings to DEV model, commit fixes.

The DEV model receives the findings list and edits files on disk; this phase
then stages and commits the result as a new fix round.
"""
import json
from typing import Any

from adversarial_common import gitops

__all__ = ["run_fix"]


def run_fix(
    findings: list,
    dev_cmd: str,
    workdir: str,
    timeout: int,
    feature: str,
    loop_n: int,
    providers: Any,
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
        stdout, stderr, code = providers.run_cmd(
            dev_cmd, stdin_text=prompt, timeout=timeout, cwd=workdir, role="fixer",
        )
        if code != 0:
            return {
                "phase": "fix",
                "loop": loop_n,
                "exit_code": 1,
                "error": f"DEV exited {code}: {(stderr or '')[:200]}",
                "stdout": stdout,
            }
        gitops.commit_all(workdir, f"fix: {feature} — round {loop_n}")
        return {
            "phase": "fix",
            "loop": loop_n,
            "exit_code": 0,
            "commit_sha": gitops.head_sha(workdir),
            "stdout": stdout,
        }
    except Exception as exc:
        return {"phase": "fix", "loop": loop_n, "exit_code": 1, "error": str(exc)}
