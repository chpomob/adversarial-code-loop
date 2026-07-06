"""
GIT phase: orchestrate git setup (PHASE 0) and finalization (MERGE / REJECT).

Thin composition over :mod:`adversarial_common.gitops` so the orchestrator has
two clean entry points instead of a dozen git calls. All paths are passed
explicitly; no globals.
"""
from typing import Any

from adversarial_common import gitops

__all__ = ["setup_git", "finalize_git"]


def setup_git(
    workdir: str,
    feature: str,
    parent_branch: str,
) -> dict:
    """
    Full PHASE 0:

    - ensure_git_available
    - detect_enclosing_repo or auto_init
    - stash_dirty
    - create_loop_branch (+ checkout)
    - record_branch_point
    - ensure_gitignore

    Returns ``{"phase": "git_setup", "branch": "...", "branch_point": "...",
               "stash_id": "...", "exit_code": 0}``.
    """
    try:
        ok, info = gitops.ensure_git_available()
        if not ok:
            return {"phase": "git_setup", "exit_code": 1, "error": info}

        if not gitops.detect_enclosing_repo(workdir):
            gitops.auto_init(workdir)
        else:
            # Pre-existing repo: bootstrap identity if none is configured so
            # BUILD/FIX commits don't fail with "tell me who you are" (F6).
            gitops.ensure_git_identity(workdir)

        stash_id = gitops.stash_dirty(workdir)
        branch = gitops.create_loop_branch(workdir, feature, parent_branch)
        gitops.checkout(workdir, branch)
        branch_point = gitops.record_branch_point(workdir, parent_branch)
        gitops.ensure_gitignore(workdir, ".adversarial-loop/")

        return {
            "phase": "git_setup",
            "exit_code": 0,
            "branch": branch,
            "branch_point": branch_point,
            "stash_id": stash_id,
        }
    except Exception as exc:
        return {"phase": "git_setup", "exit_code": 1, "error": str(exc)}


def finalize_git(
    workdir: str,
    feature: str,
    parent_branch: str,
    verdict: str,
    evidence_path: str,
    no_merge: bool = False,
) -> dict:
    """
    On APPROVE: tag_with_evidence (best-effort), squash_merge into parent,
    delete loop branch — unless ``no_merge`` leaves the branch in place.

    On REJECT: reject_marker commit, do NOT merge.

    Returns ``{"phase": "git_finalize", "verdict": "...", "exit_code": 0}``.
    """
    try:
        branch = gitops.get_current_branch(workdir)

        if verdict == "APPROVE":
            # ponytail: evidence tag is best-effort — a missing/unreadable file
            # must not block the merge that already succeeded.
            if evidence_path:
                try:
                    gitops.tag_with_evidence(workdir, f"{branch}-approved", evidence_path)
                except Exception:
                    pass
            if no_merge:
                return {
                    "phase": "git_finalize",
                    "verdict": verdict,
                    "exit_code": 0,
                    "merged": False,
                }
            gitops.squash_merge(
                workdir, branch, parent_branch,
                f"squash: {feature} — adversarial approved",
            )
            return {
                "phase": "git_finalize",
                "verdict": verdict,
                "exit_code": 0,
                "merged": True,
            }

        # REJECT (or any non-APPROVE verdict): marker commit, no merge.
        gitops.reject_marker(workdir, f"{feature} — {verdict}")
        return {
            "phase": "git_finalize",
            "verdict": verdict,
            "exit_code": 0,
            "merged": False,
        }
    except Exception as exc:
        return {
            "phase": "git_finalize",
            "verdict": verdict,
            "exit_code": 1,
            "error": str(exc),
        }
