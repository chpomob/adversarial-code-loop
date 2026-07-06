"""Regression self-checks for the review fixes (F1, F6, F8).

Pure-logic checks for the non-trivial fixes that don't need a live model.
Run: ``python3 scripts/test_loop_fixes.py`` (or pytest).
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(_HERE)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)
_COMMON = os.path.abspath(os.path.join(_SKILL_ROOT, os.pardir, "adversarial-common"))
if os.path.isdir(_COMMON) and _COMMON not in sys.path:
    sys.path.insert(0, _COMMON)

import argparse  # noqa: E402

from adversarial_common import gitops  # noqa: E402
from adversarial_loop import _ensure_ids, _unresolved, _positive_int  # noqa: E402


def test_f1_ids_unique_and_no_collapse():
    """F1: duplicate/missing ids must not let _unresolved collapse findings."""
    # Two findings with identical descriptions and no id -> distinct ids.
    fs = [{"summary": "fix this"}, {"summary": "fix this"}]
    _ensure_ids(fs)
    assert fs[0]["id"] != fs[1]["id"], fs
    # Explicit duplicate ids are disambiguated.
    fs = [{"id": "X"}, {"id": "X"}]
    _ensure_ids(fs)
    assert fs[0]["id"] != fs[1]["id"], fs
    # Resolving one finding never settles a different one by shared id.
    findings = _ensure_ids([{"summary": "a"}, {"summary": "b"}])
    results = [{"id": findings[0]["id"], "status": "resolved"}]
    assert _unresolved(findings, results) == [findings[1]], _unresolved(findings, results)
    # A result with no id must not mark anything settled.
    assert _unresolved(findings, [{"status": "resolved"}]) == findings


def test_f8_positive_int():
    """F8: --max-loops/--timeout reject 0, negatives, and non-ints."""
    assert _positive_int("3") == 3
    for bad in ("0", "-1", "x", ""):
        try:
            _positive_int(bad)
        except argparse.ArgumentTypeError:
            continue
        raise AssertionError(f"_positive_int accepted {bad!r}")


def test_f6_identity_bootstrapped_when_unset():
    """F6: ensure_git_identity sets repo-local identity when none is configured."""
    tmp = tempfile.mkdtemp(prefix="acl-identity-")
    gitops._run(tmp, ["init"])
    # Isolate config so no global/system identity is visible -> the unset path.
    env = dict(os.environ)
    saved = {k: env.get(k) for k in ("HOME", "GIT_CONFIG_NOSYSTEM",
                                     "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
    try:
        env["HOME"] = tmp
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env.pop("GIT_CONFIG_GLOBAL", None)
        env.pop("GIT_CONFIG_SYSTEM", None)
        os.environ.clear()
        os.environ.update(env)
        name_before, _err, rc = gitops._run(tmp, ["config", "user.name"])
        assert rc != 0 or not name_before.strip(), name_before
        gitops.ensure_git_identity(tmp)
        name, _err, _rc = gitops._run(tmp, ["config", "--local", "user.name"])
        email, _err, _rc = gitops._run(tmp, ["config", "--local", "user.email"])
        assert name.strip() == "adversarial-loop", name
        assert email.strip() == "loop@adversarial.local", email
        # Idempotent: existing local identity is never overridden.
        gitops._run(tmp, ["config", "--local", "user.name", "keep-me"])
        gitops.ensure_git_identity(tmp)
        name2, _e, _r = gitops._run(tmp, ["config", "--local", "user.name"])
        assert name2.strip() == "keep-me", name2
    finally:
        os.environ.clear()
        os.environ.update(env)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_f1_ids_unique_and_no_collapse()
    test_f8_positive_int()
    test_f6_identity_bootstrapped_when_unset()
    print("OK: loop fix regressions pass (F1, F6, F8)")


if __name__ == "__main__":
    main()
