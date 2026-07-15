# claude-tmux + adversarial-plan challenge phase compatibility

## Problem

The adversarial-plan CHALLENGE phase pipes the plan.md to a reviewer command via stdin.
When using claude-tmux, Claude receives the plan + "Write to file..." instruction.
Claude then runs shell commands to explore the repo (as instructed by the persona)
but never reaches the JSON output writing stage. The `done.sentinel` file is never
created, and the wrapper times out or falls back to pane scraping (which captures
incomplete output → "invalid JSON after retry").

## Root cause

The CHALLENGE phase's persona (plan-challenger.md) tells Claude to:
1. Read the plan from stdin
2. Explore the repository files on disk  
3. Write JSON findings

Claude runs step 2 (exploration) for too long — it reads files, runs `git diff`,
and burns through context/time budget. By the time it tries to create the JSON
output file + done.sentinel, the hard-timeout fires or the Write tool fails
because the session is being cleaned up.

This does NOT affect the adversarial-code-loop REVIEW phase, where the persona
is different (critic.md), the input is a git diff, and Claude's exploration is
more bounded.

## Validated on

- 2026-07-14: adversarial-features plan, CHALLENGE phase with claude-tmux via
  claude-tmux-wrapper v2 (done.sentinel mechanism). Same failure across 3 attempts.
  Switching to GLM-5.2 (`pi -p --provider zai --model glm-5.2 --thinking high`)
  completed the challenge in ~30s with valid JSON.

## Workaround

Use GLM-5.2 (via `pi`) for the CHALLENGE phase of adversarial-plan and the
CHALLENGE/VERIFY phases of adversarial-spec. Keep claude-tmux for the 
adversarial-code-loop REVIEW phase, where it works reliably.

## Permanent fix ideas

1. Pre-compute the exploration context (file listing, git log) and include it in
   the prompt so Claude doesn't need to run shell commands.
2. Add `--max-turns 3` to claude-tmux to limit exploration before writing.
3. Modify the plan-challenger persona to instruct: "Write your JSON output file
   BEFORE exploring the repo — exploration is optional, output is mandatory."
