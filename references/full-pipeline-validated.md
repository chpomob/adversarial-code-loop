# Full Pipeline Validation — Quota Status v2 (2026-07-08)

## Pipeline stages

```
brief → adversarial-spec → spec.md → adversarial-plan → plan.md → adversarial-code-loop --plan → code
```

All 3 stages completed in 1 cycle each. The entire pipeline from brief to 94 passing tests ran
in ~3 hours of wall-clock time with 10 implementation steps.

## Model pairing

| Stage | DEV role | REVIEW role | Result |
|-------|----------|-------------|--------|
| adversarial-spec | Codex (spec-writer) | DeepSeek V4 Pro (challenger) | APPROVED, 13 findings → 1 revise cycle |
| adversarial-plan | Codex (plan-writer) | DeepSeek V4 Pro (challenger) | APPROVED, 2 nit findings → 1 revise cycle |
| adversarial-code-loop | Codex (builder/fixer) | Claude via tmux (reviewer) | 10/10 steps APPROVED, 1 cycle each |

## Key finding: Claude fails as spec/plan challenger

Claude via `claude-tmux.py` exits code 3 (REJECT) without producing parseable JSON in the
spec-challenger and plan-challenger roles. The challenge/verify phases require the model to
output raw JSON findings embedded in the prompt response. Claude outputs conversation text
instead.

Claude **works** as the code-loop reviewer because the prompt shape is different: it reads
files (git diff + source files) from disk and produces findings as structured text that the
phase_verify module can extract.

## Key finding: --plan mode works reliably

The `adversarial-code-loop --plan plan.md` mode was validated on a 10-step plan with strict
dependency ordering (P1→P10). Each step ran as a full adversarial loop on its own
`loop/<feature>/<step_id>/<N>` branch. Steps were executed in topological order. All 10
steps were APPROVED and squash-merged without merge conflicts.

## Project scope

- 4 source files modified (__init__.py, quota_api.py, test_quota_status.py, plugin.yaml)
- 5 providers: Claude, Codex, Gemini, GLM/Zhipu (Z.AI Coding Plan), DeepSeek
- 94 unit tests passing
- Total artifacts generated: ~200KB across 10 step directories
- Total Claude API time: ~11 review calls × 8-12 min each ≈ 2 hours
- Total Codex API time: ~15 write/fix calls × 0.5-3 min each ≈ 20 min
