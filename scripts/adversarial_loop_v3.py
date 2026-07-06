#!/usr/bin/env python3
"""Adversarial Code Loop — Sequential BUILD -> CRITIQUE -> (FIX -> VERIFY)^N -> ARBITER.

Shared engine (subprocess hardening, JSON handling, provider detection,
personas) lives in the adversarial-common sibling skill.

Exit codes: 0=APPROVED, 1=pipeline failure, 3=REJECT, 4=CODE_NEEDS_FIXES,
5=ARBITRATED (indeterminate). The machine-readable contract is final.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'adversarial-common'))
from adversarial_common import persona_path
from adversarial_common.runner import run_cli, fail_phase
from adversarial_common.jsonio import strip_json_wrapper, save_artifact, write_final_json
from adversarial_common.providers import resolve_role_cmd, default_wrapper_cmd, persona_for_role
from adversarial_common.snapshot import snapshot_workdir

# Final verdict -> process exit code (2 is left to argparse usage errors).
VERDICT_EXIT_CODES = {
    "APPROVED": 0,
    "REJECT": 3,
    "CODE_NEEDS_FIXES": 4,
    "ARBITRATED": 5,
}


def step_builder(spec_text, dev_cmd, out_dir, timeout, workdir=None):
    print(f"\n{'='*60}\n  BUILD  (DEV)\n{'='*60}")
    prompt = f"Implement the specification:\n\n{spec_text}"
    stdout, stderr, code = run_cli(dev_cmd, stdin_text=prompt, timeout=timeout,
                                   cwd=workdir, persona_file=persona_path(persona_for_role("builder", dev_cmd)))
    save_artifact(out_dir, "01_code.md", stdout)
    if stderr:
        save_artifact(out_dir, "01_code.err", stderr)
    if code != 0:
        fail_phase("BUILD", code, stderr)
    print(f"  OK Code — {len(stdout)} chars")
    return stdout


def step_critic(code_text, review_cmd, out_dir, timeout, workdir=None):
    print(f"\n{'='*60}\n  CRITIQUE  (REVIEW)\n{'='*60}")
    prompt = f"Review this code:\n\n{code_text}"
    stdout, stderr, code = run_cli(review_cmd, stdin_text=prompt, timeout=timeout,
                                   cwd=workdir, persona_file=persona_path("critic"))
    save_artifact(out_dir, "02_review.json", stdout)
    if stderr:
        save_artifact(out_dir, "02_review.err", stderr)
    if code != 0:
        fail_phase("CRITIQUE", code, stderr)
    try:
        review = json.loads(strip_json_wrapper(stdout))
        verdict = review.get("verdict", "UNKNOWN")
        print(f"  OK {len(review.get('findings', []))} findings — Verdict: {verdict}")
        return stdout, verdict
    except (json.JSONDecodeError, AttributeError):
        print(f"  OK Review ({len(stdout)} chars)")
        return stdout, "UNKNOWN"


def step_fixer(code_text, review_json, dev_cmd, out_dir, loop_n, timeout,
               workdir=None, baseline=None):
    print(f"\n{'='*60}\n  FIX  (loop #{loop_n})\n{'='*60}")
    prompt = f"Original code:\n{code_text}\n\nFindings:\n{review_json}"
    stdout, stderr, code = run_cli(dev_cmd, stdin_text=prompt, timeout=timeout,
                                   cwd=workdir, persona_file=persona_path(persona_for_role("fixer", dev_cmd)))
    save_artifact(out_dir, f"loop_{loop_n}_03_fix.md", stdout)
    if stderr:
        save_artifact(out_dir, f"loop_{loop_n}_03_fix.err", stderr)
    if code != 0:
        fail_phase(f"FIX #{loop_n}", code, stderr)
    try:
        fix = json.loads(strip_json_wrapper(stdout))
        all_fixed = fix.get("all_fixed", False)
        updated_code = fix.get("updated_code", "")
        target_file = fix.get("target_file", "")
        # Don't trust all_fixed if no code was actually returned.
        if all_fixed and not (updated_code or "").strip():
            all_fixed = False
        print(f"  OK all_fixed={all_fixed} updated_code={len(updated_code)}B target={target_file}")
        return stdout, all_fixed, updated_code, target_file
    except (json.JSONDecodeError, AttributeError):
        return _fixer_sandbox_fallback(stdout, out_dir, loop_n, workdir, baseline)


def _fixer_sandbox_fallback(stdout, out_dir, loop_n, workdir, baseline):
    """Codex sandbox modes write directly to the filesystem and return non-JSON
    stdout. Detect changes since the pipeline-start snapshot (not against an
    arbitrary baseline that may include pre-existing dirt) and surface them."""
    print(f"  Fix stdout non-JSON ({len(stdout)} chars) — checking disk changes...")
    all_fixed = False
    updated_code = ""
    target_file = ""
    if workdir and os.path.isdir(os.path.join(workdir, ".git")):
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=10, cwd=workdir,
            )
            current = {line[3:].strip() for line in status.stdout.splitlines() if line.strip()}
            changed = sorted(current - (baseline or set()))
            if changed:
                # First file changed by THIS fixer, any extension.
                target_file = changed[0]
                full_path = os.path.join(workdir, target_file)
                if os.path.isfile(full_path):
                    try:
                        with open(full_path) as fh:
                            updated_code = fh.read()
                    except OSError:
                        updated_code = ""
                # Only claim "all_fixed" if we captured real file content.
                all_fixed = bool(updated_code.strip())
                print(f"  Detected {len(changed)} new file(s). target={target_file} code={len(updated_code)}B")
                diff_text = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, timeout=10, cwd=workdir,
                ).stdout
                save_artifact(out_dir, f"loop_{loop_n}_03_fix.diff", diff_text[:5000])
                if all_fixed:
                    fixed_list = "\n".join(f"  - `{f}`" for f in changed[:20])
                    stdout = json.dumps({
                        "all_fixed": True,
                        "target_file": target_file,
                        "updated_code": updated_code,
                        "summary": f"Fixed {len(changed)} file(s) via sandbox write:\n{fixed_list}\n\nDiff:\n```diff\n{diff_text[:3000]}\n```"
                    }, indent=2)
        except Exception as e:
            print(f"  git diff fallback error: {e}")
    return stdout, all_fixed, updated_code, target_file


def step_verifier(review_original, fix_response, review_cmd, out_dir, loop_n,
                  timeout, workdir=None):
    print(f"\n{'='*60}\n  VERIFY  (loop #{loop_n})\n{'='*60}")
    prompt = f"Original review:\n{review_original}\n\nDeveloper's response:\n{fix_response}"
    stdout, stderr, code = run_cli(review_cmd, stdin_text=prompt, timeout=timeout,
                                   cwd=workdir, persona_file=persona_path("verifier"))
    save_artifact(out_dir, f"loop_{loop_n}_04_verdict.json", stdout)
    if stderr:
        save_artifact(out_dir, f"loop_{loop_n}_04_verdict.err", stderr)
    if code != 0:
        fail_phase(f"VERIFY #{loop_n}", code, stderr)
    try:
        v = json.loads(strip_json_wrapper(stdout))
        verdict = v.get("verdict", "UNKNOWN")
        print(f"  OK Verdict: {verdict}")
        return stdout, verdict
    except (json.JSONDecodeError, AttributeError):
        print(f"  OK ({len(stdout)} chars)")
        return stdout, "UNKNOWN"


def parse_arbiter_verdict(text):
    """Extract the JUDGE's actual verdict from its output.

    Looks for an explicit `VERDICT: <x>` line first, then falls back to keyword
    scanning. Returns one of APPROVED / CODE_NEEDS_FIXES / REJECT / ARBITRATED."""
    if not text:
        return "ARBITRATED"
    m = re.search(r'VERDICT\s*[:=]\s*([A-Z_]+)', text, re.IGNORECASE)
    token = m.group(1).upper() if m else ""
    upper = text.upper()
    if token in ("APPROVED", "APPROVE") or "CODE_APPROVED" in upper:
        return "APPROVED"
    if token == "CODE_NEEDS_FIXES" or "CODE_NEEDS_FIXES" in upper:
        return "CODE_NEEDS_FIXES"
    if token in ("REJECT", "REJECTED") or "REJECT" in upper:
        return "REJECT"
    return "ARBITRATED"


def step_arbiter(history, arbiter_cmd, out_dir, timeout, workdir=None):
    print(f"\n{'='*60}\n  ARBITRATION (JUDGE)\n{'='*60}")
    prompt = f"Case file:\n\n{history}"
    stdout, stderr, code = run_cli(arbiter_cmd, stdin_text=prompt, timeout=timeout,
                                   cwd=workdir, persona_file=persona_path("judge"))
    save_artifact(out_dir, "05_arbitrage.md", stdout)
    if stderr:
        save_artifact(out_dir, "05_arbitrage.err", stderr)
    if code != 0:
        fail_phase("ARBITRATION", code, stderr)
    verdict = parse_arbiter_verdict(stdout)
    print(f"  OK Arbiter verdict: {verdict}")
    return stdout, verdict


def resolve_target_path(workdir, target_file):
    target = Path(target_file)
    if target.is_absolute():
        raise ValueError("target_file must be relative")
    if ".." in target.parts:
        raise ValueError("target_file must not contain '..'")
    resolved = (Path(workdir).resolve() / target).resolve()
    if not resolved.is_relative_to(Path(workdir).resolve()):
        raise ValueError("target_file resolves outside workdir")
    return resolved


def finish(out_dir, verdict, spec_text, code_text, reviews, verdicts, arbiter_text=None):
    """Write final.md + final.json, then exit per VERDICT_EXIT_CODES."""
    lines = ["# Adversarial Code Loop — Final Report", f"Date: {datetime.now().isoformat()}", "",
             "## Summary", f"- **Final verdict**: {verdict}",
             f"- **Cycles**: {len(reviews)}", f"- **Arbitrated**: {'Yes' if arbiter_text else 'No'}", "",
             "## Specification", f"```\n{spec_text}\n```", "", "## Final Code", f"{code_text}", ""]
    save_artifact(out_dir, "final.md", "\n".join(lines))
    write_final_json(out_dir, verdict,
                     verdicts=verdicts,
                     phases=[label for label, _ in reviews],
                     arbitrated=bool(arbiter_text),
                     artifacts_dir=str(out_dir))
    print(f"\n{verdict}")
    sys.exit(VERDICT_EXIT_CODES.get(verdict, 5))


def main():
    p = argparse.ArgumentParser(
        description="Adversarial Code Loop (BUILD -> CRITIQUE -> (FIX -> VERIFY)^N -> ARBITER)")
    default_dev = default_wrapper_cmd("--max-turns 20")
    default_review = "codex exec --skip-git-repo-check --sandbox read-only"
    default_arbiter = default_wrapper_cmd("--max-turns 10")
    p.add_argument("--spec", required=True, help="Specification file to implement")
    p.add_argument("--workdir", default=os.getcwd())
    p.add_argument("--max-loops", type=int, default=3)
    p.add_argument("--dev-cmd", default=None,
                   help=f"BUILDER/FIXER command (default: $ACL_DEV_CMD or '{default_dev}')")
    p.add_argument("--review-cmd", default=None,
                   help=f"CRITIC/VERIFIER command, read-only (default: $ACL_REVIEW_CMD or '{default_review}')")
    p.add_argument("--arbiter-cmd", default=None,
                   help=f"JUDGE command (default: $ACL_ARBITER_CMD or '{default_arbiter}')")
    p.add_argument("--no-arbiter", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--out", default=".adversarial-loop")
    args = p.parse_args()

    dev_cmd = resolve_role_cmd("dev", args.dev_cmd, "ACL_DEV_CMD", default_dev)
    review_cmd = resolve_role_cmd("review", args.review_cmd, "ACL_REVIEW_CMD", default_review)
    arbiter_cmd = resolve_role_cmd("arbiter", args.arbiter_cmd, "ACL_ARBITER_CMD", default_arbiter)

    if not os.path.isfile(args.spec):
        print(f"X Spec not found: {args.spec}")
        sys.exit(1)
    spec_text = Path(args.spec).read_text()
    workdir = args.workdir or os.getcwd()

    out_dir = Path(workdir) / args.out if not os.path.isabs(args.out) else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    save_artifact(out_dir, "00_spec.txt", spec_text)

    # Snapshot dirty files so the FIXER fallback only counts NEW changes.
    baseline = snapshot_workdir(workdir)

    print(f"\n{'#'*60}\n  ADVERSARIAL CODE LOOP\n  Spec: {args.spec}\n  Max loops: {args.max_loops}\n  DEV: {dev_cmd[:60]}\n  REVIEW: {review_cmd[:60]}\n{'#'*60}")

    reviews, verdicts = [], []

    code_text = step_builder(spec_text, dev_cmd, out_dir, args.timeout, workdir=workdir)
    reviews.append(("CODE (BUILDER)", code_text))

    review_text, verdict = step_critic(code_text, review_cmd, out_dir, args.timeout, workdir=workdir)
    reviews.append(("CRITIQUE (CRITIC)", review_text))
    verdicts.append(verdict)
    if verdict == "APPROVE":
        finish(out_dir, "APPROVED", spec_text, code_text, reviews, verdicts)

    for n in range(1, args.max_loops + 1):
        fix_text, all_fixed, updated_code, target_file = step_fixer(
            code_text, review_text, dev_cmd, out_dir, n, args.timeout,
            workdir=workdir, baseline=baseline)
        reviews.append((f"FIX #{n}", fix_text))
        if all_fixed and updated_code.strip() and target_file:
            try:
                tp = resolve_target_path(workdir, target_file)
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_text(updated_code)
                print(f"  -> Wrote {len(updated_code)}B to {target_file}")
                code_text = updated_code
            except (ValueError, TypeError) as e:
                print(f"  X target_file error: {e}")
                sys.exit(1)

        verify_text, verdict = step_verifier(review_text, fix_text, review_cmd,
                                             out_dir, n, args.timeout, workdir=workdir)
        reviews.append((f"VERIFY #{n}", verify_text))
        verdicts.append(verdict)
        if verdict == "APPROVE":
            print(f"\nAPPROVED at cycle #{n}")
            finish(out_dir, "APPROVED", spec_text, code_text, reviews, verdicts)

    if args.no_arbiter:
        print(f"\nREJECTED after {args.max_loops} cycles")
        finish(out_dir, "REJECT", spec_text, code_text, reviews, verdicts)

    history = "\n\n".join(["## Specification", spec_text, "## Code", code_text]
                          + [f"### {s}\n{c}" for s, c in reviews])
    arbiter_text, arbiter_verdict = step_arbiter(history, arbiter_cmd, out_dir,
                                                 args.timeout, workdir=workdir)
    reviews.append(("ARBITRATION", arbiter_text))
    verdicts.append(arbiter_verdict)
    finish(out_dir, arbiter_verdict, spec_text, code_text, reviews, verdicts,
           arbiter_text=arbiter_text)


if __name__ == "__main__":
    main()
