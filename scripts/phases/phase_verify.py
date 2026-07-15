"""
VERIFY phase: check if findings are resolved.

The verifier is placed in the workdir (checked out at loop branch HEAD) with
access to findings on disk. They can read files directly and run
``git diff <branch-point>..HEAD`` to see the cumulative change. Output is
validated JSON.

``run_verify(findings, diff_text, review_cmd, providers, jsonio, timeout, workdir) -> dict``
"""
import json
from collections.abc import Mapping
from typing import Any

from scripts.phases.runtime import merge_runtime, merge_warnings

__all__ = ["run_verify"]

_VALID_VERDICTS = {"APPROVE", "REJECT"}
_VALID_STATUS = {"resolved", "rejected", "disputed"}


def _validate(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("verdict") not in _VALID_VERDICTS:
        return False
    results = payload.get("results")
    if not isinstance(results, list):
        return results is None or len(results) == 0  # empty = treat as unresolved
    for item in results:
        if not isinstance(item, dict):
            return False
        if item.get("status") not in _VALID_STATUS:
            return False
    return True


def run_verify(
    findings: list,
    diff_text: str,
    review_cmd: str,
    providers: Any,
    jsonio: Any,
    timeout: int = 600,
    workdir: str = "",
    branch_point: str = "",
    execution: Mapping[str, Any] | None = None,
    ledger: Any = None,
) -> dict:
    """
    Run VERIFY model with project access to the loop branch.

    The verifier reads findings from review JSON, explores the code on disk,
    runs ``git diff <branch-point>..HEAD`` to see changes, and outputs per-finding
    status. JSON extraction tries multiple strategies to be model-agnostic.

    Returns ``{"phase": "verify", "results": [...], "verdict": "...",
               "exit_code": 0}``.
    """
    import subprocess
    try:
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, cwd=workdir, timeout=5,
        ).stdout.strip()
    except Exception:
        branch = "(unknown)"

    diff_base = branch_point or "<branch-point>"
    prompt = (
        f"You are verifying code in a git branch checked out at `{branch}`.\n\n"
        "For each finding below, determine whether it is **resolved** (code fixed), "
        "**rejected** (finding was wrong), or **disputed** (unclear).\n\n"
        f"The branch-point SHA for this review is `{diff_base}`.\n"
        "To see the cumulative change since that branch point:\n"
        f"  git diff {diff_base}..HEAD\n\n"
        "To see full files: cat <filepath>\n\n"
        f"Findings:\n{json.dumps(findings, indent=2)}\n\n"
        "Output ONLY valid JSON:\n"
        '{"results": [{"id": "A1", "status": "resolved|rejected|disputed", '
        '"note": "optional"}], "verdict": "APPROVE|REJECT"}'
    )

    runtime_calls = []
    parse_warnings = []

    def _attempt(prompt_text):
        execution_args = dict(execution or {})
        if execution is not None or ledger is not None:
            execution_args["phase"] = "verify"
        if ledger is not None:
            execution_args["ledger"] = ledger
        provider_result = providers.run_cmd(
            review_cmd, stdin_text=prompt_text, role="verifier",
            timeout=timeout, cwd=workdir, **execution_args,
        )
        stdout, stderr, code = provider_result[:3]
        metadata = getattr(provider_result, "metadata", {})
        runtime_calls.append(
            dict(metadata) if isinstance(metadata, Mapping) else {}
        )
        if code != 0:
            return None, f"VERIFY exited {code}: {(stderr or '')[:200]}", stdout
        payload = jsonio.parse_json_output(stdout, warnings=parse_warnings)
        return payload, None, stdout

    try:
        payload, err, stdout = _attempt(prompt)
        if err:
            return {
                "phase": "verify", "exit_code": 1, "error": err,
                "execution": merge_runtime(runtime_calls),
            }
        if not _validate(payload):
            # retry with stricter instruction
            payload, err, stdout = _attempt(
                prompt + "\n\nIMPORTANT: Respond with raw JSON only. "
                "No markdown, no code fences, no explanations."
            )
            if err:
                return {
                    "phase": "verify", "exit_code": 1, "error": err,
                    "execution": merge_runtime(runtime_calls),
                }
            if not _validate(payload):
                return {
                    "phase": "verify", "exit_code": 1,
                    "results": [], "verdict": "UNKNOWN",
                    "error": "invalid JSON after retry", "stdout": stdout,
                    "warnings": parse_warnings,
                    "execution": merge_runtime(runtime_calls),
                }
        return {
            "phase": "verify", "exit_code": 0,
            "results": payload.get("results", []),
            "verdict": payload.get("verdict", "REJECT"),
            "stdout": stdout,
            "warnings": merge_warnings(payload, parse_warnings),
            "execution": merge_runtime(runtime_calls),
        }
    except Exception as exc:
        return {"phase": "verify", "exit_code": 1, "error": str(exc)}
