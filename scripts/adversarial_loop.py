#!/usr/bin/env python3
"""Adversarial Code Loop v4 — git-native orchestrator.

BUILD -> REVIEW -> (FIX -> VERIFY)^N -> ARBITER, on a dedicated loop branch.

Phase logic lives in scripts/phases/*; the shared engine (providers, jsonio,
gitops) lives in the adversarial-common sibling skill. This file only wires
phases together, persists state.json for --resume, and maps verdicts to exit
codes. v3 is preserved as adversarial_loop_v3.py.

Exit codes:
  0 APPROVED    — squash-merged into the parent branch
  1 infrastructure failure (phase crash, git error, interrupt)
  2 usage error (bad flags, missing spec)
  3 REJECT      — findings unresolved after max-loops, or a gate failed
  4 ARBITRATED  — approved by the arbiter; conditions recorded in final.json

The machine-readable contract is <out>/<feature>/final.json.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
# skill root (for `scripts.phases.*`) and the adversarial-common sibling skill
# (for `adversarial_common.*`) must both be importable.
sys.path.insert(0, str(_SCRIPTS_DIR.parent))
sys.path.insert(0, str(_SCRIPTS_DIR.parent.parent / "adversarial-common"))

from adversarial_common import gitops, jsonio, providers
from adversarial_common.providers import resolve_role_cmd
from scripts.phases import (
    phase_arbiter,
    phase_build,
    phase_fix,
    phase_git,
    phase_review,
    phase_verify,
)

EXIT_APPROVED = 0
EXIT_INFRA = 1
EXIT_USAGE = 2
EXIT_REJECTED = 3
EXIT_ARBITRATED = 4

# DEV must write files into the worktree; REVIEW must not.
DEFAULT_DEV_CMD = "codex exec --skip-git-repo-check --sandbox workspace-write"
DEFAULT_REVIEW_CMD = "pi --provider zai --model glm-5.2"

# Verifier statuses that no longer block approval: "resolved" (fixed) and
# "rejected" (the verifier showed the original finding was wrong).
_SETTLED_STATUSES = {"resolved", "rejected"}


# --- small JSON/state helpers -----------------------------------------------

def _write_json(out_dir, name, payload):
    """Persist *payload* as a pretty-printed JSON artifact under *out_dir*."""
    jsonio.save_artifact(out_dir, name, json.dumps(payload, indent=2) + "\n")


def _read_json(path):
    """Load JSON from *path*; None on any read/parse failure (defensive)."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _mark(state, out_dir, phase, loop=None, findings=None):
    """Record *phase* as completed and flush state.json for --resume."""
    state["phase"] = phase
    if loop is not None:
        state["loop"] = loop
    if findings is not None:
        state["findings"] = findings
    completed = state.setdefault("completed", [])
    if phase not in completed:
        completed.append(phase)
    _write_json(out_dir, "state.json", state)


def _banner(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


# --- gates & helpers ----------------------------------------------------------

def _kill_gate_group(proc):
    """SIGKILL the gate's whole process group (shell=True spawns children)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        proc.kill()


def _run_gate(name, cmd, workdir, timeout):
    """Run a shell build/test gate in its own process group.

    Returns a dict with 'exit_code'. On timeout the whole group is reaped so
    orphaned grandchildren can't hold the stdout/stderr pipes open and
    deadlock communicate() (F3). 'infra' is True when the gate could not run
    at all (bad command / timeout) — distinct from a genuine gate failure.
    """
    _banner(f"GATE  ({name})")
    print(f"  $ {cmd}")
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return {"gate": name, "cmd": cmd, "exit_code": 126,
                "infra": True, "error": f"could not start gate: {exc}"}
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_gate_group(proc)
        proc.wait()
        return {"gate": name, "cmd": cmd, "exit_code": 124,
                "infra": True, "error": f"gate timed out after {timeout}s"}
    status = "OK" if proc.returncode == 0 else f"FAILED ({proc.returncode})"
    print(f"  {status}")
    return {
        "gate": name,
        "cmd": cmd,
        "exit_code": proc.returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }


def _ensure_ids(findings):
    """Guarantee every finding has a unique, non-empty string id (in place).

    Verify keys off ids; an LLM omitting or duplicating them must not let
    _unresolved() collapse distinct findings onto one key (F1). IDs are
    positional (stable across resume since the findings order is persisted
    in state.json), so two findings never share an id.
    """
    seen = set()
    for i, f in enumerate(findings, 1):
        fid = str(f.get("id") or "").strip() or f"finding-{i}"
        while fid in seen:
            fid = f"{fid}-{i}"
        f["id"] = fid
        seen.add(fid)
    return findings


def _unresolved(findings, results):
    """Findings whose verify status is neither resolved nor rejected.

    Results without an id never mark a finding settled: a None key must not
    collide (F1). Findings are guaranteed an id at intake via _ensure_ids.
    """
    settled = {
        r.get("id") for r in results
        if r.get("id") is not None and r.get("status") in _SETTLED_STATUSES
    }
    return [f for f in findings if f.get("id") not in settled]


def _phase_failed(label, result, state, out_dir):
    """Log a phase failure into state.json and stdout. Returns EXIT_INFRA."""
    state["error"] = f"{label}: {result.get('error', 'unknown error')}"
    _mark(state, out_dir, f"{label}_failed")
    print(f"X {label} failed: {result.get('error', 'unknown error')}")
    return EXIT_INFRA


def _restore(workdir, state, out_dir):
    """Best-effort cleanup on every exit path: back to parent, pop stash."""
    parent = state.get("parent_branch", "")
    try:
        if parent and gitops.get_current_branch(workdir) != parent:
            gitops.checkout(workdir, parent)
    except gitops.GitError as exc:
        # Never unstash onto the wrong branch.
        print(f"! could not restore branch {parent!r}: {exc}")
        return
    stash_id = state.get("stash_id", "")
    if stash_id:
        try:
            gitops.unstash(workdir, stash_id)
            state["stash_id"] = ""
            if out_dir is not None:
                _write_json(out_dir, "state.json", state)
        except gitops.GitError as exc:
            print(f"! could not pop {stash_id}: {exc}")


def _final_md(verdict, feature, loops, reason, conditions):
    """Human-readable final report; also the annotation for the evidence tag."""
    lines = [
        f"# Adversarial Loop — {feature}",
        "",
        f"- Verdict: {verdict}",
        f"- Fix/verify loops: {loops}",
        f"- Finished: {datetime.now(timezone.utc).isoformat()}",
    ]
    if reason:
        lines.append(f"- Reason: {reason}")
    if conditions:
        lines.append("- Conditions:")
        lines.extend(f"  - {c}" for c in conditions)
    return "\n".join(lines) + "\n"


def _finish(args, workdir, feature, out_dir, state, verdict,
            reason="", loops=0, conditions=None, arbitrated=False):
    """Finalize git, write final.json/final.md, return the process exit code."""
    conditions = conditions or []
    jsonio.save_artifact(
        out_dir, "final.md",
        _final_md(verdict, feature, loops, reason, conditions))
    git_verdict = "APPROVE" if verdict in ("APPROVED", "ARBITRATED") else "REJECT"
    fin = phase_git.finalize_git(
        workdir, feature, state["parent_branch"], git_verdict,
        str(Path(out_dir) / "final.md"), no_merge=args.no_merge,
    )
    jsonio.write_final_json(
        out_dir, verdict,
        reason=reason,
        loops=loops,
        branch=state.get("branch", ""),
        merged=fin.get("merged", False),
        conditions=conditions,
        arbitrated=arbitrated,
        artifacts_dir=str(out_dir),
    )
    if fin["exit_code"] != 0:
        # Do NOT mark 'done': leave the run resumable so finalize is retried (F4).
        # But preserve the logical verdict in the exit code so a finalize
        # failure on an already-decided run (e.g. merge conflict during squash)
        # is NOT indistinguishable from a genuine pre-verdict infra crash (F3).
        # final.json already records merged:false for the caller to inspect.
        state["error"] = f"git finalize: {fin.get('error', 'unknown error')}"
        state["verdict"] = verdict
        _write_json(out_dir, "state.json", state)
        print(f"X git finalize failed ({verdict}): "
              f"{fin.get('error', 'unknown error')}")
        if verdict == "APPROVED":
            return EXIT_APPROVED
        if verdict == "ARBITRATED":
            return EXIT_ARBITRATED
        return EXIT_REJECTED
    state["verdict"] = verdict
    _mark(state, out_dir, "done")
    print(f"\n{verdict}" + (f" — {reason}" if reason else ""))
    if verdict == "APPROVED":
        return EXIT_APPROVED
    if verdict == "ARBITRATED":
        return EXIT_ARBITRATED
    return EXIT_REJECTED


# --- pipeline -----------------------------------------------------------------

def _pipeline(args, dev_cmd, review_cmd, arbiter_cmd,
              workdir, feature, out_dir, state):
    """Run (or resume) the full v4 workflow. Returns the process exit code."""
    completed = state.setdefault("completed", [])

    # A finished run must not be re-entered: on APPROVED the loop branch was
    # squash-merged and deleted, so replaying the resume checkout would fail (F7).
    if "done" in completed:
        print(f"Run already finished ({state.get('verdict', 'unknown')}) — "
              f"nothing to resume.")
        return int(state.get("exit_code", EXIT_INFRA))

    # PHASE 0 — git setup (branch, stash, branch-point).
    if "git_setup" in completed:
        _banner(f"RESUME  (phase={state.get('phase')}, loop={state.get('loop', 0)})")
        gitops.checkout(workdir, state["branch"])
    else:
        # Parent branch: current branch, or 'main' when auto_init will create
        # the repo (gitops.auto_init pins the initial branch to main).
        if gitops.detect_enclosing_repo(workdir):
            parent_branch = gitops.get_current_branch(workdir)
        else:
            parent_branch = "main"
        setup = phase_git.setup_git(workdir, feature, parent_branch)
        if setup["exit_code"] != 0:
            print(f"X git setup failed: {setup.get('error', 'unknown error')}")
            return EXIT_INFRA
        state.update({
            "parent_branch": parent_branch,
            "branch": setup["branch"],
            "branch_point": setup["branch_point"],
            "stash_id": setup["stash_id"],
            "loop": 0,
            "findings": [],
        })
        _mark(state, out_dir, "git_setup")
        _banner(f"LOOP BRANCH  {setup['branch']}  (from {parent_branch})")

    branch_point = state["branch_point"]
    try:
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"X could not read spec {args.spec}: {exc}")
        return EXIT_USAGE
    jsonio.save_artifact(out_dir, "00_spec.txt", spec_text)

    # BUILD
    if "build" not in completed:
        _banner("BUILD  (DEV)")
        result = phase_build.run_build(
            spec_text, dev_cmd, workdir, args.timeout, feature, providers)
        _write_json(out_dir, "01_build.json", result)
        if result["exit_code"] != 0:
            return _phase_failed("build", result, state, out_dir)
        _mark(state, out_dir, "build")
        print(f"  OK commit {result.get('commit_sha', '')[:12]}")

    # Optional build gate — a failing build is a hard REJECT (exit 3), but a
    # gate that could not run at all (bad command, timeout) is infra (exit 1).
    # The gate is gated on its own 'build_gate' marker (not 'build') so a gate
    # that fails mid-way stays re-runnable on --resume instead of being skipped
    # because 'build' was already marked complete above (F1).
    if args.build_cmd and "build_gate" not in completed:
        gate = _run_gate("build", args.build_cmd, workdir, args.timeout)
        _write_json(out_dir, "01_build_gate.json", gate)
        if gate.get("infra"):
            return _phase_failed("build_gate", gate, state, out_dir)
        if gate["exit_code"] != 0:
            return _finish(args, workdir, feature, out_dir, state,
                           "REJECT", reason="BUILD_FAILED")
        _mark(state, out_dir, "build_gate")

    # REVIEW
    if "review" in completed:
        review = _read_json(Path(out_dir) / "02_review.json") or {}
        findings = _ensure_ids(state.get("findings", review.get("findings", [])))
        review_verdict = review.get(
            "verdict", "REQUEST_CHANGES" if findings else "APPROVE")
    else:
        diff = gitops.get_diff(workdir, branch_point)
        _banner("REVIEW  (CRITIC)")
        if not diff.strip():
            # DEV produced no content changes (BUILD only forced an empty
            # commit). Nothing to review: treat as a clean APPROVE per the
            # spec ("force empty commit if nothing changed") instead of REJECT,
            # so a no-op build exits 0 even when .gitignore already covered
            # .adversarial-loop/ (F2).
            print("  empty diff — nothing to review")
            review = {"findings": [], "verdict": "APPROVE", "exit_code": 0}
        else:
            review = phase_review.run_review(diff, review_cmd, providers, jsonio, workdir=workdir)
            if review["exit_code"] != 0:
                return _phase_failed("review", review, state, out_dir)
        _write_json(out_dir, "02_review.json", review)
        findings = _ensure_ids(review.get("findings", []))
        review_verdict = review.get("verdict", "APPROVE")
        _mark(state, out_dir, "review", findings=findings)
        print(f"  OK {len(findings)} findings — verdict {review_verdict}")

    # FIX / VERIFY loop. The critic's verdict gates the clean-review shortcut:
    # an empty findings list only approves when the verdict is also APPROVE (F2).
    approved = not findings and review_verdict == "APPROVE"
    arbitrated = False
    conditions = []
    loops_run = state.get("loop", 0)

    for n in range(1, args.max_loops + 1):
        if approved:
            break
        loops_run = n

        if f"fix_{n}" not in completed:
            _banner(f"FIX  (round {n}/{args.max_loops})")
            fix = phase_fix.run_fix(
                findings, dev_cmd, workdir, args.timeout, feature, n, providers)
            _write_json(out_dir, f"03_fix_{n}.json", fix)
            if fix["exit_code"] != 0:
                return _phase_failed(f"fix_{n}", fix, state, out_dir)
            _mark(state, out_dir, f"fix_{n}", loop=n)

        if f"verify_{n}" in completed:
            verify = _read_json(Path(out_dir) / f"04_verdict_{n}.json") or {}
        else:
            _banner(f"VERIFY  (round {n}/{args.max_loops})")
            diff = gitops.get_diff(workdir, branch_point)
            verify = phase_verify.run_verify(
                findings, diff, review_cmd, providers, jsonio)
            _write_json(out_dir, f"04_verdict_{n}.json", verify)
            if verify["exit_code"] != 0:
                return _phase_failed(f"verify_{n}", verify, state, out_dir)

        results = verify.get("results", [])
        verdict = verify.get("verdict", "REJECT")
        all_settled = bool(results) and not _unresolved(findings, results)
        print(f"  Verdict {verdict} — "
              f"{len(findings) - len(_unresolved(findings, results))}"
              f"/{len(findings)} settled")

        if verdict == "APPROVE" and all_settled:
            approved = True
            _mark(state, out_dir, f"verify_{n}", loop=n)
            break

        # Narrow to the still-open findings for the next round; if the verifier
        # rejected overall while marking everything settled (contradiction),
        # keep the current list so the arbiter sees real content.
        remaining = _unresolved(findings, results)
        if remaining:
            findings = remaining
        _mark(state, out_dir, f"verify_{n}", loop=n, findings=findings)

        if n < args.max_loops:
            continue

        # Last round still rejected: arbiter (if configured) has the final say.
        if arbiter_cmd and not args.no_arbiter:
            _banner("ARBITER  (JUDGE)")
            arb = phase_arbiter.run_arbiter(
                findings, dev_cmd, review_cmd, arbiter_cmd, providers)
            _write_json(out_dir, "05_arbiter.json", arb)
            if arb["exit_code"] != 0:
                return _phase_failed("arbiter", arb, state, out_dir)
            _mark(state, out_dir, "arbiter")
            if arb["verdict"] == "APPROVE":
                approved = True
                arbitrated = True
                conditions = arb.get("conditions", [])

    reason = ""
    if not approved:
        reason = f"findings unresolved after {args.max_loops} loops"

    # Optional test gate — flips an approval into a rejection; a gate that
    # could not run at all is an infrastructure error, not a test failure.
    if approved and args.test_cmd:
        gate = _run_gate("test", args.test_cmd, workdir, args.timeout)
        _write_json(out_dir, "06_test_gate.json", gate)
        if gate.get("infra"):
            return _phase_failed("test_gate", gate, state, out_dir)
        if gate["exit_code"] != 0:
            approved = False
            arbitrated = False
            reason = "TEST_FAILED"

    if approved:
        verdict = "ARBITRATED" if arbitrated else "APPROVED"
        return _finish(args, workdir, feature, out_dir, state, verdict,
                       loops=loops_run, conditions=conditions,
                       arbitrated=arbitrated)
    return _finish(args, workdir, feature, out_dir, state, "REJECT",
                   reason=reason, loops=loops_run)


# --- CLI ----------------------------------------------------------------------

def _positive_int(value):
    """argparse type: strictly positive integer (rejects 0 and negatives, F8)."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}")
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {value!r}")
    return ivalue


def build_parser():
    p = argparse.ArgumentParser(
        description="Adversarial Code Loop v4 "
                    "(BUILD -> REVIEW -> (FIX -> VERIFY)^N -> ARBITER, git-native)")
    p.add_argument("--spec", required=True, help="Specification file to implement")
    p.add_argument("--workdir", default=".", help="Project directory (default: .)")
    p.add_argument("--dev-cmd", default=None,
                   help=f"BUILDER/FIXER command (default: $ACL_DEV_CMD or '{DEFAULT_DEV_CMD}')")
    p.add_argument("--review-cmd", default=None,
                   help=f"CRITIC/VERIFIER command (default: $ACL_REVIEW_CMD or '{DEFAULT_REVIEW_CMD}')")
    p.add_argument("--arbiter-cmd", default=None,
                   help="JUDGE command (optional; default: $ACL_ARBITER_CMD; unset = no arbiter)")
    p.add_argument("--max-loops", type=_positive_int, default=3)
    p.add_argument("--no-arbiter", action="store_true", help="Skip the arbiter")
    p.add_argument("--timeout", type=_positive_int, default=600,
                   help="Per-subprocess timeout (s)")
    p.add_argument("--build-cmd", default=None, help="Optional build gate (shell)")
    p.add_argument("--test-cmd", default=None, help="Optional test gate (shell)")
    p.add_argument("--no-merge", action="store_true",
                   help="On approval, leave the loop branch unmerged")
    p.add_argument("--feature", default=None,
                   help="Branch/artifact name (default: spec filename)")
    p.add_argument("--out", default=".adversarial-loop", help="Artifacts directory")
    p.add_argument("--resume", action="store_true", help="Resume from state.json")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.spec):
        print(f"X Spec not found: {args.spec}")
        return EXIT_USAGE
    workdir = str(Path(args.workdir).resolve())
    if not os.path.isdir(workdir):
        print(f"X Workdir not found: {args.workdir}")
        return EXIT_USAGE

    ok, info = gitops.ensure_git_available()
    if not ok:
        print(f"X {info}")
        return EXIT_INFRA

    dev_cmd = resolve_role_cmd("dev", args.dev_cmd, "ACL_DEV_CMD", DEFAULT_DEV_CMD)
    review_cmd = resolve_role_cmd(
        "review", args.review_cmd, "ACL_REVIEW_CMD", DEFAULT_REVIEW_CMD)
    # The arbiter is optional: no flag and no env var means "no arbiter".
    arbiter_cmd = (args.arbiter_cmd or os.environ.get("ACL_ARBITER_CMD") or "").strip()

    feature = gitops.sanitize_feature_name(args.feature or Path(args.spec).stem)
    if not feature:
        print("X Could not derive a feature name; pass --feature")
        return EXIT_USAGE

    out_base = Path(args.out)
    if not out_base.is_absolute():
        out_base = Path(workdir) / out_base
    out_dir = out_base / feature
    out_dir.mkdir(parents=True, exist_ok=True)

    state = {}
    if args.resume:
        saved = _read_json(out_dir / "state.json")
        if saved and saved.get("branch"):
            state = saved
        else:
            print("! No resumable state.json found — starting fresh")

    print(f"\n{'#' * 60}\n  ADVERSARIAL CODE LOOP v4\n"
          f"  Spec: {args.spec}\n  Feature: {feature}\n"
          f"  Max loops: {args.max_loops}\n"
          f"  DEV: {dev_cmd[:60]}\n  REVIEW: {review_cmd[:60]}\n{'#' * 60}")

    try:
        code = _pipeline(args, dev_cmd, review_cmd, arbiter_cmd,
                         workdir, feature, out_dir, state)
    except KeyboardInterrupt:
        print("\nX Interrupted — restoring workdir "
              "(loop branch kept; rerun with --resume to continue)")
        state["phase"] = "interrupted"
        _write_json(out_dir, "state.json", state)
        code = EXIT_INFRA
    except gitops.GitError as exc:
        print(f"\nX git error: {exc}")
        state["error"] = str(exc)
        _write_json(out_dir, "state.json", state)
        code = EXIT_INFRA
    finally:
        _restore(workdir, state, out_dir)

    return code


if __name__ == "__main__":
    sys.exit(main())
