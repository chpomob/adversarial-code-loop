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
  5 context blocked before any provider or git mutation

The machine-readable contract is <out>/<feature>/final.json.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
# skill root (for `scripts.phases.*`) and the adversarial-common sibling skill
# (for `adversarial_common.*`) must both be importable.
sys.path.insert(0, str(_SCRIPTS_DIR.parent))
sys.path.insert(0, str(_SCRIPTS_DIR.parent.parent / "adversarial-common"))

from adversarial_common import (
    CostLedger,
    NoProviderAvailable,
    ProviderConfigError,
    QuotaResolver,
    gates,
    gitops,
    jsonio,
    load_provider_config,
    providers,
    runner,
)
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
EXIT_CONTEXT_BLOCKED = runner.CI_EXIT_CONTEXT_BLOCKED

# DEV must write files into the worktree; REVIEW must not.
DEFAULT_DEV_CMD = "codex exec --skip-git-repo-check --sandbox workspace-write"
DEFAULT_REVIEW_CMD = "pi --provider zai --model glm-5.2"

# Verifier statuses that no longer block approval: "resolved" (fixed) and
# "rejected" (the verifier showed the original finding was wrong).
_SETTLED_STATUSES = {"resolved", "rejected"}

_EXIT_BY_VERDICT = {"APPROVED": EXIT_APPROVED, "ARBITRATED": EXIT_ARBITRATED}

_THRESHOLD_ENV = {
    "min_chars": (
        "ACL_MIN_CONTEXT_CHARS", "ACL_CONTEXT_MIN_CHARS",
        "ADVERSARIAL_MIN_CHARS",
    ),
    "min_tokens": (
        "ACL_MIN_CONTEXT_TOKENS", "ACL_CONTEXT_MIN_TOKENS",
        "ADVERSARIAL_MIN_TOKENS",
    ),
}
_GATE_FINDING_ID = "GATE-VERIFICATION"
_FORCE_PROVIDER_ROLES = frozenset({"dev", "review", "verify", "arbiter"})


# --- small JSON/state helpers -----------------------------------------------

def _write_json(out_dir, name, payload):
    """Persist *payload* as a pretty-printed JSON artifact under *out_dir*."""
    jsonio.save_artifact(out_dir, name, json.dumps(payload, indent=2) + "\n")


def _read_json(path):
    """Load JSON from *path*; None on any read/parse failure (defensive)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
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


def _ensure_ids(findings):
    """Guarantee every finding has a unique, non-empty string id (in place).

    The verify gate keys off ids; an LLM omitting or duplicating them must
    not let _unresolved() collapse distinct findings onto one key.
    """
    seen = set()
    for i, f in enumerate(findings, 1):
        fid = str(f.get("id") or "").strip() or f"finding-{i}"
        while fid in seen:
            fid = f"{fid}-{i}"
        f["id"] = fid
        seen.add(fid)
    return findings


def _threshold_overrides(args):
    """Resolve context thresholds with CLI > loop env > shared env precedence."""
    overrides = {}
    for name, env_names in _THRESHOLD_ENV.items():
        value = getattr(args, name, None)
        if value is None:
            for env_name in env_names:
                raw = os.environ.get(env_name)
                if raw is None:
                    continue
                try:
                    value = int(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"${env_name} must be a non-negative integer"
                    ) from exc
                if value < 0:
                    raise ValueError(
                        f"${env_name} must be a non-negative integer"
                    )
                break
        if value is not None:
            overrides[name] = value
    return overrides


def _execution_settings(args):
    """Return runner settings shared by every model phase."""
    return {
        "max_retries": getattr(args, "max_retries", 3),
        "max_input_chars": getattr(
            args, "max_input_chars", runner.DEFAULT_MAX_INPUT_CHARS
        ),
        "max_output_chars": getattr(
            args, "max_output_chars", runner.DEFAULT_MAX_OUTPUT_CHARS
        ),
        "truncate_input": getattr(args, "truncate_input", False),
    }


def _execution_record(args):
    """Return serializable effective execution controls for artifacts."""
    return {
        **_execution_settings(args),
        "show_costs": getattr(args, "show_costs", False),
        "max_agents": getattr(args, "max_agents", 6),
    }


def _preflight(args, spec_text, out_dir):
    """Run R1/R3/R5 before command resolution or any git operation."""
    capped, truncated = gates.enforce_input_cap(
        spec_text, getattr(args, "max_input_chars", runner.DEFAULT_MAX_INPUT_CHARS)
    )
    cap_events = []
    if truncated:
        cap_events.append({
            "kind": "input",
            "phase": "preflight",
            "limit": getattr(args, "max_input_chars", runner.DEFAULT_MAX_INPUT_CHARS),
            "original_chars": len(spec_text),
            "truncated": bool(getattr(args, "truncate_input", False)),
        })
    effective_text = capped if getattr(args, "truncate_input", False) else spec_text
    context = gates.check_context("input", effective_text, _threshold_overrides(args))
    if truncated and not getattr(args, "truncate_input", False) and context["ok"]:
        context = dict(context)
        context.update({
            "ok": False,
            "reason": "input_exceeds_max_chars",
            "max_input_chars": getattr(args, "max_input_chars", 0),
            "input_chars": len(spec_text),
        })
    complexity = gates.estimate_complexity(
        effective_text, max_agents=getattr(args, "max_agents", 6)
    )
    args._context = context
    args._complexity = complexity
    args._preflight_cap_events = cap_events
    if context["ok"]:
        return effective_text, True

    jsonio.write_final_json(
        out_dir, "CONTEXT_BLOCKED",
        status="blocked",
        context_blocked=True,
        reason=context["reason"],
        context=context,
        thresholds=context.get("thresholds", {}),
        complexity=complexity,
        execution=_execution_record(args),
        attempts=[],
        cap_events=cap_events,
        calls=[],
        costs=CostLedger().summary(),
        gates=[],
        findings=[],
        epistemic_labels=jsonio.epistemic_distribution([]),
        warnings=[],
    )
    print(f"X context blocked: {context['reason']}", file=sys.stderr)
    return effective_text, False


def _restore_ledger(state):
    """Rebuild recorded usage on resume so costs are neither lost nor duplicated."""
    ledger = CostLedger()
    costs = state.get("costs", {})
    records = costs.get("records", []) if isinstance(costs, dict) else []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            record_args = {
                "phase": str(record.get("phase", "")),
                "persona": str(record.get("persona", "")),
            }
            if record.get("estimated", False):
                record_args.update({
                    "prompt_text": _estimated_token_text(
                        record.get("prompt_tokens", 0)
                    ),
                    "completion_text": _estimated_token_text(
                        record.get("completion_tokens", 0)
                    ),
                })
            else:
                record_args["usage"] = {
                    "prompt_tokens": record.get("prompt_tokens", 0),
                    "completion_tokens": record.get("completion_tokens", 0),
                }
            ledger.record(record.get("model"), **record_args)
        except (TypeError, ValueError):
            continue
    return ledger


def _estimated_token_text(token_count):
    """Return minimal text whose char/4 estimate equals *token_count*."""
    if (
        isinstance(token_count, bool)
        or not isinstance(token_count, int)
        or token_count < 0
    ):
        raise ValueError("estimated token count must be a non-negative integer")
    return "" if token_count == 0 else " " * (token_count * 4 - 3)


def _record_phase(state, label, result, ledger):
    """Attach bounded runner evidence and the current ledger to resumable state."""
    runtime = result.get("execution", {}) if isinstance(result, dict) else {}
    if not isinstance(runtime, dict):
        runtime = {}
    call = {
        "label": label,
        "ok": bool(isinstance(result, dict) and result.get("exit_code") == 0),
        "attempts": list(runtime.get("attempts", [])),
        "cap_events": list(runtime.get("cap_events", [])),
    }
    state.setdefault("calls", []).append(call)
    state.setdefault("attempts", []).extend(
        {"phase": label, **attempt} for attempt in call["attempts"]
    )
    state.setdefault("cap_events", []).extend(
        {"phase": label, **event} for event in call["cap_events"]
    )
    state["costs"] = ledger.summary()
    history = result.get("provider_history", []) if isinstance(result, dict) else []
    for decision in history:
        if isinstance(decision, dict):
            state.setdefault("provider_history", []).append(dict(decision))
    for warning in result.get("warnings", []) if isinstance(result, dict) else []:
        if warning not in state.setdefault("warnings", []):
            state["warnings"].append(warning)
    if isinstance(result, dict):
        distribution = result.get("epistemic_distribution")
        if not isinstance(distribution, dict):
            distribution = result.get("epistemic_labels")
        if isinstance(distribution, dict):
            state["epistemic_labels"] = distribution


def _provider_call_args(args, role, explicit_cmd):
    """Return the registry controls shared by every invocation of *role*."""
    forced = getattr(args, "_force_providers", {})
    return {
        "explicit_cmd": explicit_cmd,
        "force": bool(getattr(args, "force", False)),
        "force_provider": forced.get(role),
    }


def _provider_history_from_group_results(group_results, phase):
    """Flatten concurrent phase decisions in stable group order."""
    history = []
    for index in sorted(group_results):
        result = group_results[index].get(phase) or {}
        for decision in result.get("provider_history", []):
            if isinstance(decision, dict):
                history.append(dict(decision))
    return history


def _normalize_findings(findings, state=None):
    """Normalize R8 labels without dropping or replacing finding identity."""
    payload = {"findings": findings}
    warnings = []
    jsonio.normalize_findings(payload, warnings=warnings)
    if state is not None:
        for warning in warnings:
            if warning not in state.setdefault("warnings", []):
                state["warnings"].append(warning)
    return findings


def _gate_finding(gate, stage, loop_n=0):
    """Convert objective verification evidence into one stable FIX finding."""
    return {
        "id": _GATE_FINDING_ID,
        "severity": "blocker",
        "file": "(verification gate)",
        "line": 1,
        "summary": f"{stage.replace('_', ' ').title()} verification failed",
        "evidence": (
            f"Command: {gate.get('command', '')}\n"
            f"Exit: {gate.get('exit_code')}\n"
            f"Log: {gate.get('log', '')}"
        ),
        "confidence": "high",
        "basis": "code",
        "origin": "verification_gate",
        "gate_stage": stage,
        "fix_round": loop_n,
        "gate": gate,
    }


def _replace_gate_finding(findings, gate_finding):
    return [
        finding for finding in findings
        if finding.get("id") != _GATE_FINDING_ID
    ] + [gate_finding]


def _without_gate_findings(findings):
    """Discard synthetic gate evidence after objective verification passes."""
    return [finding for finding in findings if not finding.get("gate")]


# --- partition & concurrent batches (R7) -------------------------------------

def _partition_findings_by_file(findings):
    """Partition *findings* into file-disjoint groups.

    Two findings that share a filename land in the same group; findings
    that touch disjoint files are placed in independent groups that can
    be fixed concurrently without conflicts.
    """
    # Union-find: two findings are linked if they share a file.
    parent = list(range(len(findings)))

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    file_to_indices = {}
    for i, f in enumerate(findings):
        name = (f.get("file") or "").strip()
        indices = file_to_indices.setdefault(name, [])
        if indices:
            _union(indices[0], i)
        indices.append(i)

    root_groups = {}
    for i, f in enumerate(findings):
        root_groups.setdefault(_find(i), []).append(f)

    return list(root_groups.values())


def _run_concurrent_fix_verify_round(
    findings, dev_cmd, review_cmd, workdir, feature, loop_n,
    timeout, execution, ledger, verification_cmd,
    branch_point, max_workers, out_dir, state, args,
):
    """Run one FIX-to-VERIFY round concurrently in isolated worktrees.

    Findings are partitioned into file-disjoint groups. Each group runs
    its own FIX then VERIFY in a dedicated worktree. Successful worktree
    commits are cherry-picked back into the main worktree in order. All
    worktrees are cleaned up on any exit path.
    """
    groups = _partition_findings_by_file(findings)
    workers = min(len(groups), max_workers)

    wt_base = Path(out_dir) / "worktrees"
    # Clean up any stale worktrees from a prior interrupted run.
    if wt_base.exists():
        shutil.rmtree(str(wt_base), ignore_errors=True)
    wt_base.mkdir(parents=True, exist_ok=True)

    lock = threading.Lock()
    group_results = {}     # group_index -> result dict
    worktree_infos = []    # [(wt_path, branch_name)]

    def _process_group(idx, group):
        wt_path = wt_base / f"wt-{idx}"
        branch = f"loop/{feature}/fix-{loop_n}/g{idx}"

        try:
            gitops.create_worktree(workdir, str(wt_path), "HEAD", branch)
            with lock:
                worktree_infos.append((str(wt_path), branch))

            fix = phase_fix.run_fix(
                group, dev_cmd, str(wt_path), timeout,
                feature, loop_n, getattr(args, "_provider_resolver", None),
                **_provider_call_args(args, "dev", getattr(args, "dev_cmd", None)),
                execution=execution, ledger=ledger,
            )
            # Record per-group fix artifact
            _write_json(out_dir, f"03_fix_{loop_n}_g{idx}.json", fix)

            if fix.get("exit_code") != 0:
                return idx, {"fix": fix, "verify": None,
                             "group_size": len(group),
                             "error": fix.get("error", "FIX failed")}

            wt_diff = gitops.get_diff(str(wt_path), branch_point)
            verify = phase_verify.run_verify(
                group, wt_diff, review_cmd,
                getattr(args, "_provider_resolver", None), jsonio,
                timeout=timeout, workdir=str(wt_path),
                branch_point=branch_point,
                **_provider_call_args(
                    args, "verify", getattr(args, "review_cmd", None)
                ),
                execution=execution, ledger=ledger,
            )
            _write_json(out_dir, f"04_verdict_{loop_n}_g{idx}.json", verify)

            return idx, {"fix": fix, "verify": verify,
                         "group_size": len(group), "branch": branch}
        except NoProviderAvailable:
            raise
        except Exception as exc:
            return idx, {"error": str(exc), "group_size": len(group)}

    _banner(
        f"FIX+VERIFY (round {loop_n}/{args.max_loops}) "
        f"— {workers} worker(s), {len(groups)} group(s)"
    )

    try:
        # Run all groups concurrently (capped at max_workers).
        provider_error = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_process_group, i, g): i for i, g in enumerate(groups)}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    idx, result = fut.result()
                except NoProviderAvailable as exc:
                    if provider_error is None:
                        provider_error = exc
                    continue
                with lock:
                    group_results[idx] = result

        # Cherry-pick successful worktree commits back in order.
        for idx in sorted(group_results):
            result = group_results[idx]
            branch = result.get("branch")
            if not branch or result.get("error"):
                continue
            try:
                gitops.checkout(workdir, state["branch"])
                gitops.cherry_pick(workdir, branch)
            except gitops.GitError as exc:
                result["merge_error"] = str(exc)

        if provider_error is not None:
            raise provider_error
    finally:
        # ponytail: cleanup on any exit path (success, failure, interrupt).
        # Keep this outside the try body so an aggregate block is one shot.
        for wt_path, branch in worktree_infos:
            try:
                gitops.remove_worktree(workdir, str(wt_path))
            except Exception:
                pass
            try:
                gitops.delete_branch(workdir, branch)
            except Exception:
                pass
        try:
            shutil.rmtree(str(wt_base), ignore_errors=True)
        except Exception:
            pass

    # --- Aggregate results ---
    all_verify_results = []
    group_fix_errors = []

    for idx in sorted(group_results):
        result = group_results[idx]
        fix = result.get("fix") or {}
        if fix.get("exit_code") != 0 or result.get("error"):
            group_fix_errors.append(idx)
        verify = result.get("verify") or {}
        all_verify_results.extend(verify.get("results", []))
        # Findings in groups that failed fix or merge are marked disputed.
        if result.get("error") or result.get("merge_error"):
            reason = result.get("error") or result.get("merge_error", "unknown")
            for f in groups[idx]:
                all_verify_results.append({
                    "id": f["id"], "status": "disputed",
                    "evidence": f"FIX group failed: {reason[:200]}",
                    "confidence": "medium", "basis": "inference",
                })

    # Combined fix artifact
    fix_exit = 0 if not group_fix_errors else 1
    combined_fix = {
        "phase": "fix", "loop": loop_n, "exit_code": fix_exit,
        "groups": len(groups), "group_results": [
            group_results[i].get("fix", {}) for i in sorted(group_results)
        ],
        "group_errors": group_fix_errors,
        "concurrent": True, "workers": workers,
        "provider_history": _provider_history_from_group_results(
            group_results, "fix"
        ),
    }

    # Combined verify artifact
    all_verdict = "APPROVE" if (
        all_verify_results and
        all(r.get("status") in _SETTLED_STATUSES for r in all_verify_results)
    ) else "REJECT"
    combined_verify = {
        "phase": "verify", "exit_code": 0,
        "results": all_verify_results,
        "verdict": all_verdict,
        "groups": len(groups),
        "concurrent": True, "workers": workers,
        "provider_history": _provider_history_from_group_results(
            group_results, "verify"
        ),
    }

    return combined_fix, combined_verify


# --- gates & helpers ----------------------------------------------------------


def _terminate_provider_processes():
    """Best-effort reap of provider-spawned children before touching git.

    The subprocesses belong to adversarial_common.providers; call its cleanup
    hook when one is exposed so _restore() never checkouts/unstashes while a
    DEV/REVIEW child is still writing into the worktree.
    """
    for name in ("terminate_active", "kill_active_processes", "shutdown"):
        fn = getattr(providers, name, None)
        if callable(fn):
            try:
                fn()
            except Exception as exc:  # cleanup must never mask the interrupt
                print(f"! provider cleanup ({name}) failed: {exc}")
            return


def _unresolved(findings, results):
    """Findings whose verify status is neither resolved nor rejected.

    Results without an id are ignored: a None key must never mark findings
    settled (every finding is guaranteed an id by _ensure_ids).
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
    # ponytail: purge orphaned worktrees from prior interrupted concurrent runs.
    wt_base = Path(out_dir) / "worktrees"
    if wt_base.exists():
        try:
            shutil.rmtree(str(wt_base), ignore_errors=True)
        except Exception:
            pass

    parent = state.get("parent_branch", "")
    try:
        if parent and gitops.get_current_branch(workdir) != parent:
            gitops.checkout(workdir, parent)
    except gitops.GitError as exc:
        # Never unstash onto the wrong branch.
        print(f"! could not restore branch {parent!r}: {exc}")
        stash_id = state.get("stash_id", "")
        if stash_id:
            print(f"! stashed changes NOT restored — recover manually with: "
                  f"git stash pop {stash_id}")
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
    findings = _normalize_findings(list(state.get("findings", [])), state)
    distribution = state.get("epistemic_labels")
    if not isinstance(distribution, dict):
        distribution = jsonio.epistemic_distribution(findings)
    ledger = getattr(args, "_ledger", None)
    costs = ledger.summary() if ledger is not None else state.get("costs", {})
    final_extra = {
        "reason": reason,
        "loops": loops,
        "branch": state.get("branch", ""),
        "merged": fin.get("merged", False),
        "conditions": conditions,
        "arbitrated": arbitrated,
        "artifacts_dir": str(out_dir),
        "context": state.get("context", getattr(args, "_context", {})),
        "thresholds": state.get("thresholds", {}),
        "execution": state.get("execution", _execution_record(args)),
        "attempts": state.get("attempts", []),
        "cap_events": state.get("cap_events", []),
        "calls": state.get("calls", []),
        "costs": costs,
        "gates": state.get("gates", []),
        "complexity": state.get("complexity", {}),
        "findings": findings,
        "epistemic_labels": distribution,
        "epistemic_distribution": distribution,
        "warnings": state.get("warnings", []),
    }
    if fin.get("error"):
        final_extra["error"] = f"git finalize failed: {fin['error']}"
    final_payload = runner.ensure_final_payload(
        verdict=verdict,
        provider_history=state.get("provider_history", []),
        **final_extra,
    )
    final_payload.pop("verdict", None)
    jsonio.write_final_json(out_dir, verdict, **final_payload)
    if fin["exit_code"] != 0:
        # Do NOT mark 'done': leave the run resumable so finalize is retried.
        state["error"] = f"git finalize: {fin.get('error', 'unknown error')}"
        _write_json(out_dir, "state.json", state)
        print(f"X git finalize failed: {fin.get('error', 'unknown error')}")
        return EXIT_INFRA
    code = _EXIT_BY_VERDICT.get(verdict, EXIT_REJECTED)
    state["verdict"] = verdict
    state["exit_code"] = code
    _mark(state, out_dir, "done")
    print(f"\n{verdict}" + (f" — {reason}" if reason else ""))
    return code


# --- pipeline -----------------------------------------------------------------

def _pipeline(args, dev_cmd, review_cmd, arbiter_cmd,
              workdir, feature, out_dir, state):
    """Run (or resume) the full v4 workflow. Returns the process exit code."""
    completed = state.setdefault("completed", [])
    ledger = getattr(args, "_ledger", None) or CostLedger()
    args._ledger = ledger
    execution = _execution_settings(args)

    # A finished run must not be re-entered: on APPROVED the loop branch was
    # squash-merged and deleted, so replaying the resume checkout would fail.
    if "done" in completed:
        print(f"Run already finished ({state.get('verdict', 'unknown')}) — "
              f"nothing to resume.")
        return int(state.get("exit_code", EXIT_INFRA))

    # PHASE 0 — git setup. R1/R3 preflight has already completed in main().
    if "git_setup" in completed:
        _banner(f"RESUME  (phase={state.get('phase')}, loop={state.get('loop', 0)})")
        gitops.checkout(workdir, state["branch"])
    else:
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
    spec_text = getattr(args, "_spec_text", None)
    if spec_text is None:
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    jsonio.save_artifact(out_dir, "00_spec.txt", spec_text)
    build_verification_cmd = args.build_cmd
    fix_verification_cmd = args.test_cmd or args.build_cmd

    # Validate the project and configured command before the first model call.
    if "pre_build_gate" in completed:
        pre_gate = _read_json(Path(out_dir) / "00_pre_build_gate.json") or {}
    else:
        pre_gate = gates.pre_build_gate(workdir, build_verification_cmd)
        _write_json(out_dir, "00_pre_build_gate.json", pre_gate)
        state.setdefault("gates", []).append(pre_gate)
        if not pre_gate.get("ok", False):
            if pre_gate.get("infra"):
                return _phase_failed("pre_build_gate", pre_gate, state, out_dir)
            return _finish(
                args, workdir, feature, out_dir, state, "REJECT",
                reason="PRE_BUILD_GATE_FAILED",
            )
        _mark(state, out_dir, "pre_build_gate")

    if "build" not in completed:
        _banner("BUILD  (DEV)")
        result = phase_build.run_build(
            spec_text, dev_cmd, workdir, args.timeout, feature,
            getattr(args, "_provider_resolver", None),
            **_provider_call_args(args, "dev", getattr(args, "dev_cmd", None)),
            execution=execution, ledger=ledger,
        )
        _record_phase(state, "build", result, ledger)
        _write_json(out_dir, "01_build.json", result)
        if result["exit_code"] != 0:
            return _phase_failed("build", result, state, out_dir)
        _mark(state, out_dir, "build")
        print(f"  OK commit {result.get('commit_sha', '')[:12]}")

    # A post-build failure is actionable evidence, not a terminal rejection.
    post_build_failed = False
    if build_verification_cmd:
        if "post_build_gate" in completed:
            build_gate = _read_json(
                Path(out_dir) / "01_post_build_gate.json"
            ) or {}
        else:
            build_gate = gates.post_build_gate(
                workdir, build_verification_cmd, timeout=args.timeout
            )
            _write_json(out_dir, "01_post_build_gate.json", build_gate)
            state.setdefault("gates", []).append(build_gate)
            if build_gate.get("infra"):
                return _phase_failed(
                    "post_build_gate", build_gate, state, out_dir
                )
            _mark(state, out_dir, "post_build_gate")
        post_build_failed = not build_gate.get("ok", False)

    # REVIEW runs only after an objectively verified build. A failed build gate
    # becomes a normalized synthetic finding consumed by the first FIX round.
    if "review" in completed:
        review = _read_json(Path(out_dir) / "02_review.json") or {}
        findings = _ensure_ids(
            state.get("findings", review.get("findings", []))
        )
        _normalize_findings(findings, state)
        review_verdict = review.get(
            "verdict", "REQUEST_CHANGES" if findings else "APPROVE"
        )
    elif post_build_failed:
        findings = [_gate_finding(build_gate, "post_build")]
        review_verdict = "REQUEST_CHANGES"
        review = {
            "phase": "review",
            "exit_code": 0,
            "findings": findings,
            "verdict": review_verdict,
            "source": "post_build_gate",
            "warnings": [],
            "epistemic_labels": jsonio.epistemic_distribution(findings),
        }
        _write_json(out_dir, "02_review.json", review)
        _mark(state, out_dir, "review", findings=findings)
        print("  ! post-build verification failed — routing evidence to FIX")
    else:
        diff = gitops.get_diff(workdir, branch_point)
        if not diff.strip():
            return _finish(
                args, workdir, feature, out_dir, state,
                "REJECT", reason="EMPTY_DIFF",
            )
        _banner("REVIEW  (CRITIC)")
        review = phase_review.run_review(
            diff, review_cmd, getattr(args, "_provider_resolver", None), jsonio,
            workdir=workdir,
            branch_point=branch_point, execution=execution, ledger=ledger,
            **_provider_call_args(
                args, "review", getattr(args, "review_cmd", None)
            ),
        )
        _record_phase(state, "review", review, ledger)
        _write_json(out_dir, "02_review.json", review)
        if review["exit_code"] != 0:
            return _phase_failed("review", review, state, out_dir)
        findings = _ensure_ids(review["findings"])
        _normalize_findings(findings, state)
        review_verdict = review.get(
            "verdict", "REQUEST_CHANGES" if findings else "APPROVE"
        )
        _mark(state, out_dir, "review", findings=findings)
        print(f"  OK {len(findings)} findings — verdict {review_verdict}")

    approved = not findings and review_verdict == "APPROVE"
    arbitrated = False
    conditions = []
    loops_run = state.get("loop", 0)

    # A review can approve without entering FIX.  An explicitly configured
    # test gate still has to run once against that approved build.
    if approved and args.test_cmd:
        gate_name = "post_review_test_gate"
        gate_path = Path(out_dir) / "02_post_review_test_gate.json"
        if gate_name in completed:
            final_gate = _read_json(gate_path) or {}
        else:
            final_gate = gates.post_fix_gate(
                workdir, args.test_cmd, timeout=args.timeout
            )
            _write_json(out_dir, gate_path.name, final_gate)
            state.setdefault("gates", []).append(final_gate)
            if final_gate.get("infra"):
                return _phase_failed(gate_name, final_gate, state, out_dir)
            _mark(state, out_dir, gate_name)
        if not final_gate.get("ok", False):
            findings = [_gate_finding(final_gate, "post_review")]
            approved = False

    # R5 complexity cap for concurrent workers.
    complexity = state.get("complexity", {})
    max_concurrent = complexity.get(
        "recommended_agents", getattr(args, "max_agents", 6)
    )

    for n in range(1, args.max_loops + 1):
        if approved or not findings:
            break
        loops_run = n
        fix_path = Path(out_dir) / f"03_fix_{n}.json"

        # --- Determine whether to run concurrent batches ---
        groups = _partition_findings_by_file(findings)
        use_concurrent = len(groups) > 1 and not any(
            finding.get("gate") for finding in findings
        ) and not (
            f"fix_{n}" in completed or f"verify_{n}" in completed
        )

        if use_concurrent:
            # --- Concurrent FIX+VERIFY round ---
            fix, verify = _run_concurrent_fix_verify_round(
                findings, dev_cmd, review_cmd, workdir, feature, n,
                args.timeout, execution, ledger,
                fix_verification_cmd, branch_point,
                max_concurrent, out_dir, state, args,
            )
            _record_phase(state, f"fix_{n}", fix, ledger)
            _record_phase(state, f"verify_{n}", verify, ledger)
            _write_json(out_dir, fix_path.name, fix)
            _write_json(out_dir, f"04_verdict_{n}.json", verify)
            if fix["exit_code"] != 0:
                print(f"  ! {len(fix.get('group_errors', []))} group(s) failed FIX")
            _mark(state, out_dir, f"fix_{n}", loop=n)

            # Concurrent rounds always run both phases; no separate gate.
            results = verify.get("results", [])
            verdict = verify.get("verdict", "REJECT")
            remaining = _unresolved(findings, results)
            all_settled = bool(results) and not remaining
            print(f"  Verdict {verdict} — "
                  f"{len(findings) - len(remaining)}/{len(findings)} settled")

            if verdict == "APPROVE" and all_settled:
                approved = True
                _mark(state, out_dir, f"verify_{n}", loop=n, findings=findings)
                break

            if remaining:
                findings = remaining
            _normalize_findings(findings, state)
            _mark(state, out_dir, f"verify_{n}", loop=n, findings=findings)
            continue

        # --- Sequential FIX (original path) ---
        if f"fix_{n}" in completed:
            fix = _read_json(fix_path) or {}
        else:
            _banner(f"FIX  (round {n}/{args.max_loops})")
            _normalize_findings(findings, state)
            fix = phase_fix.run_fix(
                findings, dev_cmd, workdir, args.timeout, feature, n,
                getattr(args, "_provider_resolver", None),
                **_provider_call_args(
                    args, "dev", getattr(args, "dev_cmd", None)
                ),
                execution=execution, ledger=ledger,
            )
            _record_phase(state, f"fix_{n}", fix, ledger)
            _write_json(out_dir, fix_path.name, fix)
            if fix["exit_code"] != 0:
                return _phase_failed(f"fix_{n}", fix, state, out_dir)
            _mark(state, out_dir, f"fix_{n}", loop=n)

        gate_blocked = False
        gate_resolved = False
        if fix_verification_cmd:
            gate_name = f"post_fix_gate_{n}"
            gate_path = Path(out_dir) / f"03_post_fix_gate_{n}.json"
            if gate_name in completed:
                fix_gate = _read_json(gate_path) or {}
            else:
                fix_gate = gates.post_fix_gate(
                    workdir, fix_verification_cmd, timeout=args.timeout
                )
                _write_json(out_dir, gate_path.name, fix_gate)
                state.setdefault("gates", []).append(fix_gate)
                if fix_gate.get("infra"):
                    return _phase_failed(gate_name, fix_gate, state, out_dir)
                _mark(state, out_dir, gate_name, loop=n)

            # The FIX artifact is the authoritative evidence bundle for its
            # objective post-gate, including failures routed to the next round.
            fix["verification_gate"] = fix_gate
            if not fix_gate.get("ok", False):
                gate_finding = _gate_finding(fix_gate, "post_fix", n)
                fix["attached_evidence"] = [gate_finding]
                findings = _replace_gate_finding(findings, gate_finding)
                state["findings"] = findings
                _write_json(out_dir, fix_path.name, fix)
                _write_json(out_dir, "state.json", state)
                if n < args.max_loops:
                    continue
                gate_blocked = True
            else:
                gate_resolved = any(
                    finding.get("id") == _GATE_FINDING_ID
                    for finding in findings
                )
                findings = _without_gate_findings(findings)
                state["findings"] = findings
                _write_json(out_dir, fix_path.name, fix)

        if gate_resolved and not findings:
            verify = {
                "phase": "verify",
                "exit_code": 0,
                "results": [{
                    "id": _GATE_FINDING_ID,
                    "status": "resolved",
                    "note": "post-fix verification gate passed",
                }],
                "verdict": "APPROVE",
                "source": "post_fix_gate",
            }
            _write_json(out_dir, f"04_verdict_{n}.json", verify)
            approved = True
            _mark(state, out_dir, f"verify_{n}", loop=n, findings=[])
            break

        if gate_blocked:
            verify = {
                "phase": "verify", "results": [],
                "verdict": "REJECT", "exit_code": 0,
                "source": "post_fix_gate",
            }
            _write_json(out_dir, f"04_verdict_{n}.json", verify)
        elif f"verify_{n}" in completed:
            verify = _read_json(Path(out_dir) / f"04_verdict_{n}.json") or {}
        else:
            _banner(f"VERIFY  (round {n}/{args.max_loops})")
            diff = gitops.get_diff(workdir, branch_point)
            verify = phase_verify.run_verify(
                findings, diff, review_cmd,
                getattr(args, "_provider_resolver", None), jsonio,
                workdir=workdir, branch_point=branch_point,
                **_provider_call_args(
                    args, "verify", getattr(args, "review_cmd", None)
                ),
                execution=execution, ledger=ledger,
            )
            _record_phase(state, f"verify_{n}", verify, ledger)
            _write_json(out_dir, f"04_verdict_{n}.json", verify)
            if verify["exit_code"] != 0:
                return _phase_failed(f"verify_{n}", verify, state, out_dir)

        results = verify.get("results", [])
        verdict = verify.get("verdict", "REJECT")
        if verdict == "APPROVE" and not results:
            print("  ! verifier returned APPROVE with no per-finding results "
                  "— treating as inconclusive; findings stay open")
        remaining = _unresolved(findings, results)
        all_settled = bool(results) and not remaining
        print(f"  Verdict {verdict} — "
              f"{len(findings) - len(remaining)}/{len(findings)} settled")

        if verdict == "APPROVE" and all_settled:
            approved = True
            _mark(state, out_dir, f"verify_{n}", loop=n, findings=findings)
            break

        if remaining:
            findings = remaining
        _normalize_findings(findings, state)
        _mark(state, out_dir, f"verify_{n}", loop=n, findings=findings)

        if n < args.max_loops:
            continue

        if arbiter_cmd and not args.no_arbiter:
            if "arbiter" in completed:
                arb = _read_json(Path(out_dir) / "05_arbiter.json") or {}
            else:
                _banner("ARBITER  (JUDGE)")
                _normalize_findings(findings, state)
                arb = phase_arbiter.run_arbiter(
                    findings, dev_cmd, review_cmd, arbiter_cmd,
                    getattr(args, "_provider_resolver", None),
                    workdir=workdir,
                    timeout=args.timeout,
                    **_provider_call_args(
                        args, "arbiter", getattr(args, "arbiter_cmd", None)
                    ),
                    execution=execution,
                    ledger=ledger,
                )
                _record_phase(state, "arbiter", arb, ledger)
                _write_json(out_dir, "05_arbiter.json", arb)
                if arb["exit_code"] != 0:
                    return _phase_failed("arbiter", arb, state, out_dir)
                _mark(state, out_dir, "arbiter")
            if arb.get("verdict") == "APPROVE":
                approved = True
                arbitrated = True
                conditions = arb.get("conditions", [])

    state["findings"] = findings
    state["costs"] = ledger.summary()
    reason = ""
    if not approved:
        if findings:
            reason = f"findings unresolved after {args.max_loops} loops"
        else:
            reason = f"review verdict {review_verdict} with no findings"

    if approved:
        verdict = "ARBITRATED" if arbitrated else "APPROVED"
        return _finish(
            args, workdir, feature, out_dir, state, verdict,
            loops=loops_run, conditions=conditions, arbitrated=arbitrated,
        )
    return _finish(
        args, workdir, feature, out_dir, state, "REJECT",
        reason=reason, loops=loops_run,
    )


# --- CLI ----------------------------------------------------------------------

def _positive_int(value):
    """argparse type: strictly positive integer."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}")
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {value!r}")
    return ivalue


def _non_negative_int(value):
    """argparse type: integer greater than or equal to zero."""
    try:
        ivalue = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from exc
    if ivalue < 0:
        raise argparse.ArgumentTypeError(
            f"must be a non-negative integer, got {value!r}"
        )
    return ivalue


def _force_provider_value(value):
    """argparse type for repeatable ``ROLE:ALIAS`` provider overrides."""
    role, separator, alias = value.partition(":")
    role = role.strip().lower()
    alias = alias.strip()
    if not separator or role not in _FORCE_PROVIDER_ROLES or not alias:
        allowed = ", ".join(sorted(_FORCE_PROVIDER_ROLES))
        raise argparse.ArgumentTypeError(
            f"expected <role>:<alias> with role in: {allowed}"
        )
    return role, alias


def _force_provider_map(values):
    """Validate repeatable overrides and return one alias per role."""
    result = {}
    for role, alias in values:
        if role in result:
            raise ValueError(f"--force-provider specified more than once for {role}")
        result[role] = alias
    return result


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
    p.add_argument(
        "--provider-config",
        default=None,
        metavar="PATH",
        help="provider registry YAML (env: ADVERSARIAL_PROVIDER_CONFIG)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="skip quota checks and select each role's primary provider",
    )
    p.add_argument(
        "--force-provider",
        action="append",
        default=[],
        type=_force_provider_value,
        metavar="ROLE:ALIAS",
        help="force an alias for one role; repeat for multiple roles",
    )
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
    p.add_argument(
        "--min-chars", "--min-context-chars", dest="min_chars",
        type=_non_negative_int, default=None,
        help="minimum specification characters (env: ACL_MIN_CONTEXT_CHARS)",
    )
    p.add_argument(
        "--min-tokens", "--min-context-tokens", dest="min_tokens",
        type=_non_negative_int, default=None,
        help="minimum estimated specification tokens (env: ACL_MIN_CONTEXT_TOKENS)",
    )
    p.add_argument(
        "--max-retries", type=_non_negative_int, default=3,
        help="transient retries per provider phase (default: 3)",
    )
    p.add_argument(
        "--max-input-chars", type=_non_negative_int,
        default=runner.DEFAULT_MAX_INPUT_CHARS,
        help="hard input cap per provider phase",
    )
    p.add_argument(
        "--max-output-chars", type=_non_negative_int,
        default=runner.DEFAULT_MAX_OUTPUT_CHARS,
        help="hard output cap per provider phase",
    )
    p.add_argument(
        "--truncate-input", action="store_true",
        help="head-truncate oversized provider input instead of rejecting it",
    )
    p.add_argument(
        "--show-costs", action="store_true",
        help="print the final per-model cost breakdown to stderr",
    )
    p.add_argument(
        "--max-agents", type=_positive_int, default=6,
        help="complexity recommendation cap recorded for adaptive execution",
    )
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        args._force_providers = _force_provider_map(args.force_provider)
        args._provider_config = load_provider_config(args.provider_config)
        if args._provider_config is None:
            args._provider_resolver = None
        else:
            if not args._provider_config.quota_cmd:
                raise ProviderConfigError(
                    "PROVIDER_CONFIG_QUOTA_CMD_REQUIRED",
                    "quota_cmd is required when the code loop uses a provider registry",
                )
            # One resolver owns the process-scoped quota cache and selection
            # history for every phase, retry, concurrent group, and plan step.
            args._provider_resolver = QuotaResolver(
                args._provider_config, args._provider_config.quota_cmd
            )
    except (ProviderConfigError, TypeError, ValueError) as exc:
        print(f"X invalid provider configuration: {exc}", file=sys.stderr)
        return EXIT_USAGE

    workdir = str(Path(args.workdir).resolve())
    if not os.path.isdir(workdir):
        print(f"X Workdir not found: {args.workdir}")
        return EXIT_USAGE

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
            if "done" in state.get("completed", []):
                print(
                    f"Run already finished ({state.get('verdict', 'unknown')}) "
                    "— nothing to resume."
                )
                return int(state.get("exit_code", EXIT_INFRA))
        else:
            print("! No resumable state.json found — starting fresh")

    if not os.path.isfile(args.spec):
        print(f"X Spec not found: {args.spec}")
        return EXIT_USAGE
    try:
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"X could not read spec {args.spec}: {exc}")
        return EXIT_USAGE

    try:
        spec_text, preflight_ok = _preflight(args, spec_text, out_dir)
    except (TypeError, ValueError) as exc:
        print(f"X invalid preflight configuration: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not preflight_ok:
        return EXIT_CONTEXT_BLOCKED

    # R1/R3 have succeeded. Only now may git or command resolution run.
    ok, info = gitops.ensure_git_available()
    if not ok:
        print(f"X {info}")
        return EXIT_INFRA
    if args._provider_config is None:
        dev_cmd = resolve_role_cmd(
            "dev", args.dev_cmd, "ACL_DEV_CMD", DEFAULT_DEV_CMD
        )
        review_cmd = resolve_role_cmd(
            "review", args.review_cmd, "ACL_REVIEW_CMD", DEFAULT_REVIEW_CMD
        )
        arbiter_cmd = (
            args.arbiter_cmd or os.environ.get("ACL_ARBITER_CMD") or ""
        ).strip()
    else:
        # Registry commands are deliberately not resolved here: each phase
        # must consult the shared quota resolver immediately before execution.
        dev_cmd = (args.dev_cmd or "").strip()
        review_cmd = (args.review_cmd or "").strip()
        arbiter_cmd = (args.arbiter_cmd or "").strip()
        if not arbiter_cmd and args._provider_config.roles.get("arbiter"):
            # Only used as an enablement sentinel; run_phase_cmd selects the
            # actual command after quota resolution.
            arbiter_cmd = args._provider_config.roles["arbiter"][0].command

    args._spec_text = spec_text
    args._ledger = _restore_ledger(state)
    state.setdefault("context", args._context)
    state.setdefault("thresholds", args._context.get("thresholds", {}))
    state.setdefault("complexity", args._complexity)
    state.setdefault("execution", _execution_record(args))
    state.setdefault("attempts", [])
    state.setdefault("calls", [])
    state.setdefault("warnings", [])
    state.setdefault("provider_history", [])
    cap_events = state.setdefault("cap_events", [])
    if not any(
        isinstance(event, dict) and event.get("phase") == "preflight"
        for event in cap_events
    ):
        cap_events.extend(args._preflight_cap_events)
    state.setdefault("gates", [])
    state["costs"] = args._ledger.summary()

    provider_mode = "registry" if args._provider_resolver is not None else "legacy"
    print(f"\n{'#' * 60}\n  ADVERSARIAL CODE LOOP v4\n"
          f"  Spec: {args.spec}\n  Feature: {feature}\n"
          f"  Max loops: {args.max_loops}\n"
          f"  Provider mode: {provider_mode}\n{'#' * 60}")

    try:
        code = _pipeline(
            args, dev_cmd, review_cmd, arbiter_cmd,
            workdir, feature, out_dir, state,
        )
    except KeyboardInterrupt:
        print("\nX Interrupted — restoring workdir "
              "(loop branch kept; rerun with --resume to continue)")
        _terminate_provider_processes()
        state["phase"] = "interrupted"
        state["costs"] = args._ledger.summary()
        _write_json(out_dir, "state.json", state)
        code = EXIT_INFRA
    except NoProviderAvailable as exc:
        print(f"X no provider available for role '{exc.role}'", file=sys.stderr)
        aliases = set(exc.snapshots) | set(exc.reasons)
        for alias in sorted(aliases):
            snapshot = json.dumps(
                exc.snapshots.get(alias, {}), sort_keys=True, default=str
            )
            reason = exc.reasons.get(alias, "ineligible")
            print(
                f"  {alias}: {reason}; snapshot={snapshot}", file=sys.stderr
            )
        state["error"] = str(exc)
        state["provider_snapshots"] = json.loads(
            json.dumps(exc.snapshots, default=str)
        )
        state["costs"] = args._ledger.summary()
        _write_json(out_dir, "state.json", state)
        code = EXIT_REJECTED
    except gitops.GitError as exc:
        print(f"\nX git error: {exc}")
        state["error"] = str(exc)
        state["costs"] = args._ledger.summary()
        _write_json(out_dir, "state.json", state)
        code = EXIT_INFRA
    finally:
        _restore(workdir, state, out_dir)

    if args.show_costs:
        args._ledger.print_summary(file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
