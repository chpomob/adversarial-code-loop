# Batch Splitting Strategy for Adversarial Dev Loops

How to go from an adversarial-code-review report (17 findings) to fixed code via adversarial dev loops, without stepping on your own feet.

## The Pipeline

1. **adversarial-code-review** → finds bugs, classifies by severity, cross-validates
2. **Group findings into batches** by file dependency (see below)
3. **Write a spec per batch** covering the fixes, files, and test requirements
4. **Run adversarial dev loops sequentially** — NEVER parallel on overlapping files
5. **Commit after each batch** before starting the next

## Why Sequential?

The DEV/FIXER role writes files to the workdir. Two simultaneous loops that touch the same file will overwrite each other. The review batches may touch disjoint files but the dev loop writes what the spec asks for — and specs often overlap on shared files (e.g. `routes_api.py`, `gui.py`).

**Rule:** only parallelize when `git diff --stat` between batches shows zero file overlap. In practice, this almost never happens — just run sequentially.

## Batch Grouping Rules

1. **Tightly coupled bugs → same batch.** If fixing bug A enables bug B (e.g. callback fix enables refresh-loop fix), batch them together.
2. **Same file modified → same batch if possible.** Two bugs in `routes_api.py` should be one batch.
3. **Independent fixes → separate batches.** Config atomicity (routes_api.py) and version sync (__init__.py) can be separate if they don't touch the same function.
4. **Size: 3-6 bugs per batch max.** More than 6 and the spec gets too long; the DEV may skip items.

## Validated Example (pz-save-manager, 2026-06-16)

17 adversarial-review findings, 10 fixed across 3 batches:

| Batch | Bugs | Files | Cycles | Files touched |
|-------|------|-------|--------|---------------|
| 1 | A2/B1, B2, B3, C1, C2 | 5 | 1 | gui.py, routes_api.py, watcher.py, index.html, test_gui.py |
| 2 | B5, B6, B7, B8 | 4 | 1 | routes_api.py, backup.py, index.html, __init__.py |
| 3 | A4 | 1 | 2 | gui.py |

Batch 1 and 2 both touched `routes_api.py` and `index.html` → must be sequential. Batch 3 touched `gui.py` (also touched by batch 1) → must be sequential.

**Total: ~30 min for 3 batches, 4 cycles, all APPROVED, Codex DEV + Claude Opus REVIEW.**

## Pitfalls

- **Don't batch security hardening with functional fixes.** The review's A1 (loopback gate) is a cross-cutting concern touching every route — it deserves its own batch with careful testing.
- **The adversarial-code-review cross-review was one-directional** (A reviewed B only). Single-reviewer findings (A1-A9) have lower confidence. Prioritize cross-validated (A2/B1) and consensus (B2-B8) findings first.
- **Disputed findings (A7/B4) need human adjudication** before entering a dev loop. Don't automate a fix for something the reviewers disagreed on.
