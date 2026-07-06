"""Self-check for the v4 phase modules.

Runs the full build -> review -> fix -> verify -> finalize flow (and the
arbiter) against a throwaway git repo using a stub ``providers``/``jsonio`` so
no real model is invoked. Run with: ``python3 scripts/phases/test_phases.py``
or ``pytest scripts/phases/test_phases.py``.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Self-contained path bootstrap: adversarial-common is a sibling skill and the
# skill root must be on sys.path so ``scripts.phases`` is importable directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)
_COMMON = os.path.abspath(os.path.join(_SKILL_ROOT, os.pardir, "adversarial-common"))
if os.path.isdir(_COMMON) and _COMMON not in sys.path:
    sys.path.insert(0, _COMMON)

from adversarial_common import gitops, jsonio  # noqa: E402

from scripts.phases.phase_build import run_build  # noqa: E402
from scripts.phases.phase_review import run_review  # noqa: E402
from scripts.phases.phase_fix import run_fix  # noqa: E402
from scripts.phases.phase_verify import run_verify  # noqa: E402
from scripts.phases.phase_arbiter import run_arbiter  # noqa: E402
from scripts.phases.phase_git import setup_git, finalize_git  # noqa: E402


class StubProviders:
    """Stand-in for adversarial_common.providers: writes canned files/JSON."""

    def __init__(self, workdir):
        self.workdir = workdir
        self.roles = []

    def run_cmd(self, cmd, stdin_text=None, timeout=600, cwd=None, role=None,
                project=None):
        self.roles.append(role)
        if role == "builder":
            with open(os.path.join(self.workdir, "app.txt"), "w") as fh:
                fh.write("v1\n")
            return "built app.txt", "", 0
        if role == "fixer":
            with open(os.path.join(self.workdir, "app.txt"), "a") as fh:
                fh.write("fixed\n")
            return "fixed", "", 0
        if role == "critic":
            return (json.dumps({
                "findings": [{
                    "id": "A1", "severity": "major", "file": "app.txt",
                    "line": 1, "summary": "needs fixing", "evidence": "trivial",
                }],
                "verdict": "REQUEST_CHANGES",
            }), "", 0)
        if role == "verifier":
            return (json.dumps({
                "results": [{"id": "A1", "status": "resolved"}],
                "verdict": "APPROVE",
            }), "", 0)
        if role == "judge":
            return (json.dumps({
                "verdict": "APPROVE", "conditions": ["keep tests green"],
            }), "", 0)
        return "", "", 0


def _git(workdir, *args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          cwd=workdir)


def _run_full_flow():
    tmp = tempfile.mkdtemp(prefix="acl-phases-")
    try:
        providers = StubProviders(tmp)
        gitops.auto_init(tmp)  # creates main + identity

        setup = setup_git(tmp, "demo feature", "main")
        assert setup["exit_code"] == 0, setup
        assert setup["branch"] == "loop/demo-feature/1"
        assert setup["branch_point"]
        assert setup["stash_id"] == ""  # clean tree

        build = run_build("Demo spec\nsecond line", "fake-dev", tmp, 60,
                          "demo-feature", providers)
        assert build["exit_code"] == 0, build
        assert build["commit_sha"]
        diff = gitops.get_diff(tmp, setup["branch_point"])
        assert "app.txt" in diff

        review = run_review(diff, "fake-review", providers, jsonio)
        assert review["exit_code"] == 0, review
        assert review["verdict"] == "REQUEST_CHANGES"
        assert review["findings"][0]["id"] == "A1"

        fix = run_fix(review["findings"], "fake-dev", tmp, 60, "demo-feature",
                      1, providers)
        assert fix["exit_code"] == 0, fix
        assert fix["loop"] == 1 and fix["commit_sha"]

        diff2 = gitops.get_diff(tmp, setup["branch_point"])
        verify = run_verify(review["findings"], diff2, "fake-review",
                            providers, jsonio)
        assert verify["exit_code"] == 0, verify
        assert verify["verdict"] == "APPROVE"
        assert verify["results"][0]["status"] == "resolved"

        # evidence artifact for the tag
        evidence = os.path.join(tmp, "final.json")
        with open(evidence, "w") as fh:
            json.dump({"verdict": "APPROVE"}, fh)

        final = finalize_git(tmp, "demo-feature", "main", "APPROVE", evidence)
        assert final["exit_code"] == 0, final
        assert final["merged"] is True

        # loop branch gone, change landed on main
        assert not gitops.branch_exists(tmp, "loop/demo-feature/1")
        with open(os.path.join(tmp, "app.txt")) as fh:
            assert "fixed" in fh.read()

        return tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_reject_flow():
    tmp = tempfile.mkdtemp(prefix="acl-phases-rej-")
    try:
        providers = StubProviders(tmp)
        gitops.auto_init(tmp)
        setup_git(tmp, "rej", "main")
        run_build("rej spec", "fake-dev", tmp, 60, "rej", providers)
        final = finalize_git(tmp, "rej", "main", "REJECT", "")
        assert final["exit_code"] == 0 and final["merged"] is False, final
        msg = _git(tmp, "log", "-1", "--pretty=%s").stdout.strip()
        assert msg.startswith("[REJECTED]"), msg
        # loop branch still exists (not merged)
        assert gitops.branch_exists(tmp, "loop/rej/1")
        return tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_arbiter():
    tmp = tempfile.mkdtemp(prefix="acl-phases-arb-")
    try:
        providers = StubProviders(tmp)
        arb = run_arbiter([{"id": "A1", "status": "disputed"}],
                          "fake-dev", "fake-review", "fake-judge", providers)
        assert arb["exit_code"] == 0, arb
        assert arb["verdict"] == "APPROVE"
        assert arb["conditions"] == ["keep tests green"]
        return tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_retry_on_bad_json():
    """review must retry once when the first JSON is malformed."""
    tmp = tempfile.mkdtemp(prefix="acl-phases-bad-")
    try:
        calls = {"n": 0}

        class BadProviders:
            def run_cmd(self, cmd, stdin_text=None, timeout=600, cwd=None,
                        role=None, project=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return "this is not json at all", "", 0
                return (json.dumps({
                    "findings": [{
                        "id": "A1", "severity": "minor", "file": "f",
                        "line": 1, "summary": "s", "evidence": "e",
                    }],
                    "verdict": "APPROVE",
                }), "", 0)

        out = run_review("diff", "fake", BadProviders(), jsonio)
        assert out["exit_code"] == 0, out
        assert calls["n"] == 2  # one retry
        return tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v4_phases():
    """pytest entry point — exercises every phase end to end."""
    _run_full_flow()
    _run_reject_flow()
    _run_arbiter()
    _run_retry_on_bad_json()


def main():
    test_v4_phases()
    print("OK: all v4 phase modules pass")


if __name__ == "__main__":
    main()
