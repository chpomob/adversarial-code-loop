"""
BUILD phase: run DEV model, stage and commit all changes.

The DEV model writes files directly into *workdir*; this phase runs it with the
spec on stdin, then stages and commits everything. A zero-change build still
advances history (``commit_all`` forces an empty commit), so the loop branch
always gets a build commit.
"""
from typing import Any

from adversarial_common import gitops

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
    providers: Any,
) -> dict:
    """
    Run the DEV model with the spec as input.

    - ``providers.run_cmd()`` executes *dev_cmd* with the spec on stdin
    - ``gitops.commit_all(workdir, "build: <feature> — <summary>")``

    Returns ``{"phase": "build", "exit_code": 0, "commit_sha": sha_or_None}``.
    On failure returns ``{"phase": "build", "exit_code": 1, "error": "..."}``.
    """
    try:
        prompt = f"Implement the specification:\n\n{spec_text}"
        stdout, stderr, code = providers.run_cmd(
            dev_cmd, stdin_text=prompt, timeout=timeout, cwd=workdir, role="builder",
        )
        if code != 0:
            return {
                "phase": "build",
                "exit_code": 1,
                "error": f"DEV exited {code}: {(stderr or '')[:200]}",
                "stdout": stdout,
            }
        summary = _short_summary(spec_text)
        gitops.commit_all(workdir, f"build: {feature} — {summary}")
        return {
            "phase": "build",
            "exit_code": 0,
            "commit_sha": gitops.head_sha(workdir),
            "stdout": stdout,
        }
    except Exception as exc:  # defensive: never leak an exception to the loop
        return {"phase": "build", "exit_code": 1, "error": str(exc)}
