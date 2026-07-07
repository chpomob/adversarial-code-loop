# Multi-repo planning with --plan mode

## How it works

`--plan plan.md` executes each step on its own git branch. When a step lists files
from a different repo than the main `--workdir`, `_resolve_step_workdir()` detects the
enclosing `.git` and runs the step in that repo.

## Known issues (2026-07-07)

1. **Plan-level git lifecycle stays in `--workdir`.** The stash, parent branch detection,
   and .gitignore setup all run in the original `--workdir`, not the resolved repo.
   This means dirty-tree handling is per-workdir, not per-repo.

2. **Already-done steps produce empty squash.** If BUILD produces no changes (task
   already applied), `git merge --squash` finds nothing to commit. The `squash_merge()`
   fallback to `git merge --ff-only` handles this in most cases.

3. **File paths must be absolute.** `_resolve_step_workdir()` calls
   `gitops.detect_enclosing_repo()` which needs an absolute path. Relative paths are
   resolved against `--workdir`.

4. **Plan format is fragile.** Files and dependencies must be on a single comma-separated
   line. Multi-line bullet lists are NOT parsed. See SKILL.md pitfall #26.

## Validated

2026-07-07: 8-step cross-skill refactoring across 5 repos:
- P2 (jsonio helpers) → adversarial-common: passed
- P3 (fail_phase alias) → adversarial-common: passed
- P4 (run_arbiter params) → adversarial-code-loop: passed
- P5 (gitops consolidate) → adversarial-code-loop: passed
- P6 (try_parse_json) → adversarial-code-loop: rejected (GLM quota)
- P7-P9: skipped

4/8 steps succeeded. The plan format and multi-repo detection worked correctly where
file paths were absolute and repos were pre-committed.
