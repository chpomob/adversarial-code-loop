"""--plan support: parse a plan.md, validate, topo-sort, run one loop per step.

Each step is executed as a full adversarial loop (BUILD -> REVIEW ->
(FIX -> VERIFY)^N -> ARBITER) on its own sub-branch ``loop/<feature>/<step_id>/1``
forked from the plan's parent branch. A step that passes squash-merges back
into the parent so the next step builds on top of it; a step that fails aborts
the whole plan.

This module is deliberately decoupled from the orchestrator: it takes the
``run_pipeline`` callable (``adversarial_loop._pipeline``) and a ``final_md``
formatter so there is no circular import. The exit-code constants below mirror
``adversarial_loop.EXIT_*`` — a stable, documented contract (0/1/2/3/4).
"""
import copy
import json
import re
from pathlib import Path

from adversarial_common import gitops, jsonio

# Must match adversarial_loop.py EXIT_* (stable documented contract).
EXIT_APPROVED = 0
EXIT_INFRA = 1
EXIT_USAGE = 2
EXIT_REJECTED = 3
EXIT_ARBITRATED = 4

_PASSED = (EXIT_APPROVED, EXIT_ARBITRATED)

# [^*]+ (greedy) == [^*]+? here: the class can never match the trailing '*',
# so the lazy quantifier was a no-op. group(1) is .strip()'d downstream anyway.
_BULLET_RE = re.compile(r"^-\s+\*\*([^*]+)\s*:\*\*\s*(.*)$")
_HEADING_RE = re.compile(r"^###\s+(\S+?)\s*:\s*(.*)$")
# A bullet that is NOT a `- **Key:** value` line — the shape of a multi-line
# sub-list, which parse_plan cannot read (SKILL.md pitfall #26).
_SUBLIST_RE = re.compile(r"^\s*-\s+\S")


# --- parsing -----------------------------------------------------------------

def _split_frontmatter(text):
    """Return (frontmatter_block, body). No frontmatter -> ('', text).

    Raises ValueError if the text opens with ``---`` but has no matching
    closing ``---``: a stray opening fence is malformed frontmatter, not a
    body, and silently returning it as body would let a ``###`` heading inside
    the unterminated frontmatter be parsed as a step.
    """
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise ValueError("frontmatter opened with '---' but never closed")


def _split_commas(val):
    """Split on commas, respecting single/double-quoted segments.

    Keeps a value containing a comma (e.g. ``"src/a, b.rs"``) intact instead
    of splitting it on the comma inside the quotes.
    """
    out, buf, quote = [], [], None
    for ch in val:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _parse_list(val):
    """Parse ``[a, b]`` / ``a`` into a stripped list (empty on ``[]``/``None``)."""
    val = (val or "").strip()
    if val.startswith("["):
        val = val[1:]
    if val.endswith("]"):
        val = val[:-1]
    return [x.strip().strip('"').strip("'") for x in _split_commas(val)
            if x.strip()]


def parse_plan(plan_path):
    """Parse plan.md into a list of step dicts.

    Each step: ``{id, title, files[], description, dependencies[], tests, risks}``.
    Frontmatter is parsed but currently unused (feature name comes from
    ``--feature`` / the plan filename); kept tolerant so future fields land.

    Raises ValueError when ``Files:``/``Dependencies:`` are followed by a
    multi-line bullet list instead of a single comma-separated line
    (SKILL.md pitfall #26) — silently dropping the values would run steps
    against the wrong files.
    """
    text = Path(plan_path).read_text(encoding="utf-8")
    _fm, body = _split_frontmatter(text)

    steps = []
    current = None
    pending_list_key = None  # files/dependencies bullet left without a value
    for line in body.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            if current:
                steps.append(current)
            current = {
                "id": heading.group(1).strip(),
                "title": heading.group(2).strip(),
                "files": [],
                "description": "",
                "dependencies": [],
                "tests": "",
                "risks": "",
            }
            pending_list_key = None
            continue
        if current is None:
            continue
        m = _BULLET_RE.match(line)
        if not m:
            if pending_list_key and _SUBLIST_RE.match(line):
                raise ValueError(
                    f"step {current['id']}: '{pending_list_key.capitalize()}:' is "
                    "followed by a multi-line bullet list, which parse_plan cannot "
                    "read. Put all values on one comma-separated line, e.g. "
                    "'- **Files:** /path/one, /path/two' (SKILL.md pitfall #26)")
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        # An explicit `[]` is an intentional empty list; a bare key with no
        # value usually means the values follow as sub-bullets — flag those.
        pending_list_key = key if key in ("files", "dependencies") and not val else None
        if key in ("files", "dependencies"):
            current[key] = _parse_list(val)
        elif key in current:
            current[key] = val
    if current:
        steps.append(current)
    return steps


# --- validation --------------------------------------------------------------

def _detect_cycle(steps):
    """DFS cycle check. Raises ValueError naming the cycle path on detection."""
    deps = {s["id"]: list(s.get("dependencies", [])) for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in deps}

    def dfs(node, stack):
        color[node] = GRAY
        for d in deps.get(node, []):
            if color.get(d) == GRAY:
                raise ValueError(
                    f"circular dependency: {' -> '.join(stack + [d])}")
            if color.get(d, BLACK) == WHITE:
                dfs(d, stack + [d])
        color[node] = BLACK

    for sid in deps:
        if color[sid] == WHITE:
            dfs(sid, [sid])


def validate_steps(steps):
    """Check: non-empty, unique ids, description present, deps resolve, no cycle.

    Raises ValueError on the first problem found.
    """
    if not steps:
        raise ValueError("plan has no steps")
    seen = set()
    for s in steps:
        sid = s.get("id", "")
        if not sid:
            raise ValueError("step missing id")
        if sid in seen:
            raise ValueError(f"duplicate step id: {sid}")
        seen.add(sid)
        if not s.get("description"):
            raise ValueError(f"step {sid} missing description")
        for dep in s.get("dependencies", []):
            if dep not in seen and dep not in {x["id"] for x in steps}:
                raise ValueError(f"step {sid} depends on unknown step {dep}")
    _detect_cycle(steps)


def topo_sort(steps):
    """Kahn's algorithm, stable in original input order. Returns step dicts."""
    by_id = {s["id"]: s for s in steps}
    idx = {sid: i for i, s in enumerate(steps) for sid in [s["id"]]}
    indeg = {sid: 0 for sid in by_id}
    children = {sid: [] for sid in by_id}
    for s in steps:
        for d in s.get("dependencies", []):
            children[d].append(s["id"])
            indeg[s["id"]] += 1
    ready = sorted([sid for sid in by_id if indeg[sid] == 0], key=lambda i: idx[i])
    order = []
    while ready:
        sid = ready.pop(0)
        order.append(by_id[sid])
        for c in children[sid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
        ready.sort(key=lambda i: idx[i])
    if len(order) != len(steps):  # defensive: validate_steps already guards cycles
        raise ValueError("dependency cycle detected during topo sort")
    return order


# --- per-step execution ------------------------------------------------------

def _step_spec_text(step):
    """Render a step into a self-contained spec the DEV model can implement."""
    files = "\n".join(f"- {f}" for f in step.get("files", [])) or "- (unspecified)"
    deps = ", ".join(step.get("dependencies", [])) or "none"
    return (
        f"Implement step {step['id']}: {step.get('title', '')}\n\n"
        f"Files to modify:\n{files}\n\n"
        f"Description:\n{step.get('description', '')}\n\n"
        f"Acceptance tests:\n{step.get('tests', 'none') or 'none'}\n\n"
        f"Risks:\n{step.get('risks', 'none') or 'none'}\n\n"
        f"Depends on (already merged into parent): {deps}\n"
    )


def execute_step(step, args, workdir, feature, parent_branch, out_dir,
                 dev_cmd, review_cmd, arbiter_cmd, *, run_pipeline):
    """Run one full adversarial loop for *step* on its own sub-branch.

    Forks ``loop/<feature>/<step_id>/1`` from *parent_branch*, delegates to
    ``run_pipeline`` (the orchestrator's ``_pipeline``), and on approval the
    pipeline squash-merges back into *parent_branch*.

    Returns ``{"id", "status": "passed"|"failed", "loops", "exit_code"[, "error"]}``.
    """
    sid = step["id"]
    step_out = Path(out_dir) / sid
    step_out.mkdir(parents=True, exist_ok=True)
    spec_path = step_out / "00_spec.txt"
    spec_path.write_text(_step_spec_text(step), encoding="utf-8")

    branch = f"loop/{feature}/{sid}/1"
    if gitops.branch_exists(workdir, branch):
        gitops.delete_branch(workdir, branch)  # ponytail: rerun replaces stale branch
    gitops.create_branch(workdir, branch, parent_branch)
    gitops.checkout(workdir, branch)
    branch_point = gitops.record_branch_point(workdir, parent_branch)

    # Force merge per step (steps must land in parent to feed the next one),
    # even if the caller passed --no-merge for a single-spec run.
    step_args = copy.copy(args)
    step_args.spec = str(spec_path)
    step_args.no_merge = False
    step_args.resume = False

    state = {
        "completed": ["git_setup"],  # skip setup_git: branch already created above
        "phase": "git_setup",
        "parent_branch": parent_branch,
        "branch": branch,
        "branch_point": branch_point,
        "stash_id": "",
        "loop": 0,
        "findings": [],
        "step_id": sid,
    }

    try:
        code = run_pipeline(step_args, dev_cmd, review_cmd, arbiter_cmd,
                            workdir, f"{feature}/{sid}", step_out, state)
    except gitops.GitError as exc:
        return {"id": sid, "status": "failed", "loops": 0,
                "exit_code": EXIT_INFRA, "error": f"git: {exc}"}

    fin = jsonio_load_final(step_out)
    loops = fin.get("loops", 0) if fin else 0
    if code in _PASSED:
        # execute_step forces --no-merge off: a step must land in the parent so
        # the next step forks from it. _pipeline still returns EXIT_APPROVED/
        # EXIT_ARBITRATED when finalize_git's squash-merge fails (it records
        # merged:false in final.json for callers to inspect), so verify the
        # merge actually landed before trusting the exit code as 'passed'.
        if not (fin and fin.get("merged")):
            err = ((fin.get("reason") if fin else None)
                   or "approved but squash-merge into parent failed")
            return {"id": sid, "status": "failed", "loops": loops,
                    "exit_code": EXIT_INFRA, "error": err}
        return {"id": sid, "status": "passed", "loops": loops, "exit_code": code}
    if code == EXIT_REJECTED:
        err = "verify rejected after max loops"
    else:
        err = (fin.get("reason") if fin else None) or f"exit code {code}"
    return {"id": sid, "status": "failed", "loops": loops,
            "exit_code": code, "error": err}


def jsonio_load_final(step_out):
    """Read <step_out>/final.json defensively (None on any failure)."""
    try:
        return json.loads((Path(step_out) / "final.json").read_text())
    except (OSError, ValueError):
        return None


# --- plan orchestration ------------------------------------------------------

def run_plan(plan_path, args, workdir, feature, out_dir, state,
             dev_cmd, review_cmd, arbiter_cmd, *, run_pipeline, final_md):
    """Parse, validate, topo-sort and execute a plan step by step.

    Returns the process exit code: ``EXIT_APPROVED`` (0) when every step
    passed, ``EXIT_REJECTED`` (3) when a step failed, ``EXIT_USAGE`` (2) on a
    malformed plan. Infrastructure errors surface as ``EXIT_INFRA`` (1).
    """
    try:
        steps = parse_plan(plan_path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"X could not read plan {plan_path}: {exc}")
        return EXIT_USAGE
    except ValueError as exc:  # malformed frontmatter or multi-line bullet list
        print(f"X invalid plan {plan_path}: {exc}")
        return EXIT_USAGE
    try:
        validate_steps(steps)
    except ValueError as exc:
        print(f"X invalid plan: {exc}")
        return EXIT_USAGE
    ordered = topo_sort(steps)

    # Plan-level git lifecycle: ensure repo + identity, stash dirty tree once,
    # then pin .gitignore (after stashing, so the new .gitignore isn't itself
    # stashed away — mirrors phase_git.setup_git's ordering).
    # Per-step branches are created/merged by execute_step; the plan itself
    # never leaves the parent branch.
    if gitops.detect_enclosing_repo(workdir):
        gitops.ensure_git_identity(workdir)
        parent = gitops.get_current_branch(workdir)
    else:
        gitops.auto_init(workdir)
        parent = "main"
    stash_id = gitops.stash_dirty(workdir)
    gitops.ensure_gitignore(workdir, ".adversarial-loop/")
    state.update({
        "parent_branch": parent, "stash_id": stash_id, "phase": "plan",
        "plan": plan_path, "feature": feature, "steps_total": len(ordered),
    })

    _banner_plan(plan_path, feature, ordered)

    results = []
    failed = None
    for step in ordered:
        sid = step["id"]
        print(f"\n{'=' * 60}\n  STEP {sid}: {step.get('title', '')}\n{'=' * 60}")
        res = execute_step(step, args, workdir, feature, parent, out_dir,
                           dev_cmd, review_cmd, arbiter_cmd, run_pipeline=run_pipeline)
        results.append(res)
        # Always return to the parent between steps (reject/infra leaves the
        # step branch checked out).
        _safe_checkout(workdir, parent)
        if res["status"] != "passed":
            failed = res
            break

    all_passed = failed is None
    verdict = "APPROVED" if all_passed else "REJECT"
    reason = "" if all_passed else f"step {failed['id']} failed: {failed.get('error', '')}"
    jsonio.save_artifact(
        out_dir, "final.json",
        json.dumps({"verdict": verdict, "plan": plan_path, "feature": feature,
                    "steps": results}, indent=2) + "\n")
    jsonio.save_artifact(
        out_dir, "final.md",
        final_md(verdict, f"{feature} (plan)", 0, reason, []))
    state["verdict"] = verdict
    state["steps"] = results
    _mark_done(state, out_dir)

    print(f"\n{'=' * 60}\n  PLAN {verdict}\n{'=' * 60}")
    for r in results:
        extra = (f" ({r.get('loops', 0)} loops)"
                 if r["status"] == "passed" else f" — {r.get('error', '')}")
        print(f"  - {r['id']}: {r['status']}{extra}")
    for step in ordered[len(results):]:
        print(f"  - {step['id']}: skipped")

    return EXIT_APPROVED if all_passed else EXIT_REJECTED


def _safe_checkout(workdir, branch):
    """Checkout *branch*, tolerating errors (caller continues regardless)."""
    try:
        if gitops.get_current_branch(workdir) != branch:
            gitops.checkout(workdir, branch)
    except gitops.GitError as exc:
        print(f"! could not return to parent {branch}: {exc}")


def _banner_plan(plan_path, feature, ordered):
    print(f"\n{'#' * 60}\n  ADVERSARIAL CODE LOOP v4 — PLAN MODE\n"
          f"  Plan: {plan_path}\n  Feature: {feature}\n"
          f"  Steps: {len(ordered)} -> {', '.join(s['id'] for s in ordered)}\n"
          f"{'#' * 60}")


def _mark_done(state, out_dir):
    state["phase"] = "done"
    done = state.setdefault("completed", [])
    if "done" not in done:
        done.append("done")
    jsonio.save_artifact(
        out_dir, "state.json", json.dumps(state, indent=2) + "\n")
