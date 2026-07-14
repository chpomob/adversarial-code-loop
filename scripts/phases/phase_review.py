"""
REVIEW phase: run REVIEW model with project access to the loop branch.

The reviewer checks the code on disk (checked out at loop branch HEAD).
They can read files directly and run ``git diff <branch-point>..HEAD`` to see
the cumulative change. Output is validated JSON findings.

``run_review(diff_text, review_cmd, providers, jsonio, workdir) -> dict``
"""
import json
from typing import Any

__all__ = ["run_review"]

_VALID_VERDICTS = {"REQUEST_CHANGES", "APPROVE", "REJECT"}
_REQUIRED_FINDING_KEYS = {"id", "severity", "file", "line", "summary", "evidence"}


def _valid_line(line: Any) -> bool:
    return isinstance(line, int) or (isinstance(line, str) and line.isdigit())


def _validate(payload: Any) -> bool:
    """Lightweight v4 schema check. No jsonschema dependency."""
    if not isinstance(payload, dict):
        return False
    if payload.get("verdict") not in _VALID_VERDICTS:
        return False
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        if not isinstance(finding, dict):
            return False
        if not _REQUIRED_FINDING_KEYS.issubset(finding.keys()):
            return False
        if not _valid_line(finding.get("line")):
            return False
    return True


def _build_prompt(diff_text: str, workdir: str, branch_point: str = "") -> str:
    """Build a prompt that tells the reviewer to explore the code on disk.

    The reviewer is in `workdir` (checked out at loop branch HEAD). They can:
    - Read any file from disk
    - Run ``git diff <branch-point>..HEAD`` to see the cumulative change
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
    return (
        f"You are reviewing code in a git branch. The working directory is "
        f"checked out at `{branch}` (the latest commit to review).\n\n"
        f"The branch-point SHA for this review is `{diff_base}`.\n"
        f"To see the cumulative change since that branch point, run:\n"
        f"  git diff {diff_base}..HEAD   — line-by-line diff\n\n"
        f"To see the full context of a file, read it from disk or use:\n"
        f"  cat <filepath>\n\n"
        f"Review the cumulative changes since `{diff_base}`. Each finding must "
        f"reference a real file and line visible in that diff. Do NOT report "
        f"pre-existing issues outside this branch's change.\n\n"
        f"Output ONLY valid JSON:\n"
        f'{{"findings": [{{"id": "A1", "severity": "blocker|major|minor|nit", '
        f'"file": "path", "line": 42, "summary": "...", '
        f'"evidence": "..."}}], '
        f'"verdict": "REQUEST_CHANGES|APPROVE|REJECT"}}'
    )


def run_review(
    diff_text: str,
    review_cmd: str,
    providers: Any,
    jsonio: Any,
    workdir: str = "",
    timeout: int = 600,
    branch_point: str = "",
) -> dict:
    """
    Run REVIEW model with project access to the loop branch.

    The reviewer reads files directly from disk and runs git diff to see
    what changed since the branch point. Output JSON is validated against the
    v4 schema. Retries once on invalid JSON.

    Returns ``{"phase": "review", "findings": [...], "verdict": "...",
               "exit_code": 0}``.
    """
    prompt = _build_prompt(diff_text, workdir, branch_point)

    def _attempt(prompt_text):
        stdout, stderr, code = providers.run_cmd(
            review_cmd, stdin_text=prompt_text, role="critic",
            timeout=timeout, cwd=workdir,
        )
        if code != 0:
            return None, f"REVIEW exited {code}: {(stderr or '')[:200]}", stdout
        try:
            payload = json.loads(jsonio.strip_json_wrapper(stdout))
        except (json.JSONDecodeError, ValueError, TypeError):
            payload = None
        return payload, None, stdout

    try:
        payload, err, stdout = _attempt(prompt)
        if err:
            return {"phase": "review", "exit_code": 1, "error": err}
        if not _validate(payload):
            payload, err, stdout = _attempt(
                prompt + (
                    "\n\nIMPORTANT: Your response was not valid JSON. "
                    "Respond with ONLY valid JSON matching the schema."
                )
            )
            if err:
                return {"phase": "review", "exit_code": 1, "error": err}
            if not _validate(payload):
                return {
                    "phase": "review", "exit_code": 1,
                    "findings": [], "verdict": "UNKNOWN",
                    "error": "invalid JSON after retry", "stdout": stdout,
                }
        return {
            "phase": "review", "exit_code": 0,
            "findings": payload["findings"], "verdict": payload["verdict"],
            "stdout": stdout,
        }
    except Exception as exc:
        return {"phase": "review", "exit_code": 1, "error": str(exc)}
