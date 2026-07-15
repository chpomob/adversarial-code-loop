# Phase challenge prompt reduction (2026-07-15)

## Problem
`phase_challenge.py` embedded the full plan text and full spec text in the prompt sent to the challenger model. For a 240-line spec + 761-line plan, this was 1000+ lines of embedded text. Claude Fable 5 with extended thinking took 15-25 min to process this, and Claude Sonnet couldn't complete within reasonable timeouts.

## Root cause
Historical: `_build_prompt()` was designed for model-agnostic operation (works even for providers without file access). But the prompt also says "both also on disk at plan.md and spec.md in the current directory." The embedding was redundant — the model can read from disk.

## Fix (2026-07-15)
Modified `adversarial-plan/scripts/phases/phase_challenge.py`:
- Removed `plan_text` and `spec_text` parameters from `_build_prompt()`
- Removed `f"--- plan.md ---\n{plan_text}"` and `f"--- spec.md ---\n{spec_text}"` from the prompt
- Changed prompt from "Challenge the implementation plan below against its specification (both also on disk...)" to "Challenge the implementation plan at `plan.md` against its specification at `spec.md` (both are in the current directory)."
- Updated `run_challenge()` call site to omit the text parameters

## Result
- Prompt reduced from 1000+ lines / ~50K chars to **740 chars** (~40x reduction)
- Claude Sonnet completes challenge in ~2 min instead of timing out at 20 min
- The model reads plan.md and spec.md from disk via the `--cwd` flag passed to claude-tmux
- Verified with `_build_prompt()` returning "740 chars" (down from embed)

## Requirements
- The challenger command MUST have `--cwd <workdir>` set so it can find plan.md/spec.md on disk
- `pi` (GLM-5.2) handles this automatically (runs in subprocess with cwd=workdir)
- claude-tmux requires explicit `--cwd`

## Files affected
- `adversarial-plan/scripts/phases/phase_challenge.py` (only)

## Verification
```python
from phases.phase_challenge import _build_prompt
p = _build_prompt("abc123")
assert "--- plan.md ---" not in p
assert "--- spec.md ---" not in p
assert "plan.md" in p and "spec.md" in p
assert len(p) < 2000
```
