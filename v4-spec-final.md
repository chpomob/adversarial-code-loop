# adversarial-code-loop v4 — Final Specification

## Overview
Rewrite of adversarial-code-loop with git-aware workflow, modular architecture, and git-adapted personas. All phases operate on git branches: each BUILD/FIX is committed, REVIEW/VERIFY inspect git diffs, the orchestrator manages branches and squash-merges automatically.

## Key corrections from GLM review
- Keep `adversarial-common/` as single shared engine for providers.py, json_io.py, snapshot.py (do NOT duplicate)
- Phase modules are small wrappers calling shared tools — not full reimplementations

## Key corrections from Claude review
- Dirty working tree at start → stash/restore or abort
- Parent branch determination → record merge-base SHA at PHASE 0
- Merge conflict handling → abort with exit code, keep the loop branch
- Empty BUILD / multi-commit BUILD → enforce single commit, force empty commit if nothing changed
- Git identity → auto-bootstrap user.name/user.email in loop branch
- Findings JSON schema → define {id, severity, file, line, summary, status}
- Delta review on FIX → new REVIEW pass on each FIX round's diff
- Objective gate → --build-cmd and --test-cmd optional gates before merge
- No-merge option → --no-merge flag for human-only merge
- Evidence preservation → attach final.json via git tag before deleting loop branch
- Exit codes cleanup → 0 approved, 1 infrastructure, 2 usage, 3 rejected, 4 arbitrated
- Arbiter precedence → --arbiter-cmd set AND --no-arbiter absent → use arbiter
- Resumability → state file + lockfile + --resume flag
- Branch naming → sanitize --feature, monotonic N counter
- .gitignore → auto-append if not present
- Nested repo detection → detect enclosing .git, use it; auto-init only if no parent git
- Language policy → commit messages in English, personas/specs/code English, user output in their language
- --hard-timeout → move to per-provider policy in providers.py

## Architecture

```
scripts/
  adversarial_loop.py         ← orchestrator, reads spec, runs phases sequentially
  __init__.py

skills/
  adversarial-common/
    providers.py               ← provider detection, command resolution (EXISTING, reuse)
    json_io.py                 ← JSON read/write/validate (EXISTING, reuse)
    snapshot.py                ← file tree for --project-dir mode (EXISTING, reuse)
    gitops.py                  ← NEW: init, branch, commit, diff, squash, merge, reject-marker
    personas/
      builder.md               ← revised: git-aware, must commit changes
      builder-pi.md            ← revised: git-aware, pi-specific
      critic.md                 ← revised: review the git diff, not stdin
      fixer.md                  ← revised: git-aware, amend or new commit
      fixer-pi.md               ← revised: git-aware, pi-specific
      verifier.md               ← revised: check findings against git diff
      judge.md                  ← arbiter: resolve disputes
```

## Workflow

```
PHASE 0 — GIT SETUP
  - Detect enclosing .git, or auto-init in workdir
  - Stash dirty working tree (restore on all exit paths)
  - Record branch-point SHA (merge-base with parent)
  - Create branch loop/<feature>/<N>
  - Bootstrap git user.name/user.email

PHASE 1 — BUILD
  - Run DEV model (codex exec / claude-tmux / pi)
  - Stage all changes
  - Commit: "build: <feature> — <summary>"
  - If zero changes: force empty commit

PHASE 2 — REVIEW (first pass)
  - Run REVIEW model on git diff <branch-point>..HEAD
  - Parse and validate JSON findings (schema: {id, severity, file, line, summary})
  - Save to artifacts

PHASE 3 — FIX (loop N)
  - Present findings to DEV model
  - DEV writes fixes
  - Stage and commit: "fix: <feature> — address finding(s) (round N)"
  - Run delta REVIEW on FIX diff (new findings allowed)

PHASE 4 — VERIFY (loop N)
  - Run VERIFY model: check each finding {resolved, rejected, disputed}
  - If APPROVED and all findings resolved → proceed to merge
  - If REJECT and loops remaining → goto PHASE 3
  - If REJECT and max loops reached → goto PHASE 5

PHASE 5 — ARBITER (optional, after max-loops)
  - If --arbiter-cmd set and --no-arbiter absent
  - Arbiter decides final outcome
  - Record conditions in final.json

MERGE / REJECT
  - If APPROVED or ARBITRATED merged:
    - Tag the loop branch HEAD with final.json as annotation
    - Squash merge into parent branch
    - Delete loop branch
    - Exit 0 (APPROVED) or 4 (ARBITRATED)
  - If REJECT:
    - Commit "[REJECTED] <feature> — adversarial (N cycles)" on loop branch
    - Do NOT merge
    - Exit 3
```

## CLI interface

```
python3 adversarial_loop.py \
  --spec <file>                      # required
  --workdir <dir>                    # default: .
  --dev-cmd <cmd>                    # default: codex exec ...
  --review-cmd <cmd>                 # default: pi ... glm-5.2
  --arbiter-cmd <cmd>                # optional
  --max-loops <N>                    # default: 3
  --no-arbiter                       # skip arbiter, REJECT
  --timeout <N>                      # per-subprocess (default: 600)
  --build-cmd <cmd>                  # optional build gate (cargo build, etc.)
  --test-cmd <cmd>                   # optional test gate (cargo test, etc.)
  --no-merge                         # leave loop branch, do NOT merge
  --feature <name>                   # branch naming (default: from spec filename)
  --out <dir>                        # artifacts (default: .adversarial-loop)
  --resume                           # resume from state file
```

## Artifacts (auto-gitignored)

```
.adversarial-loop/<feature>/
  state.json               ← resumability state (phase, loop, branch-point SHA)
  00_spec.txt
  01_build.md
  02_review_1.json
  03_fix_1.md
  04_verdict_1.json
  03_fix_2.md
  ...
  final.json
  final.md
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | APPROVED — squash merged into parent |
| 1 | Infrastructure failure |
| 2 | Usage error / bad spec |
| 3 | REJECT after max-loops |
| 4 | ARBITRATED — merged with conditions (recorded in final.json) |

## Findings JSON schema (v4)

```json
{
  "findings": [
    {
      "id": "A1",
      "severity": "blocker|major|minor|nit",
      "file": "path/to/file.rs",
      "line": 42,
      "summary": "Short title",
      "evidence": "Detailed description"
    }
  ],
  "verdict": "REQUEST_CHANGES|APPROVE|REJECT"
}
```

VERIFY output:
```json
{
  "results": [
    {"id": "A1", "status": "resolved|rejected|disputed"},
    {"id": "A2", "status": "..."}
  ],
  "verdict": "APPROVE|REJECT"
}
```

## Persona changes (git-aware)

Each persona gets an instruction block at the top:
```
You are working in a git branch. Every change you make will be committed.
- BUILD: produce complete, working code. All new/changed files will be staged and committed.
- REVIEW: inspect git diff <branch-point>..HEAD. Each finding must reference actual code in the diff.
- FIX: address each finding. Your changes are committed as a new fix round.
- VERIFY: check if each finding is resolved by inspecting the current diff. 
  A finding is resolved if the problematic code is gone or corrected.
```

## Rollout plan

1. Create `gitops.py` in `adversarial-common/`
2. Update persona files (builder, builder-pi, critic, fixer, fixer-pi, verifier, judge) — add git-awareness
3. Build phase modules: build, review, fix, verify, arbiter
4. Build `adversarial_loop.py` orchestrator
5. Update SKILL.md with new flags, exit codes, workflow, pitfalls
6. Integration tests: no git, dirty tree, empty diff, merge conflict, malformed JSON, timeout, max-loops reject
7. Keep `adversarial_loop_v3.py` alias for one release
8. Integration test gate before v3 replacement
