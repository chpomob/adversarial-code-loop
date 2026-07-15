# Sequential Step Execution Pattern

Validated 2026-07-15 during a 21-step adversarial plan execution on the Hermes adversarial skills themselves.

## Problem

The `--plan` flag is NOT wired in `adversarial_loop_v4.py` (pitfall #1). Multi-step plans cannot be executed with a single command. Each plan step must run as its own code loop.

## Pattern

Create a focused spec for each plan step, then launch a sequential adversarial code loop for each:

1. Read the plan's step (e.g. P1 from plan.md)
2. Write a focused `spec-p1.md` covering only that step's files, description, and tests
3. Launch the code loop:
   ```bash
   python3 adversarial_loop.py \
     --spec /tmp/spec-p1.md \
     --workdir <repo-dir> \
     --dev-cmd "codex exec --skip-git-repo-check --sandbox workspace-write" \
     --review-cmd "...claude-tmux.py --yolo --model sonnet --timeout 600 --hard-timeout 1800 --cwd <repo-dir>" \
     --max-loops 2 \
     --timeout 3600 \
     --no-arbiter \
     --feature p1-my-step
   ```
4. On APPROVED (exit 1 but code committed), commit manually:
   ```bash
   cd <repo-dir> && git add -A && git commit -m "feat: step description"
   ```
5. Repeat for P2, P3, etc.

## Results

9 steps completed in ~3 hours. Each step:
- BUILD (Codex): ~2-5 min
- REVIEW (Claude Sonnet via tmux): ~2-5 min  
- FIX (Codex): ~1-3 min
- VERIFY (Claude Sonnet via tmux): ~1-3 min
- Git finalize fails (dirty parent repo) → manual commit in the real repo

## Spec patterns that work

- **New files**: `gates.py (NEW)`, `costs.py (NEW)` → Codex creates them correctly
- **Existing files**: `providers.py`, `runner.py` → Codex patches them, but changes may conflict with earlier uncommitted work
- **Personas**: markdown files → Codex edits them, but may lose formatting
- **Workdir must be the target repo**, NOT a parent directory (avoids auto-init pitfall #25b)
- **claude-tmux --cwd must match the workdir** (pitfall #26)

## Pitfalls

- **Manual commit after every step** is required because the parent repo auto-init swallows commits
- **Codex can cascade** beyond spec scope (fixes adjacent bugs, migrates consumers) — check `git diff --stat`
- **REJECTED steps still write valid code to disk** — the code is on the loop branch and on the worktree. Check `git diff`, commit the changes, and move on
- **Fable 5 has its own usage limit** separate from Claude Pro (pitfall #39). Switch to Sonnet when hit
