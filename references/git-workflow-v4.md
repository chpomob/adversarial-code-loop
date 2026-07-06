# adversarial-code-loop v4 — Git Workflow Design

## Why git-native?

The v3 pipeline writes files directly into the workdir without isolation:
- A prose-overwrite bug destroys source files (pitfall #17)
- No rollback: after REJECT you must manually restore corrupted files
- Codex sandbox build artifacts (`target/`) leak into the workdir
- REVIEW on stdin concatenation loses file structure context
- Multi-loop runs pollute the project directory

v4 solves these by running each loop on an isolated git branch.

## Workflow

```
PHASE 0 — GIT SETUP
  - Detect enclosing .git (walk up parents), or auto-init in workdir
  - Stash dirty working tree (restore on ALL exit paths via try/finally)
  - Record branch-point SHA (merge-base with parent branch)
  - Create branch loop/<feature>/<N> (sanitized feature name, monotonic N)
  - Bootstrap git user.name / user.email (repo-local, so git never fails)
  - Ensure .adversarial-loop/ in .gitignore

PHASE 1 — BUILD
  - Run DEV model (codex exec / claude-tmux / pi)
  - Stage all changes: git add -A
  - Commit: git commit -m "build: <feature> — <summary>"
  - If zero changes (tree clean): git commit --allow-empty

PHASE 2 — REVIEW (first pass)
  - Get diff: git diff <branch-point>..HEAD
  - Run REVIEW model with diff on stdin
  - Validate JSON output against v4 findings schema
  - Save findings to artifacts

PHASE 3 — FIX (loop N)
  - Present findings to DEV model
  - DEV writes fixes to disk
  - Stage and commit: git commit -m "fix: <feature> — round N"
  - (Optional) delta REVIEW on FIX diff

PHASE 4 — VERIFY (loop N)
  - Run VERIFY model with findings + current diff
  - Check each finding: {resolved, rejected, disputed}
  - If APPROVED and all resolved → proceed to merge
  - If REJECT and loops remaining → goto PHASE 3
  - If REJECT and max loops reached → goto PHASE 5

PHASE 5 — ARBITER (optional, after max-loops)
  - Run only if --arbiter-cmd set AND --no-arbiter absent
  - Arbiter decides final outcome
  - Record conditions in final.json

MERGE / REJECT
  - If APPROVED:
    1. Tag loop branch HEAD with final.json annotation
    2. git checkout <parent>
    3. git merge --squash <loop-branch> && git commit
    4. git branch -D <loop-branch>
    5. Exit 0
  - If REJECT:
    1. git commit --allow-empty -m "[REJECTED] <feature>"
    2. Do NOT merge
    3. Keep loop branch for inspection
    4. Exit 3
  - Always: unstash if stashed; restore parent branch

## Branch naming

Branch: `loop/<feature>/<N>`
- feature: sanitized from --feature (lowercase, non-alnum→hyphen, max 40 chars)
- N: scanned from existing `loop/<feature>/*` refs, max+1 (starting at 1)

## Key design decisions

| Decision | Value |
|----------|-------|
| Pipeline controls git | DEV model writes files only; the orchestrator stages and commits |
| Diff base | merge-base(parent, HEAD), not HEAD~1 — survives multi-commit phases |
| Empty builds | `--allow-empty` so REVIEW always sees a diff |
| Dirty tree | Stash at PHASE 0, restore on ALL exits (try/finally, not just success) |
| Merge conflicts | Abort with exit code 1, keep loop branch for manual resolution |
| Evidence | Tag loop branch with final.json before deletion |
| Resume | state.json written BEFORE each phase (optimistic), phase field marks progress |
| Concurrency | Lockfile preventing simultaneous loops on same workdir |
| Auto-init | Only when no enclosing .git found; bootstrap user.name/user.email |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | APPROVED — squash merged into parent |
| 1 | Infrastructure failure (timeout, merge conflict, git missing) |
| 2 | Usage error / bad spec |
| 3 | REJECT after max-loops — loop branch preserved |
| 4 | ARBITRATED — merged with conditions (recorded in final.json) |

## State persistence (for --resume)

```
.adversarial-loop/<feature>/
  state.json     ← current phase, loop counter, branch-point SHA
  00_spec.txt
  01_build.json
  02_review.json
  03_fix_1.json
  04_verdict_1.json
  03_fix_2.json
  ...
  final.json
  final.md
```

## Comparison: v3 vs v4

| Aspect | v3 | v4 |
|--------|----|----|
| Workspace isolation | Direct writes to workdir | Isolated git branch |
| Rollback | Manual (git checkout) | git reset --hard + branch delete |
| Review input | Stdin concatenation (no file structure) | git diff (file-aware) |
| Codex target/ leak | Leaks into workdir if not gitignored | Stays in branch, not on parent |
| Prose overwrite recovery | Manual git checkout per file | Full branch discard |
| Multi-step orchestration | Manual branch management | Automated squash-merge |
| Resume support | None | state.json + --resume flag |
| Concurrent runs | Unsafe (write collisions) | Lockfile |
