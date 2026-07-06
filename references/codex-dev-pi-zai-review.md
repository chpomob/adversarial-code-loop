# Codex DEV + pi/GLM-5.2 REVIEW — Validated Pairing

The most reliable adversarial pairing for this user's environment:

- **DEV**: Codex (`codex exec ... --sandbox danger-full-access`) — always writes executable code
- **REVIEW**: pi/GLM-5.2 (`pi --provider zai --model glm-5.2`) — thorough reviews (5-10KB findings with runnable probes)

## Why This Works

| Role | Model | Strength |
|------|-------|----------|
| DEV | Codex | Always writes executable code. Fast (~3min BUILD). Reliable multi-file edits |
| REVIEW | GLM-5.2 | Thorough architectural analysis. Finds real bugs with runnable probes. ~5-10KB JSON findings |

## Known Behaviors

- GLM-5.2 as reviewer often finds pre-existing bugs outside the spec scope (pitfall #22). Check on-disk code after REJECT — the in-scope fix is usually correct.
- GLM-5.2 as reviewer takes ~3-6 min per CRITIQUE phase (no extended thinking like Fable 5).
- Use `--timeout 600-900` for GLM-5.2 reviewer (faster than Fable 5). No extended thinking needed.

## Timing Reference (omnisense firmware, C/embedded, 52 files)

| Phase | Model | Time |
|-------|-------|------|
| BUILD | Codex | ~2-3 min |
| CRITIQUE | GLM-5.2 | ~3-6 min |
| FIX | Codex | ~3-8 min |
| VERIFY | GLM-5.2 | ~2-3 min |
| **Total** (2 cycles) | | ~16-20 min |

## What Does NOT Work

| Pairing | Problem |
|---------|---------|
| GLM-5.2 DEV + Codex REVIEW | GLM writes prose instead of code (fixed by builder-pi persona in v1.2.0, but still fragile) |
| Claude DEV + Codex REVIEW | Claude writes prose in FIX phase (pitfall #17) |
| GLM-5.2 REVIEW on large files | Times out (>20 min) on files >50K chars in `--file`/`--stdin` mode |
