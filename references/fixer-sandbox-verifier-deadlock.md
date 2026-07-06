# FIXER Sandbox → VERIFIER Deadlock Pattern

## Symptom

The adversarial loop runs, FIXER claims `all_fixed: true` with full `updated_code`, but VERIFIER rejects with 0/N resolved. Loop 2 repeats identically. ARBITER confirms VERIFIER was right: "files on disk were never modified."

## Root Cause

The FIXER runs as Codex CLI in read-only sandbox. It generates corrected code in `updated_code` and detailed `code_diff` entries, but **cannot write to disk**. The VERIFIER reads actual files, finds them unchanged, and correctly rejects.

In loop 2, the FIXER sometimes "disputes" findings, claiming fixes are "already in the code" — referring to its own `updated_code` output, not disk state.

## Resolution

Do NOT re-run the adversarial loop. Instead:

1. Read `final.md` — it contains a "Code Final" section with the fully corrected source
2. Apply with `write_file()` (for complete rewrites) or `patch()` (for targeted edits)
3. Rebuild and test

## Real Example: UCI Chardev (2026-05-26)

```
Loop 1: FIXER (Codex, sandbox) → all_fixed=True, updated_code=7170B
        VERIFIER (Codex) → 0/7 resolved, REJECT
Loop 2: FIXER (Claude) → disputed F1-F7 ("déjà corrigé dans le code")
        VERIFIER → 0/7 resolved, REJECT
ARBITER: CODE_NEEDS_FIXES — "files on disk were never modified"

Post-loop: extracted updated uci_sim_chardev.c from final.md "Code Final" section
           → write_file() → rebuild → 7/7 tests PASS → commit
```

## Prevention Ideas

- If the loop exits ARBITRATED/CODE_NEEDS_FIXES (exit 5/4) and the ARBITER says "files never modified", skip re-running the loop — just apply the fixes manually.
- The `final.md` "Code Final" section is the authoritative corrected source when the FIXER ran in sandbox.
