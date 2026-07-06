# Timeout Recovery Workflow

**Recover a timed-out BUILD phase when Codex (or claude-tmux) wrote files to
disk before stdout closed.** Validated 2026-06-13 on Bruce firmware (20 review
findings across 7 files, Codex DEV, `--timeout 600`).

## When to use

- BUILD phase exited 124 (TIMEOUT) or was killed
- Codex had `--sandbox danger-full-access` + `--workdir` set
- `01_code.md` is empty or missing (stdout never closed)
- BUT files appear modified: `git diff --stat` shows changes

## Recovery steps

```bash
# 1. Verify the code is on disk (not just in sandbox)
cd <workdir>
git diff --stat
# Expect: files changed, insertions/deletions > 0

# 2. Compile immediately — don't re-run the loop
pio run -e <target>  # or make, cargo build, etc.

# 3. If compilation succeeds, review the diff
git diff | codex exec --skip-git-repo-check --sandbox read-only \
  "Review this git diff for correctness and safety..."
# OR use adversarial-code-review with --stdin mode

# 4. Apply any review findings as targeted patches
# (prefer patch tool over re-running the loop)

# 5. Recompile and commit
git add -A && git commit -m "..."
```

## Why this works

Codex writes files to disk during its extended thinking phase via the sandbox
Write tool. The subprocess timeout only kills the **stdout pipe** (the markdown
artifact `01_code.md`). Files written through the sandbox persist because the
sandbox process (`codex-exec`) outlives the stdout pipe for a brief window.

The critical clue is `git diff --stat` showing changes despite exit 124.

## When NOT to recover this way

- **Without `--workdir`**: Codex writes to a temp sandbox that gets destroyed on
  timeout. Check `ls ~/.codex/tmp/` for sandbox artifacts.
- **Read-only sandbox**: Codex can't write files. The timeout means zero output.
  Re-run with a higher `--timeout` or split the spec.
- **claude-tmux as FIXER**: claude-tmux writes to a file via the Write tool,
  which is captured differently. See `references/wrapper-failures.md`.

## Prevention

- Split large specs (pitfall #5: >3-4 files per pass) — Codex on 20 findings ×
  7 files timed out at 600s but wrote everything. With 3-4 files, it finishes
  within timeout and produces the `01_code.md` artifact.
- For known-large specs: `--timeout 900` or background mode.
