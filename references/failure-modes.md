# Adversarial Loop — Failure Mode Taxonomy

Five recurring failure categories observed across pipeline runs, with root
causes and mitigations. Wrapper-level failures (timeouts, orphaned processes,
.done detection) are a separate family — see `wrapper-failures.md`.

## 1. Reviewer outputs prose instead of JSON

**Symptom:** `loop_N_04_verdict.json` contains markdown/prose; the verdict
parses as `UNKNOWN` and the real verdict is lost.

**Root cause:** the VERIFIER/CRITIC model answers in markdown despite the
persona's JSON requirement.

**Mitigation:** the script extracts the largest valid JSON object from the
output (handles fences and prose preamble/trailing commentary), but if the
model emits no JSON at all the verdict degrades to UNKNOWN. Strengthen the
spec ("output ONLY the JSON object") or use a CLI that handles JSON natively
(Codex) for strict-JSON roles.

## 2. Phase crash on quota/rate limit

**Symptom:** `X Phase 'FIX #2' failed (exit code 1)` with a tiny output file.

**Root cause:** the model hit its quota mid-pipeline (long BUILD + FIX runs
consume a lot of input tokens).

**Mitigation:** this is correct behavior — `fail_phase()` stops the pipeline
rather than feeding broken output downstream. Earlier artifacts are preserved.
Resume strategy and quota-aware scheduling: see `quota-aware-orchestration.md`.

## 3. VERIFIER false positives (hallucinated claims)

**Symptom:** VERIFY asserts something about the code that is factually wrong
(e.g. claims functions were made `suspend` when they were not), wasting a
cycle and trust.

**Root cause:** the VERIFIER only sees the diff/text, not the real source
tree, and does not compile anything.

**Mitigation:** never act on a VERIFIER claim without checking the actual
code. Treat verdicts as advisory.

## 4. Generated code does not compile

**Symptom:** type mismatches or API misuse discovered only after extraction.

**Root cause:** neither CRITIC, FIXER, nor VERIFIER compiles. The pipeline
does NOT guarantee compilable code.

**Mitigation:** ALWAYS compile and run tests after extraction, before commit
(`python3 -m py_compile`, `./gradlew compileDebugKotlin`, firmware build…).

## 5. Fragile post-loop extraction

**Symptom:** regex extraction of code blocks from `01_code.md` mis-splits
multi-file outputs (changing formats, ambiguous file boundaries, unclosed
blocks).

**Mitigation:** see `post-loop-extraction-workflow.md` for the robust
procedure (block-order convention, size checks, build verification).

## Manual pipeline fallback

When the scripted pipeline is blocked (wrapper failures, repeated phase
crashes), the phases can be run by hand: write the persona + input to a file,
run each CLI manually (or via manual tmux, see `wrapper-failures.md`), and
save outputs under the same artifact names so post-loop extraction keeps
working (and `--resume` in adversarial_review.py picks them up).
