# Fable 5 Timeout Recovery (2026-07-13)

## Symptom

Adversarial-code-loop exits with `REVIEW exited 124: TIMEOUT after 600s` on the
first REVIEW pass, even on a small codebase (~2580 lines). The process is still
running — Claude is in extended thinking — but the tmux pane inactivity timer
killed it before it produced output.

## Root cause (two layers)

### Layer 1: claude-tmux pane inactivity timeout (original)

The claude-tmux wrapper's inner `--timeout` (pane inactivity detection) was too
low. Fable 5's extended thinking on the first review pass can exceed 12 minutes
of silence with no stdout, even on a small plugin. The pipeline's outer
`--timeout` is irrelevant — the inner pane timer fires first.

**Fix:** `--timeout 900 --hard-timeout 2400` inside claude-tmux.

### Layer 2: pipeline timeout propagation bug (discovered 2026-07-13)

**Even after fixing claude-tmux's inner timeout**, REVIEW can still die at 600s
on a large or threading-heavy diff. The reason: **`phase_review.run_review()`
and `phase_verify.run_verify()` do not propagate the pipeline's `--timeout` to
`providers.run_cmd()`**. They call `run_cmd()` with no timeout argument, so it
defaults to 600s.

The pipeline's `--timeout 2400` only applies to BUILD and FIX phases — REVIEW
and VERIFY always use 600s regardless of the flag.

**Symptom distinguishing the two layers:**
- Layer 1 (pane timeout): claude-tmux reports timeout, tmux session killed
- Layer 2 (pipeline timeout): pipeline reports `REVIEW exited 124: TIMEOUT after 600s`
  - The `600s` is the default from `providers.run_cmd(timeout=600)`
  - The `state.json` error field says exactly `"review: REVIEW exited 124: TIMEOUT after 600s"`

**Fix (validated 2026-07-13):** Patch `phase_review.py`, `phase_verify.py`, and
`adversarial_loop.py` to pass `timeout=args.timeout` through the call chain.

## Cleanup procedure

When `--plan` mode REJECTs on step 1 (nothing was merged):

```
rm -rf .adversarial-loop
git branch -D loop/<feature>/<step>/<N>
git diff -- .gitignore  # verify clean
```

When plan mode REJECTs mid-way with several steps already merged:
1. Check `git log --oneline -5` for the merged steps
2. Create a reduced plan.md with only remaining steps + cleared deps
3. Clean artifacts + delete orphaned loop branch
4. Relaunch with `--feature <new-name>` to avoid branch collision
5. Optionally switch `--review-cmd` if quota is an issue

## Fixed claude-tmux flags

Safe flags for Fable 5 REVIEW in adversarial-code-loop:

```
--review-cmd "python3 /path/to/claude-tmux.py --yolo --model best --timeout 900 --hard-timeout 2400 --cwd <workdir>"
```

| Flag | Value | Why |
|------|-------|-----|
| `--timeout` | 900 (15 min) | Pane inactivity; Fable 5 can be silent 12+ min on first pass |
| `--hard-timeout` | 2400 (40 min) | Hard max per phase; verify needs multiple file reads |
| `--cwd` | `<workdir>` | Required when pipeline runs from a different CWD |
| Pipeline `--timeout` | 2400 (40 min) | Must exceed claude-tmux hard-timeout AND be propagated via the phase patch |

## How to check it's working

```
tmux capture-pane -t claude-tmux-<PID> -p | tail -5
# Look for "Whirring…" with a running duration
```

## Fallback

If Fable 5 repeatedly times out, switch REVIEW to GLM-5.2:
```
--review-cmd "pi -p --provider zai --model glm-5.2 --thinking high"
```
When Claude quota is exhausted mid-plan, use the mid-pipeline model fallback
procedure.
