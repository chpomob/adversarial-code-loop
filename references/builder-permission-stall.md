# BUILDER Permission Stall — Deadlock Pattern

## Symptom

The adversarial loop runs but:
- BUILDER produces a conversational message asking for write/edit permission (not code)
- CRITIC correctly flags "no reviewable code provided" (F1: blocker)
- FIXER acknowledges the finding but cannot fix because there's no code to fix
- VERIFIER rejects every cycle — still no code
- ARBITER rules in favor of the reviewer: "BUILDER never produced code despite N rounds"

Net result: ~25 LLM calls, 3 loops, 0 lines of code.

## Root Cause

Codex CLI, when asked to "produce the COMPLETE modified source code for ALL
files", enters a meta-discussion loop:

1. It detects that write commands would be blocked by sandbox/permissions
2. Instead of generating code inline (in its stdout), it asks the user to
   "authorize Write/Edit permissions"
3. Across the FIX rounds, it produces hundreds of lines of procedural
   discussion and 0 lines of code — even though inline generation was always
   possible

**Spec-size trigger:** the pattern is far more likely on large specs (the
observed case was a 6.5KB spec asking for a 12-file migration). A spec
requiring >3-4 files in one pass paralyzes the BUILDER — split into
incremental specs.

## Prevention

Add to the spec when targeting Codex as BUILDER:

```
IMPORTANT: Produce ALL code inline in your response using ```python code blocks.
Do NOT attempt to write to disk. Do NOT ask for permissions. Just output the code.
```

Also consider Claude (not Codex) as BUILDER for large refactoring tasks —
Claude is more willing to generate code inline in stdout.

## Detection

After the BUILD step, check `01_code.md` size:

```bash
if [ $(wc -c < .adversarial-loop/01_code.md) -lt 500 ]; then
    echo "EMPTY BUILDER — aborting pipeline"
fi
```

If the content is conversational (not code), the BUILDER stalled. Kill the
pipeline — it will not recover by looping.

## Resolution

Do NOT re-run the loop:

1. Read the ARBITER's decision in `05_arbitrage.md` — its CODE_NEEDS_FIXES
   rationale tells you what to do
2. Implement the fix directly (the orchestrating agent takes over where the
   pipeline failed). There is no code to extract because none was generated.

## Fallback strategies

- **Option A**: re-run BUILDER with Claude instead of Codex (swap roles)
- **Option B**: have the orchestrating agent write the code directly (fastest)
- **Option C**: split the spec into smaller pieces and retry

## Lesson

The adversarial loop is NOT suitable for large multi-file refactors where the
BUILDER can't write to disk. Use it for: single-file code generation, bug
fixes one file at a time, review of existing code. NOT for 12-file
cross-module refactoring.
