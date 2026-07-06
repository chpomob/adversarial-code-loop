# Step Chaining Workflow

## Upstream: Adversarial Planning

This workflow assumes you already have a **synthesized fix plan** from the adversarial planning phase (see SKILL.md "Pre-loop: Adversarial Planning Phase"). The plan defines the steps; this workflow chains their execution.

## Per-step .diff generation

After each adversarial loop completes, generate a .diff so the user can review or share:

```bash
cd /path/to/project
git diff > ".adversarial-step-N/step-N-changes.diff"
# or for a clean patch:
git add -A && git diff --cached > ".adversarial-step-N/step-N.patch"
git commit -m "step-N: description"
```

Useful when the project is for someone else (friend/colleague/OSS) — the .diff files become a shareable patch series.

## Workflow
1. Apply each fix step as an `adversarial-code-loop` invocation
2. Monitor completion in background (use `notify_on_complete=true`)
3. On APPROVED: generate .diff, commit, clean artifacts, spec the next step, launch next loop
4. On REJECT: check if code on disk is usable (pitfall #22), generate .diff, commit if tests pass, adjust spec for remaining issues
5. On pipeline failure (exit 1): restore corrupted files (`git checkout`), fix spec, retry

## Data to track per step
- Model pair (DEV + REVIEW)
- Time per phase (BUILD/CRITIQUE/FIX/VERIFY)
- Number of cycles needed
- Findings (in scope vs out of scope)
- Commit summary

## Recommended model pairings
- **Codex DEV + GLM-5.2 REVIEW**: Most reliable. Codex writes files, GLM reviews thoroughly.
- **Codex DEV + Claude REVIEW**: High quality, reviews stay in scope better than GLM.
- **GLM-5.2 DEV + Codex REVIEW**: Works with pi-specific personas (v1.2.0+). Good for simple steps.
- **GLM-5.2 DEV + Claude REVIEW**: Works but Claude may find many out-of-scope issues.

## Codex cascading-fix behavior (beneficial)
When Codex is the FIXER, it often resolves MORE findings than the spec requires — it cascades to fix related bugs in other files. Validated 2026-07-01 (omnisense firmware Step 3): spec asked for a single `fall_get_event()` duplicate fix in `main.cpp`; Codex resolved **11 findings across 13 files** (config parser, seqlock, fall_detector, Makefile dependencies, dead SPSC code, etc.).

**Don't fight this behavior.** It's not a bug — Codex explores the codebase and fixes related issues it finds. Always check `git diff --stat` after a cascade to audit the extra changes. If the cascade touched valid bugs (tests pass), commit them. If it introduced unwanted changes, `git checkout` those specific files.

### Consumer-migration cascade (workspace-breaking changes)

A specific cascading-fix sub-pattern occurs when the spec changes a shared type/protocol/API that consumers depend on. GLM REVIEW REJECTs because the workspace no longer compiles (unresolved imports, renamed types). Codex FIX then **migrates all consumers** — possibly far beyond the spec's scope. Validated 2026-07-06, chatter Rust project (P1-T1 protocol split):

- **Spec scope**: only `protocol/src/lib.rs` (replace `MessageChatter` with `ClientMessage`/`ServerMessage`)
- **GLM REJECT**: "BREAKING API CHANGE WITH NO CONSUMER MIGRATION — 25+ references in server/main.rs and client/app.rs"
- **Codex FIX**: migrated `server/src/main.rs` (288 lines changed), `client/src/app.rs` (198 lines changed), cleaned Cargo.toml deps, bumped version to 0.2.0. **6 files, 872 insertions, 488 deletions** — far beyond the 1-file spec.
- **Result**: APPROVED, workspace compiles and tests pass.

**Anticipate this pattern** when the spec involves renaming or restructuring shared types (enums, traits, public functions exported to other crates/modules). If you know the change is breaking, mention in the spec: "This is a breaking change — migrate consumers in the same pass." The FIXER will do this anyway, but it saves a cycle.

**Post-cascade audit**: always verify `git diff --stat` includes the expected consumer files. If the cascade missed a consumer, that file will break at build time — add it to the next step's spec.

## Pitfalls
- GLM-5.2 may hit API rate limits (Z.AI Lite: ~80 prompts/5h). Track quota usage between steps.
  - Each adversarial loop = 3-4 prompts (BUILD + CRITIQUE + FIX + VERIFY)
  - Each adversarial review = 6-8 prompts (A review + B review + cross-review + synthesis)
  - After ~10 loops or ~6 reviews the quota is depleted. The reset time is shown in the 429 error.
  - **Recovery:** Switch to Codex DEV + Codex REVIEW (no quota), or wait for the rolling 5h window reset.
- Large files (>500 lines) sent via stdin timeout GLM-5.2 (>20 min). Use `--dir` for code review, not `--stdin`.
- Codex as FIXER may cascade to fix unrelated files. Always check `git diff --stat` after each loop.
