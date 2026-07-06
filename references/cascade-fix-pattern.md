# Cascade Fix Pattern (Codex FIX behavior)

## What it is

When Codex executes a FIX phase in the adversarial loop, it often **cascades** beyond the spec scope — migrating consumers, adding minor features, fixing pre-existing bugs it discovers during analysis.

## Observed examples

| Project | Spec scope | Actual FIX scope |
|---------|-----------|------------------|
| chatter Rust P2-T2 | Peer.authenticated field (1 change, 1 file) | Completed P2-T3 (auth gate), P2-T4 (deadlock fix), P3-T1 (typed responses), P2-T5 (tests) — 5 files, ~600 lines |
| omnisense firmware Step 3 | 6-line fix in main.cpp | 13 files, 524 insertions across the entire codebase |
| pz-save-manager DI refactor | 3 files | 15 files, 7 new, 512 insertions, 1065 deletions |

## Why it happens

Codex's FIX prompt receives the full review with findings. It explores the codebase and finds opportunities to improve code quality. This is usually **productive** (accelerates multi-step plans by 2-3x) but can trigger REJECT when GLM-5.2 or Claude find out-of-scope issues.

## Implications

- After an APPROVED cascade, **always check `git diff --stat`** to see what actually changed
- A cascade may commit `target/` build artifacts (pitfall #25) — verify `.gitignore`
- If the next step in your plan targets files that Codex already touched, **verify current state before writing the next spec** — the file may already be different from what the plan assumes
- REJECT after a cascade is often **out-of-scope** (pitfall #24): check if the spec-scope code is correct, commit anyway

## Response

```bash
# After cascade APPROVED:
git diff --stat                    # see actual scope
git log --oneline -1               # review commit message
grep -n "target/" .gitignore       # verify gitignore
git status --porcelain target/ 2>/dev/null | head -3  # check no target/ leaks
```
