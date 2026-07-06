# Direct Stdin Pipeline (Codex DEV + Claude REVIEW)

**An alternative to the adversarial_loop.py script for simple refactoring/deletion tasks.** Avoids the pipe stdout synchronization issue where Codex writes files to disk during extended thinking but doesn't close stdout, causing the loop script to hang.

Validated 2026-06-15 on UCI device simulator: 4 phases across 11 files, all 159 tests passing.

## When to use

- Targeted refactoring or deletion tasks (1-5 files)
- Tasks where you know exactly what to change (spec is clear)
- Simple code removals where Codex can write the changes directly
- NOT for complex multi-file feature work where the adversarial loop's iterative refinement adds value

## Pattern

```bash
# 1. Write spec
cat > /tmp/spec.md << 'SPEC'
# Targeted change description
- File A: change X to Y
- File B: remove function Z
SPEC

# 2. Codex DEV via stdin pipe (background)
cd /path/to/project
cat /tmp/spec.md | codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  --sandbox danger-full-access &
CODEX_PID=$!

# 3. Wait for files to appear on disk
# Codex writes during extended thinking — check periodically
sleep 120
git diff --stat

# 4. If Codex is done writing (files changed), kill and build
kill $CODEX_PID 2>/dev/null
make test  # or whatever build command

# 5. Claude REVIEW via diff pipe
git diff HEAD | claude -p --model claude-sonnet-4-20250514 \
  "Review this diff for correctness. No regressions expected."

# 6. Apply review findings, commit
git add -A && git commit -m "..."
```

## Benefits over adversarial_loop.py

| Aspect | adversarial_loop.py | Direct pipe |
|--------|-------------------|-------------|
| Stdout dependency | Waits for Codex stdout to close (can hang 5-10+ min) | Kills Codex when files are on disk |
| Pipeline complexity | 6-phase cycle with JSON parsing | Write → Build → Review → Commit |
| Claude quota | One REVIEW per loop cycle (can be 2-3+ cycles) | One review at the end |
| Best for | Complex iterative features | Simple refactoring/deletion |

## Pitfalls

1. **Codex may still be thinking when you kill it** — check `git diff --stat` for file changes. Codex writes files incrementally. If no files changed, wait longer.
2. **Verify build after kill** — Codex may have partially written changes. Always compile and test after killing.
3. **Model name for Claude** — `claude -p --model claude-sonnet-4` may fail. Use the full versioned name: `claude-sonnet-4-20250514`.
4. **Not for multi-cycle refinement** — If the review finds issues, apply them manually (patch tool) rather than re-running Codex.
