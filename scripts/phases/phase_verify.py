"""
VERIFY phase: check if findings are resolved.

The verifier is placed in the workdir (checked out at loop branch HEAD) with
access to findings on disk. They can read files directly and run
``git diff HEAD~1..HEAD`` to see what changed. Output is validated JSON.

``run_verify(findings, diff_text, review_cmd, providers, jsonio) -> dict``
"""
import json, re
from typing import Any

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


def _try_parse_json(text: str) -> dict | None:
    """Parse JSON from model output, trying multiple extraction strategies.
    
    1. strip_json_wrapper (removes ```json...``` markdown)
    2. Find first { and last } in the text
    3. Find first [ and last ] for JSON arrays
    """
    import ast  # safe literal eval as last resort
    text = text.strip()
    
    # Strategy 1: markdown wrapper
    if text.startswith("```"):
        lines = text.splitlines()
        cleaned = []
        for line in lines:
            if line.strip().startswith("```"):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned).strip()
    
    # Strategy 2: find JSON object
    for strategy_name, parse_fn in [
        ("json.loads", lambda t: json.loads(t)),
        ("extract { }", lambda t: json.loads(t[t.find("{"):t.rfind("}")+1]) if "{" in t else None),
        ("extract [ ]", lambda t: json.loads(t[t.find("["):t.rfind("]")+1]) if "[" in t else None),
    ]:
        try:
            result = parse_fn(text)
            if result is not None:
                return result
        except (json.JSONDecodeError, ValueError, IndexError):
            continue
    
    return None


def run_verify(
    findings: list,
    diff_text: str,
    review_cmd: str,
    providers: Any,
    jsonio: Any,
) -> dict:
    """
    Run VERIFY model with project access to the loop branch.

    The verifier reads findings from review JSON, explores the code on disk,
    runs ``git diff HEAD~1..HEAD`` to see changes, and outputs per-finding
    status. JSON extraction tries multiple strategies to be model-agnostic.

    Returns ``{"phase": "verify", "results": [...], "verdict": "...",
               "exit_code": 0}``.
    """
    import subprocess
    try:
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        branch = "(unknown)"

    prompt = (
        f"You are verifying code in a git branch checked out at `{branch}`.\n\n"
        "For each finding below, determine whether it is **resolved** (code fixed), "
        "**rejected** (finding was wrong), or **disputed** (unclear).\n\n"
        "To see what changed:\n"
        "  git diff HEAD~1..HEAD\n"
        "  git log -1 -p\n\n"
        "To see full files: cat <filepath>\n\n"
        f"Findings:\n{json.dumps(findings, indent=2)}\n\n"
        "Output ONLY valid JSON:\n"
        '{"results": [{"id": "A1", "status": "resolved|rejected|disputed", '
        '"note": "optional"}], "verdict": "APPROVE|REJECT"}'
    )

    def _attempt(prompt_text):
        stdout, stderr, code = providers.run_cmd(
            review_cmd, stdin_text=prompt_text, role="verifier",
        )
        if code != 0:
            return None, f"VERIFY exited {code}: {(stderr or '')[:200]}", stdout
        payload = _try_parse_json(stdout)
        return payload, None, stdout

    try:
        payload, err, stdout = _attempt(prompt)
        if err:
            return {"phase": "verify", "exit_code": 1, "error": err}
        if not _validate(payload):
            # retry with stricter instruction
            payload, err, stdout = _attempt(
                prompt + "\n\nIMPORTANT: Respond with raw JSON only. "
                "No markdown, no code fences, no explanations."
            )
            if err:
                return {"phase": "verify", "exit_code": 1, "error": err}
            if not _validate(payload):
                return {
                    "phase": "verify", "exit_code": 1,
                    "results": [], "verdict": "UNKNOWN",
                    "error": "invalid JSON after retry", "stdout": stdout,
                }
        return {
            "phase": "verify", "exit_code": 0,
            "results": payload.get("results", []),
            "verdict": payload.get("verdict", "REJECT"),
            "stdout": stdout,
        }
    except Exception as exc:
        return {"phase": "verify", "exit_code": 1, "error": str(exc)}
