---
name: adversarial-code-loop
description: "BUILD → REVIEW → (FIX → VERIFY)^N → ARBITER on isolated git branches. Git-native: each loop runs on its own branch, changes are committed, reviews inspect git diffs. NOTE: `--plan` mode documented below is NOT wired in the Python code (see pitfall #1)."
version: 4.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [adversarial, code-review, multi-model, sequential, loop, persona, git]
    related_skills: [adversarial-code-review, triangle-code-review, claude-tmux-wrapper]
---

# Adversarial Code Loop v4

**BUILD → REVIEW → (FIX → VERIFY)^N → ARBITER.** A sequential pipeline where one model
writes code, another critiques the git diff, the first fixes, the second validates, and
an optional arbiter resolves the last disagreement. Every loop runs on its own git
branch; each BUILD/FIX is a commit; reviews inspect real git diffs; the result is squash-
merged into the parent branch (or marked `[REJECTED]`).

> **Rule: the orchestrator never writes code directly.** This skill delegates code to
> DEV/FIXER agents (codex, claude-tmux, pi). The orchestrator writes the spec, launches
> the pipeline, and interprets the results. Never use `patch`/`write`/`bash` to edit code
> inside a task covered by this skill — always go through the DEV role. If no DEV agent is
> configured explicitly, use `pi` with the current model.

Based on Multi-Persona adversarial debate (Smit et al., ICML 2024): each role gets a
distinct persona, which improves quality even when both roles share the same model.

**When to use:** code that must be **reviewed by another model** before delivery
(breaking the echo chamber), critical code (security, auth, money), and well-scoped
multi-file refactors (up to ~15 files with a structured spec). Not for simple questions,
trivial 1-file changes, or open-ended design exploration.

## Overview — what's new in v4

v4 is **git-native**. Where v3 wrote files directly to the worktree and reviewed a stdin
concatenation of file contents, v4 isolates every loop on a dedicated branch and reviews
real diffs.

| Concern | v3 | v4 |
|---------|----|----|
| Isolation | none — writes to live worktree | dedicated branch `loop/<feature>/<N>` |
| Review input | concatenated file contents (stdin) | `git diff <branch-point>..HEAD` |
| BUILD/FIX output | prose/JSON the orchestrator extracts | files committed by the model |
| Recovery on failure | manual file salvage | `git reset`/`git checkout` to restore |
| Merge | manual `git add -A` | squash-merge into parent branch |
| Rejection | exit code only | `[REJECTED]` marker commit + branch preserved |
| Resume | not supported | `--resume` from `state.json` |
| JSON robustness | strict `json.loads` | `strip_json_wrapper` parses markdown-fenced JSON |
| Gates | none | optional `--build-cmd` / `--test-cmd` |
| Result contract | `final.json` + exit code | `final.json` + exit code (unchanged, enriched) |

v3 is preserved verbatim as `adversarial_loop_v3.py` for one release (see
[Migration from v3](#migration-from-v3)).

## Workflow

```
PHASE 0 ──→ GIT SETUP   (detect/init repo, stash dirty tree, record branch-point,
                          create loop/<feature>/<N>, bootstrap git identity, gitignore)
PHASE 1 ──→ BUILD        (DEV writes code, orchestrator stages + commits "build: ...")
                          [optional --build-cmd gate]
PHASE 2 ──→ REVIEW       (model on git diff <branch-point>..HEAD → JSON findings)
PHASE 3 ──→ FIX          (DEV addresses findings, orchestrator commits "fix: ... (round N)")
PHASE 4 ──→ VERIFY       (model checks each finding resolved | rejected | disputed)
   loop 3-4 until APPROVED or --max-loops reached
PHASE 5 ──→ ARBITER      (optional; resolves disputes after max-loops)
                          [optional --test-cmd gate]
MERGE     ──→ squash-merge into parent + evidence tag (APPROVED / ARBITRATED)
              or [REJECTED] marker commit, loop branch preserved (REJECT)
```

PHASE 0–5 and the merge are implemented as thin wrappers in `scripts/phases/`
(`phase_git`, `phase_build`, `phase_review`, `phase_fix`, `phase_verify`,
`phase_arbiter`). The shared engine — subprocess runner, JSON I/O, provider detection,
git operations — lives in the `adversarial-common` sibling skill.

## CLI flags

Resolution order per command role: **CLI flag > env var > built-in default**. The
built-in defaults name specific tools/models (see table) but are overridable; set the
env vars or flags to point at your own DEV/REVIEW/ARBITER CLIs.

| Flag | Env | Default | Description |
|------|-----|---------|-------------|
| `--spec` | — | *(required)* | Specification file to implement |
| `--workdir` | — | `.` | Working directory (subprocess cwd, base of `--out`) |
| `--dev-cmd` | `ACL_DEV_CMD` | `codex exec --skip-git-repo-check --sandbox workspace-write` | DEV (BUILDER/FIXER) command |
| `--review-cmd` | `ACL_REVIEW_CMD` | `pi --provider zai --model glm-5.2` | REVIEW (CRITIC/VERIFIER) command |
| `--arbiter-cmd` | `ACL_ARBITER_CMD` | — *(unset = no arbiter)* | ARBITER (JUDGE) command, optional |
| `--max-loops` | — | `3` | Max FIX/VERIFY cycles |
| `--no-arbiter` | — | off | Skip arbitration; REJECT instead |
| `--timeout` | — | `600` | Per-subprocess timeout (s) |
| `--build-cmd` | — | — | Build gate run after BUILD (e.g. `cargo build`) |
| `--test-cmd` | — | — | Test gate run before merge (e.g. `cargo test`) |
| `--no-merge` | — | off | On approval, leave the loop branch unmerged |
| `--feature` | — | spec filename | Feature name used for branch + artifact dir |
| `--out` | — | `.adversarial-loop` | Artifact output directory (under `--workdir` if relative) |
| `--resume` | — | off | Resume from `state.json` |

> **Env-var support is limited by design.** As of v4.0.0 the orchestrator honors only the
> three command env vars above (`ACL_DEV_CMD`, `ACL_REVIEW_CMD`, `ACL_ARBITER_CMD`).
> `ACL_WORKDIR`, `ACL_MAX_LOOPS`, `ACL_TIMEOUT`, and `ACL_OUT_DIR` are **not** read by the
> current code — pass those values via flags. (The names are reserved so future releases
> can wire them without breaking existing invocations.)

REVIEW/VERIFY run with whatever sandbox the `--review-cmd` specifies. A reviewer must
**never** write to disk — keep its sandbox read-only / non-writing (see pitfall #1).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | **APPROVED** — squash-merged into the parent branch |
| `1` | **Infrastructure failure** — phase crash, timeout, git error, interrupt |
| `2` | **Usage error** — bad flag, missing/unreadable `--spec`, missing/bad `--workdir` |
| `3` | **REJECT** — findings unresolved after `--max-loops`, or `--build-cmd`/`--test-cmd` gate failed, or empty BUILD diff. Loop branch is preserved. |
| `4` | **ARBITRATED** — arbiter approved; conditions recorded in `final.json` |

Orchestrators consuming the pipeline should read `final.json` (the machine-readable
contract), not the exit code.

## Findings JSON schema

REVIEW output (one model call, validated by `phase_review._validate`; retried once on
malformed JSON):

```json
{
  "findings": [
    {"id": "A1",
     "severity": "blocker|major|minor|nit",
     "file": "path/to/file.rs",
     "line": 42,
     "summary": "Short title",
     "evidence": "Why it matters, referencing real code in the diff"}
  ],
  "verdict": "REQUEST_CHANGES|APPROVE|REJECT"
}
```

`line` must be an integer (numeric strings are tolerated). Findings lacking an `id`
receive a deterministic `auto_<hash>` id so VERIFY can track them across rounds.

VERIFY output (validates each finding's resolution against the current diff):

```json
{
  "results": [
    {"id": "A1", "status": "resolved|rejected|disputed"}
  ],
  "verdict": "APPROVE|REJECT"
}
```

- `resolved` — the problematic code is gone or corrected.
- `rejected` — the verifier disagrees with the original finding (it was wrong).
- `disputed` — unclear; stays open for the next round or the arbiter.

Approval requires `verdict == APPROVE` **and** every finding settled (`resolved` or
`rejected`). A finding the verifier `rejected` does not block approval.

## Artifacts

Emitted under `<--out>/<feature>/` (auto-appended to `.gitignore` so they never merge):

| File | Phase | Contents |
|------|-------|----------|
| `state.json` | 0 | Resumability: completed phases, current loop, branch, branch-point SHA, stash id, findings |
| `00_spec.txt` | 1 | Spec verbatim |
| `01_build.json` | 1 | BUILD result + commit SHA |
| `01_build_gate.json` | 1 | `--build-cmd` gate (if set) |
| `02_review.json` | 2 | Findings + verdict |
| `03_fix_<N>.json` | 3 | FIX round *N* result (one per loop) |
| `04_verdict_<N>.json` | 4 | VERIFY round *N* results + verdict (one per loop) |
| `05_arbiter.json` | 5 | Arbiter verdict + conditions (if run) |
| `06_test_gate.json` | 6 | `--test-cmd` gate (if set) |
| `final.md` | end | Human-readable summary (also the evidence-tag annotation) |
| `final.json` | end | **Machine-readable contract** — `verdict`, `reason`, `loops`, `branch`, `merged`, `conditions`, `arbitrated`, `artifacts_dir` |

## Git workflow

**Auto-init.** If `gitops.detect_enclosing_repo(workdir)` finds a parent repo, it is used
as-is. Otherwise `gitops.auto_init` initializes one (initial branch pinned to `main`).
The parent branch is the current branch (or `main` after auto-init).

**Dirty working tree.** `gitops.stash_dirty` runs `git stash push -u` at PHASE 0 and
records `stash@{0}` in `state.json`. The stash is popped on **every** exit path
(success, reject, interrupt) via `_restore`. If `git stash pop` hits a conflict (the
parent branch advanced and touched the same lines), the loop aborts with exit 1 and a
human must resolve — the stash is preserved, nothing is lost.

**Branch naming.** `loop/<sanitized-feature>/<N>`, where *N* is one more than the highest
existing `N` under that prefix (starts at 1). `--feature` is sanitized to a
branch-safe slug; default is the `--spec` filename stem.

**Commits.** BUILD commits `build: <feature> — <summary>`; each FIX round commits
`fix: <feature> — address finding(s) (round N)`. An empty BUILD diff is still committed
(empty commit allowed) but triggers an `EMPTY_DIFF` REJECT at REVIEW. Git identity
(`user.name`/`user.email`) is bootstrapped on the loop branch if unset.

**Reviews on diffs.** REVIEW and VERIFY receive `git diff <branch-point>..HEAD`, so they
see the cumulative change since the branch point — every BUILD + all FIX rounds — and
each finding must reference code that actually appears in the diff.

**Merge (APPROVED / ARBITRATED).** `gitops.squash_merge` checks out the parent branch,
runs `git merge --squash <loop-branch>`, commits `squash: <feature> — adversarial
approved`, and drops the loop branch. A merge conflict aborts with exit 1 and keeps the
loop branch. Before merging, `tag_with_evidence` creates an annotated tag
`<loop-branch>-approved` carrying `final.md` (best-effort — a missing file never blocks
the merge). `--no-merge` skips the merge and leaves the loop branch for human review.

**Reject (REJECT).** `gitops.reject_marker` records an empty
`[REJECTED] <feature> — <verdict>` commit on the loop branch. The branch is **not**
deleted and **not** merged, so the rejected work is recoverable.

## Language discipline

All internal pipeline text is **English**: spec files, auto-generated commit messages,
personas (`builder.md`, `critic.md`, …), findings JSON, verdicts, synthesis reports, and
code comments. User-facing summaries (what the orchestrator prints to you) stay in your
conversation language. Do not language-switch between roles inside the pipeline — it
confuses the model, especially in FIX where it receives an English persona + English
review + possibly a non-English spec.

## Personas

BUILDER / CRITIC / FIXER / VERIFIER / JUDGE live as text files in
`~/.hermes/skills/adversarial-common/personas/` (single source of truth, editable without
touching Python). All v4 personas are **git-aware**: BUILD produces committed code,
REVIEW inspects a diff, FIX commits a new round, VERIFY checks findings against the diff.
Injection is provider-aware: `pi` is detected and selects `builder-pi.md`/`fixer-pi.md`
(tool-based writes instead of markdown/JSON code output — mitigates the prose-overwrite
failure, pitfall #6).

## Examples (validated)

```bash
# Basic — Codex DEV + GLM-5.2 REVIEW, default flags.
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project

# Claude-as-DEV via claude-tmux (Fable 5 / Opus). Use ABSOLUTE paths — `~` expands
# relative to --workdir, not $HOME (pitfall #11). Extended thinking runs 8-12 min,
# so push --timeout up and keep the inner --hard-timeout >= the loop timeout.
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project \
  --dev-cmd "python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model best --timeout 900 --hard-timeout 2400 --max-turns 20" \
  --timeout 2400

# GLM-5.2 DEV + DeepSeek REVIEW (thinking high on both). No Claude quota needed.
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project \
  --dev-cmd  "pi -p --provider zai --model glm-5.2 --thinking high" \
  --review-cmd "pi -p --provider deepseek --model deepseek-v4-pro --thinking high" \
  --max-loops 2 --no-arbiter --timeout 1200

# With build + test gates and a named feature (Rust project).
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project --feature peer-auth \
  --build-cmd "cargo build" --test-cmd "cargo test" \
  --max-loops 3 --timeout 1800

# Arbiter on, no merge (human reviews the loop branch first).
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project \
  --arbiter-cmd "pi -p --provider gemini --model gemini-3-pro" --no-merge

# Resume after an interrupt (reads state.json under --out/<feature>/).
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/spec.md --workdir /path/to/project --resume
```

**Model pairing notes:** Codex is a fast first-choice DEV; GLM-5.2 (pi) reviews
thoroughly and reliably returns JSON; DeepSeek REVIEW is slower but finds more findings;
Claude (via tmux) is the most thorough reviewer but slowest and quota-bound. Codex
FIX often *cascades* beyond spec scope (migrates consumers, fixes adjacent bugs) — check
`git diff --stat` after every loop before assuming REJECT means the code is wrong.

## Pitfalls

1. **`--plan` mode is NOT wired into `adversarial_loop_v4.py`'s argparse.** The SKILL.md documents `--plan` mode (multi-step plan execution) but the actual Python code has no `--plan` argument. The `adversarial_loop.py` (which re-exports v4) only accepts `--spec`. The `phase_plan.py` module has `execute_step()` but no CLI entry point or plan-detection logic. **Symptom:** `adversarial_loop.py: error: the following arguments are required: --spec` when passing `--plan`. **Fix:** run each step as a separate code loop with `--spec` pointed at a focused spec for that step. Create individual specs from plan steps P1, P2, etc., and launch sequentially. Do NOT rely on the documented `--plan` mode — it is not implemented as of 2026-07-15. **Validated** 2026-07-15: `adversarial_loop.py --plan /tmp/plan.md` fails with `error: the following arguments are required: --spec`.

1b. **Codex `--sandbox read-only` vs `--dangerously-bypass-approvals-and-sandbox`.** When
   Codex is the REVIEWER and you add `--dangerously-bypass-approvals-and-sandbox`, it
   silently overrides `--sandbox read-only` to `--sandbox danger-full-access`, giving the
   reviewer write access — the opposite of what you want. **Fix:** for read-only review
   use `--sandbox read-only` **without** the bypass flag (interactive approval only);
   for a writing DEV use `--sandbox danger-full-access --dangerously-bypass-approvals-and-sandbox`
   together. In non-interactive mode the approval flag is required or Codex hangs.
2. **Bound the loop** with `--max-loops`. The arbiter settles the last disagreement; it
   does not extend the loop.
3. **GLM JSON wrapped in markdown — parsed in v4, not v3.** v3 used strict `json.loads`
   and choked on `` ```json ``-fenced output. v4's `jsonio.strip_json_wrapper` strips
   fences and extracts the largest JSON object, so GLM-5.2 / Claude markdown-wrapped JSON
   is now parsed. Since P6, every phase (including VERIFY) routes through the shared
   3-strategy parser `adversarial_common.jsonio.parse_json_output`, which tries:
   (1) markdown stripping, (2) extracting `{...}` via
   `text.find('{')`..`rfind('}')`, (3) extracting `[...]` for raw arrays. This makes the
   pipeline **model-agnostic** — the same code works regardless of whether the model
   returns raw JSON, markdown-wrapped JSON, text + JSON, or a JSON array. REVIEW/VERIFY
   still retry once on malformed JSON. If a model returns prose with no JSON object at
   all, the phase fails (exit 1) — check the captured stdout in the artifact.
4. **Claude extended thinking runs 8-12 min (Fable 5).** Pass
   `--timeout 900 --hard-timeout 2400` *inside* the claude-tmux command and keep the
   loop's `--timeout >= 2400`. The inner `--timeout` controls tmux pane inactivity
   detection — if Claude goes silent for more than this period, the pane is killed.
   With extended thinking, Claude can be silent for 12+ minutes even on small codebases
   (validated 2026-07-13 on a ~2580-line plugin: first review timed out at 600s).
   **Never use `--timeout 600` or lower** for Fable 5 REVIEW — the first pass always
   has the longest thinking burst as it reads the full diff and project structure. Set
   `--hard-timeout 2400` (40 min) to survive Verifier passes that require multiple file
   reads. If Claude repeatedly times out, switch `--review-cmd` to GLM-5.2
   (`pi -p --provider zai --model glm-5.2 --thinking high`), which is faster (no
   extended thinking) and equally reliable for JSON output. See
   `references/wrapper-failures.md`, `references/fable5-timeout-recovery.md`, and the
   `claude-tmux-wrapper` skill.
5. **Dirty working tree must be committed or stashed.** v4 auto-stashes at PHASE 0 and
   restores on every exit path, so a dirty tree no longer blocks startup. The remaining
   risk is a **stash-pop conflict**: if the parent branch advanced and touched the same
   lines you had stashed, `git stash pop` fails and the loop aborts (exit 1). The stash
   is preserved — resolve manually, then `--resume`.
6. **Models may overwrite source files with prose instead of code.** Claude/Fable 5,
   pi/GLM-5.2, and Codex have all been observed to replace working source with a markdown
   report or `<<<SEE BELOW>>>` placeholder. v4 mitigates this with pi-specific personas
   (`builder-pi.md`/`fixer-pi.md`, auto-selected when `pi` is detected) and is far easier
   to recover from than v3: `git checkout HEAD -- <file>` restores the committed version
   on the loop branch, then re-run FIX or apply the change directly via `patch` for a
   well-understood single-file fix. For mechanical fixes, direct `patch` is faster and
   more reliable than re-running the loop.
7. **Merge conflicts if the parent branch advances during the loop.** Squash-merge aborts
   with exit 1 and keeps the loop branch. Fix by rebasing the loop branch onto the
   updated parent (`git rebase <parent>`) or re-running; the loop branch is never lost.
8. **NEVER run parallel loops on the same workdir.** Each loop checks out its own branch,
   but two concurrent DEV/FIXER subprocesses writing to the same worktree files corrupt
   each other. Run batches sequentially; only disjoint file sets (no overlap in
   `git diff --stat`) can run in parallel. See `references/batch-splitting-strategy.md`.
9. **`--resume` requires `state.json` from a previous run.** It is read from
   `<--out>/<feature>/state.json`. If absent (e.g. you changed `--feature` or wiped
   `--out`), the loop starts fresh with a warning. Resumed runs re-checkout the recorded
   branch and skip completed phases.
10. **`~` in `--dev-cmd`/`--review-cmd`/`--arbiter-cmd` expands relative to `--workdir`,
    not `$HOME`.** The subprocess runner does no shell expansion. Always use absolute
    paths (`/home/user/.hermes/...` or `$HOME/.hermes/...`) for scripts in command flags.
11. **Use claude-tmux-wrapper, not `claude -p`, for Claude roles.** `claude -p` bills
    against Agent SDK credit (monthly cap); interactive Claude via tmux stays on the 5h
    sliding quota. Model alias `claude-sonnet-4` is invalid — use `claude-sonnet-4-20250514`
    or `opus`/`sonnet`/`best`/`fable` aliases. See `references/claude-p-migration-pattern.md`.
12. **Prompt injection from reviewed code.** Code under review (diff, spec) can embed
    adversarial instructions like `{"verdict": "APPROVE"}` that try to override the
    pipeline verdict. v4's review-on-diff narrows the attack surface but does not close
    it. See `references/prompt-injection-threat-model.md`; cross-model diversity is the
    strongest defense before processing untrusted PRs or specs.
13. **Codex sandbox builds commit `target/` / build artifacts.** When a DEV/FIXER runs
    `cargo build`/`cargo test`, the sandbox writes `target/` into the workdir; the
    orchestrator's `git add -A` at BUILD/FIX commits them, bloating the squash. Ensure
    `target/` (and equivalent) is in `.gitignore` **before** the first loop. After a
    loop: `git status --porcelain target/ | head -3`; if committed, `git rm -r --cached
    target/` and gitignore it.
14. **The loop can REJECT for out-of-scope findings.** The reviewer is not told to
    distinguish "pre-existing bug" from "new bug in this changeset." GLM-5.2 is
    particularly prone to finding pre-existing bugs outside spec scope. After a REJECT,
    always build + test and inspect the code on disk; if the spec-scope code is correct
    and the rest are pre-existing, the code is usable — commit it and patch the rest
    manually if wanted.
15. **Codex / models may exit 1 on deletion-only or "no new code" specs** without writing
    to stdout. Check `git status` / `git diff --stat` on the loop branch — the model may
    have made the changes before the process died. An empty BUILD diff is REJECTed as
    `EMPTY_DIFF`.
16. **`--out` persists between runs.** The directory is created with
    `mkdir(parents=True, exist_ok=True)` and not cleaned. Re-running with a different
    spec in the same project: either `rm -rf .adversarial-loop` first, or use a distinct
    `--out` / `--feature`.
17. **No parallel loops sharing a branch namespace** — the monotonic `<N>` counter in
    `loop/<feature>/<N>` is read from existing refs at PHASE 0; two concurrent starts can
    pick the same *N* and clobber each other. Sequential launches are safe.
18. **Codex / OpenAI quota exhaustion kills REVIEW silently.** Codex has usage limits,
    especially on free/Plus tiers. When exhausted (`ERROR: You've hit your usage limit`),
    the review phase exits 1 with no useful output. **Detection:** before a long loop,
    check quota with a quick CODE-only call (no reasoning). **Fallback:** switch
    `--review-cmd` to a non-OpenAI provider (GLM-5.2, DeepSeek, Claude). If Codex is the
    only reviewer configured, prepare a fallback inline or skip the review pass. Codex
    quota resets at the start of each month (OpenAI billing cycle). See
    `references/ai-quota-apis.md`.
19. **User preference: never say "I'll check back in X minutes" without actually doing
    it.** When monitoring a long-running loop, use an explicit polling loop
    (`for i in 1..N; do sleep 30; ls artifacts/; done`) or rely on
    `notify_on_complete=true`. Passive promises without follow-through frustrate
    the user. Either monitor actively with a polling loop, or say nothing and let the
    notification fire. See `references/monitoring-long-running-loops.md`.
    **Validated 2026-07-14:** the user called out the agent twice in one session for saying \"I'll check back\" without doing it. The agent said \"je revérifie dans 3 min\" and the reply was \"tu as encore menti\". This is a hard constraint: either launch a real polling loop now, or use notify_on_complete and stay silent. Never end a turn with a future-monitoring promise.

    **Concrete pattern that was validated:** launch with
    `terminal(background=true, notify_on_complete=true)` and do other work. When
    mid-run progress checks are needed, use a compact `for` loop with `sleep 30`
    that checks for specific artifact files (`02_review.json`, `loop_1_04_verdict.json`,
    `final.json`).
20. **DeepSeek via pi requires `~/.pi/agent/auth.json`.** Hermes stores the DeepSeek API
    key in `~/.hermes/.env` but does NOT export it to subprocesses. To use DeepSeek
    through `pi`, create `~/.pi/agent/auth.json` with: `{"deepseek": {"type": "api_key",
    "key": "<key>"}}`. Extract the key from Hermes via `grep DEEPSEEK_API_KEY
    ~/.hermes/.env` (the file has the actual key — Hermes masks it in terminal output
    but the file is readable by Python). Set permissions to `0600`. See
    `references/pi-auth-setup.md`.
21. **v3 pipeline does NOT parse markdown-fenced JSON.** GLM-5.2 and Claude routinely
    wrap JSON in ` ```json ... ``` ` fences. v3's strict `json.loads` fails silently
    (writes `{}` to artifact). v4's `jsonio.strip_json_wrapper` handles this. When using
    the v3 fallback (`adversarial_loop_v3.py`) with GLM/Claude as reviewer, either:
    (a) extract the JSON manually from the artifact file and re-save it, then `--resume`;
    or (b) switch the reviewer to a model that outputs raw JSON (DeepSeek V4 Pro
    reliably does this). Validated 2026-07-06.
22. **`terminal(background=true)` with `notify_on_complete=true` is the recommended
    monitoring pattern.** Long loops (5+ minutes per phase) should run in the background.
    The preferred approach: launch the loop with `background=true` +
    `notify_on_complete=true`, then work on other tasks. The notification fires
    automatically on completion. If you must monitor mid-run, use a compact polling
    loop: `for i in 1..N; do sleep 30; ls artifacts/; done`. Avoid idle waiting —
    do other work while the loop runs.
23. **DeepSeek V4 Pro VERIFY JSON can be malformed.** DeepSeek with `--thinking high`
    occasionally wraps JSON in additional markdown or text, causing
    `strip_json_wrapper` to fail extraction. The retry also fails because the model
    repeats the same wrapping. **Symptoms:** REVIEW succeeds (findings parsed), but
    VERIFY fails with "invalid JSON after retry". **Mitigation:** switch `--review-cmd`
    to a model that reliably outputs raw JSON (GLM-5.2 is more reliable for VERIFY).
    Or check the code on the loop branch manually — BUILD and FIX commits are correct
    even when VERIFY fails. Validated 2026-07-06 with GLM+DeepSeek pairing.
24. **Review prompt no longer concatenates code — model reads files directly from
    the loop branch checkout.** The review prompt is under 1K tokens. The reviewer
    runs `git diff HEAD~1..HEAD` to see changes and reads files with `cat`/`grep`
    for context. See `references/review-on-committed-code.md`.
25. **Multi-repo plan mode is experimental.** `_resolve_step_workdir()` detects the
    correct repo for each step's files. Only the BUILD phase runs in the resolved repo;
    plan git lifecycle stays in `--workdir`. If the step's files are already done,
    squash-merge may fail with "nothing to commit" — mitigated by `squash_merge()`
    falling back to `git merge --ff-only`.

25b. **Multi-repo --plan mode auto-initializes parent repo when `--workdir` has no `.git`.** 
    If `--workdir` is a parent directory containing multiple sibling git repos (e.g.
    `~/.hermes/skills` containing adversarial-common/, adversarial-code-loop/, etc.),
    the `--plan` pipeline's PHASE 0 calls `gitops.auto_init(workdir)` which creates a
    *parent* git repo. All BUILD commits then go into this parent repo as git submodule
    references, NOT into the individual skill repos. The steps report `"passed"` with
    `"0 loops"` (empty-diff approval) but the actual Python file changes are **not**
    committed to the skill repositories.
    **Symptom:** plan output shows `passed (0 loops)` for every step, but `git log` in
    each skill repo shows no new commits. The parent `--workdir` directory has a `.git`
    you didn't create, and `git status` shows the skill subdirectories as dirty submodule
    references.
    **Fix:** 
    1. Remove the auto-initialized parent repo: `rm -rf <workdir>/.git`
    2. In each skill repo, `git add -A && git commit -m "fix: ..."` to capture the changes
       that Codex wrote to disk but that the parent repo swallowed.
    3. Do NOT rely on `--plan` mode for multi-repo plans — use single-repo plans or run
       each step manually.
    **PREVENTION — automatic guard added 2026-07-15 in `gitops.auto_init()`:**
    `auto_init()` now calls `_has_child_repos(workdir)` before initializing. If the
    directory contains immediate subdirectories with their own `.git`, it raises
    `GitError` with a descriptive message listing the child repos and telling the caller
    to use `--workdir` on a specific repo instead. This prevents the parent-repo
    destruction from happening in the first place. The guard is in `adversarial-common`
    package at commit `df0a15e` and affects all pipelines (code-loop, plan, spec) that
    call `gitops.auto_init()`.
    **Validated 2026-07-15** on an 18-step cross-skill plan: steps A1–A14 all passed with
    0 loops but the code changes (embedded in the skill subdirectories) were not tracked
    in any skill repo. Only a `rm -rf .git` + manual `git add -A` per skill recovered the
    work.
26. **claude-tmux `--cwd` is REQUIRED when `--workdir` differs from the script's CWD (plan mode).** The adversarial loop script runs from `adversarial-code-loop/scripts/`, but reviews inspect the `--workdir` tree. claude-tmux spawns a tmux session whose CWD defaults to the *subprocess* CWD — which is the loop script's directory, NOT `--workdir`. Without `--cwd`, the reviewer runs `git diff` in the wrong repo and reports an empty diff even though the BUILD commit exists on the correct branch in `--workdir`. **Symptom:** REVIEW exits with 0 but findings say "the commit under review is empty" or "the worktree is checked out on `main`" — check whether claude-tmux has `--cwd`. **Fix:** always pass `--cwd <workdir>` to claude-tmux in the `--review-cmd` (and `--dev-cmd` if claude-tmux is the DEV). The claude-tmux `--cwd` flag translates to tmux's `-c` / `default-command` flag, setting the shell's working directory inside the session. Example:
   ```bash
   --review-cmd "python3 /path/to/claude-tmux.py --yolo --model best --timeout 900 --hard-timeout 2400 --cwd /home/user/plugins/hermes-quota-status"
   ```
   The `claude-tmux-wrapper` skill documents `--cwd` in its flag table but the adversarial loop pitfall #26 is the right place to warn callers. Without this, the first plan step always fails with an empty-diff finding and wastes a full loop round before you debug it.

27. **`--plan` file list format: one line, comma-separated.** Multi-line bullet lists
    under `Files:` and `Dependencies:` are NOT parsed by `parse_plan()`. Bad:
    `- **Files:**\\\\n  - /path/one`. parse_plan() accepts both comma-separated lists (`/path/a, /path/b`) and bracket lists (`[/path/a, /path/b]`). The adversarial-plan tool outputs bracket-list format.
    `parse_plan()` now rejects the bad shape with an explicit ValueError
    (surfaced as `X invalid plan ...`, exit 2) instead of silently dropping files.
28. **`gitops.create_branch()` required for --plan mode.** `phase_plan.execute_step()`
    calls `gitops.create_branch()` (not `create_loop_branch`). Add if missing.
29. **GLM-5.2 quota is 80 prompts per rolling 5h (Z.AI Lite).** HTTP 429 after 2-3
    heavy loops. Recovery: switch to DeepSeek V4 Pro (`pi -p --provider deepseek --model deepseek-v4-pro --thinking high`) for DEV, or Claude Sonnet for REVIEW. If all providers exhausted, wait 5h for GLM reset.
30. **User preference — monitor actively or stay silent.** Use polling loops or
    `notify_on_complete=true`. Never promise to "check back" without following through.
31. **User preference — quality over speed.** Always use `--thinking high`. Set generous
    timeouts (`--timeout 2400`). Accept 10-15 min BUILD times.
32. **Pipeline workdir == Hermes Agent install directory (fork-as-live-install).** When
    `--workdir` points at the Hermes Agent repo and Hermes is *running that checkout*, the
    pipeline's git operations (branch creation, checkout, squash-merge) operate on the live
    codebase. A squash-merge into the parent branch (typically `main`) without `--no-merge`
    commits the loop output directly into your running Hermes install — which can leave the
    install in an inconsistent state mid-change. **Always use `--no-merge`** so the loop
    branch stays isolated for human review and manual merge. After review, merge deliberately:
    `git checkout main && git merge --squash <loop-branch>`. Also, auto-stash of dirty trees
    (pitfall #5) is riskier here: a stash-pop conflict during the pipeline aborts with exit 1
    and leaves the working tree in a mixed state while Hermes is trying to run from those same
    files. Pre-commit or stash manually before launching. See `references/fork-as-live-install.md`.

33. **`.gitignore` auto-modification leaks into upstream PRs.** The pipeline's PHASE 0
    appends `--out` patterns (`.adversarial-loop/` by default) to `.gitignore` so artifacts
    never get tracked. This is correct for local development, but the `.gitignore` change
    ends up in every BUILD commit (via `git add -A`) and propagates into the squash merge.
    When the loop output is destined for an upstream PR, **drop the `.gitignore` delta before
    pushing**. After squash-merge into the parent branch: check with
    `git diff HEAD~1..HEAD -- .gitignore`; if it shows artifact patterns, restore the
    upstream version with `git checkout HEAD -- .gitignore` and amend:
    `git commit --amend --no-edit`. For `--no-merge` loops: inspect `.gitignore` before the
    manual merge — the upstream `.gitignore` likely already has `target/` etc., so a diff
    showing only `.adversarial-loop/`, `*.orig`, `*.rej` is the signal.
    See `references/pre-pr-cleanup.md`.

34. **Keep REVIEW/VERIFY timeout propagation wired end to end.** `run_review()` and
    `run_verify()` accept a `timeout` parameter and pass it to `providers.run_cmd()`;
    both call sites in `adversarial_loop.py` pass `timeout=args.timeout`. This makes the
    pipeline's `--timeout` apply to all five phases. Preserve all three links when
    changing phase signatures or dispatch. A regression causes Claude Fable 5 REVIEW or
    VERIFY to fail with `exit code 124: TIMEOUT after 600s` even when the caller passed
    `--timeout 2400`. See `references/fable5-timeout-recovery.md` for the validated
    reproduction and implementation details.

35. **claude-tmux wrapper must NOT modify the pipeline prompt.** The wrapper exists to\n    capture output via tmux (5h sliding quota) instead of `claude -p` (Agent SDK monthly\n    cap). Its only addition to the pipeline's stdin is the output-capture instruction —\n    *never* prepend or append behavioral modifiers like \"Do NOT run shell commands\" or\n    \"Output ONLY JSON\". The pipeline already sends those instructions. Adding them\n    duplicates (and can contradict) the pipeline's prompt, causing Claude to produce\n    prose instead of JSON or run commands the pipeline didn't want run.\n    **Symptom:** CHALLENGE phase fails with `\"invalid JSON after retry\"` because Claude\n    received conflicting instructions. **Fix:** the wrapper must use exactly this pattern:\n    ```\n    prompt += f\"\\n\\nWhen you are done, write your response to {output_file} \"\n    prompt += f\"using the Write tool. After the file is written, create an empty \"\n    prompt += f\"file at {done_sentinel} to signal completion.\"\n    ```\n    No `+ prompt = \"Do NOT...\"` or `+ prompt += \"Write the exact output...\"`.\n    See `references/claude-tmux-prompt-hygiene.md` for the validated v2 wrapper\n    implementation and restoration instructions.\n\n36. **Plan-mode reviewer model fallback mid-pipeline.** When running a multi-step `--plan`
    pipeline and the REVIEW model hits quota exhaustion (Claude's 5h sliding window or
    Codex's monthly cap), the pipeline must be killed, artifacts cleaned, and relaunched
    with a different `--review-cmd`. **Procedure (validated 2026-07-13):**
    1. Kill the background process (`process(action="kill", session_id=...)`).
    2. Clean artifacts: `rm -rf .adversarial-loop` and delete orphaned loop branches
       (`git branch -D loop/<feature>/<step>/<N>`). Return to `main`.
    3. Create a **reduced plan** with only the remaining steps and `Dependencies: []`
       for any dep that was already merged (the parser validates against the plan's own
       step IDs). Use a different `--feature` name.
    4. Relaunch with `--review-cmd` pointing to the fallback model:
       - **Claude → GLM-5.2**: `pi -p --provider zai --model glm-5.2 --thinking high`
         (no `--cwd` needed — pi's own file tools handle it).
       - **GLM/DeepSeek → Claude**: the reverse.
       - **Stop entirely** if both Codex (DEV) and GLM (review fallback) quotas are
         exhausted — no viable agent combo remains; resume later.
    5. The user's instruction for this session: "si quota Claude epuisé → GLM, si GLM ou
       Codex epuisé → STOP, on reprend plus tard."
    GLM-5.2 reviews are ~3x faster than Claude Fable 5 (no extended thinking) and
    reliably output JSON, making it the preferred fallback for REVIEW when Claude quota
    is low. Validated on 7 completed steps with Claude + 1 attempted step (P8, timeout)
    before switching to GLM.

36. **Plan resume after partial completion: always check step count.** The plan orchestrator
    emits `"status": "passed"` for completed steps and `"status": "skipped"` for the rest.
    When creating a reduced plan:
    - Include only the remaining steps in document order.
    - Set `Dependencies: []` for steps whose original dependencies are already merged.
    - Steps in the reduced plan can be renumbered or keep original IDs — IDs are scoped to
      the plan file, and `validate_steps()` only checks that referenced dep IDs exist within
      it. Original IDs make it easier to cross-reference with the adversarial-review findings.
    - **Risks:** P14 and P15 were split from P8 and P10 during adversarial-plan, so their
      IDs are out of sequence (P14 after P8, P15 after P10). Keep the original numbering
      to avoid confusion with the finding-to-step map.

37. **Squash commit naming for upstream PRs.** The pipeline's merge commit message format is
    `"squash: <feature> — adversarial approved"` (individual BUILD commits use
    `"build: <feature> — <summary>"`, FIX commits use
    `"fix: <feature> — address finding(s) (round N)"`). These are pipeline-internal names
    that don't follow conventional commits, and upstream reviewers will flag them. After
    squash-merge into the parent branch, rewrite the squash commit:
    `git commit --amend -m "feat(cli): add on_status_bar_render hook to narrow width tier"`.
    For multi-step plan outputs with several squashes stacked, either rebase and reword each,
    or squash them all into one conventional-format commit before pushing the branch upstream.
    Always verify the final commit message with `git log --oneline -1` before `git push`.
    See `references/pre-pr-cleanup.md`.

38. **`pi` (GLM-5.2) can review the wrong git repo despite correct `cwd`.** Unlike claude-tmux (which needs `--cwd`, pitfall #26), `pi` runs inside `subprocess.Popen(cwd=workdir)` — but its internal file-access tools may navigate to a different repository. **Symptom:** the REVIEW finding references a commit hash and file paths that don't exist in `--workdir` (e.g., from `hermes-agent` instead of a plugin repo), claiming an empty diff. **Diagnosis:** check `02_review.json` — if the `"file"` field says `"(commit 92ce650...)"` instead of a real file path in your project, pi is in the wrong repo. **Workaround:** merge the BUILD manually (`git merge --squash <loop-branch>`); the code on the loop branch is correct, only the review was misdirected. This was validated 2026-07-13 on a 320-line keyring-hardening step where GLM reviewed the hermes-agent repo instead of the plugin repo. After manual merge the code compiled and all 149 tests passed.

39. **Fable 5 has its own usage limit separate from Claude Pro's 5h sliding quota.** The model can be blocked even when regular Claude Pro quota is green. **Symptom:** claude-tmux starts, bypasses permissions, reads the prompt, then displays "You've reached your Fable 5 limit" and stops. **Fix:** switch to `--model sonnet` or `--model opus`. Sonnet is preferred for plan-challenger and code-loop REVIEW because it has no extended thinking (faster response, no 12-min silence), reliable JSON output, and lower token cost. See `references/fable5-usage-limit.md`. **Validated:** 2026-07-15 — Fable 5 hit limit mid-challenge; Sonnet completed in ~2 min.

40. Codex FIX phase hangs on stdin when the spec is small or findings are minor. Codex prints Reading prompt from stdin... and blocks forever when its generated input does not constitute a complete code-generation request. Symptom: BUILD succeeds, REVIEW returns findings, but FIX exits 1 with Reading prompt from stdin... as the only output. Root cause: the FIX phase embeds findings into a prompt Codex expects to be a full coding task; narrow specs with minor findings can leave Codex waiting. Mitigation (validated 2026-07-15): switch --dev-cmd from Codex to GLM-5.2 (pi -p --provider zai --model glm-5.2 --thinking high) for the problematic step. GLM reliably handles FIX prompts without stdin hang. Permanent fix: ensure the FIX prompt always includes a concrete code-generation request with file paths and expected diff pattern.

v3 is preserved verbatim as `scripts/adversarial_loop_v3.py` for one release. To migrate:

**New flags:** `--build-cmd`, `--test-cmd` (objective gates), `--no-merge`, `--feature`,
`--resume`, `--no-arbiter`.

**Changed defaults:**
- `--dev-cmd` default is now `codex exec --skip-git-repo-check --sandbox workspace-write`
  (v3 used a claude-tmux default).
- `--review-cmd` default is now `pi --provider zai --model glm-5.2` (v3 used a Codex
  read-only default).

**Changed exit codes:**
- `4` is now **ARBITRATED** (v3 used it for CODE_NEEDS_FIXES).
- `5` (ARBITRATED/indeterminate) is removed; an unparseable arbiter verdict now fails the
  phase (exit 1).

**Changed findings schema:** findings now require `{id, severity, file, line, summary,
evidence}` with an integer `line`, plus a top-level `verdict` of
`REQUEST_CHANGES|APPROVE|REJECT`. v3 used `{id, severity, file, line, description}` plus
optional `status`. VERIFY results use `{id, status: resolved|rejected|disputed}`.

**Changed artifacts:** filenames are now numbered without a `loop_N_` prefix (`02_review.json`,
`03_fix_<N>.json`, `04_verdict_<N>.json`); the contract file is still `final.json`.

**Behavior changes:** the orchestrator no longer extracts code from prose — the DEV model
must write files directly (git-aware personas enforce this). Dirty trees are auto-stashed,
not fatal. Empty BUILD diffs are REJECTed as `EMPTY_DIFF`. Review input is a git diff, not
a stdin concatenation.

## Resuming a failed --plan pipeline

When a multi-step `--plan` pipeline exits code 3 (REJECT) mid-way, the completed
steps are already squash-merged into the parent branch. To resume:

1. **Check completed steps:** `git log --oneline -5` shows squash commits for each
   approved step.
2. **Check the plan orchestrator state** at `<--out>/<feature>/state.json`. If
   `verdict: REJECT` and no steps show `status: completed`, the first step failed
   before any merge — no salvage is needed, just clean and retry.
3. **Clean up on first-step failure** (nothing was merged): delete the artifacts
   directory (`rm -rf .adversarial-loop`), delete the orphaned loop branch
   (`git branch -D loop/<feature>/<step>/<N>`), and verify `.gitignore` was not
   polluted by the pipeline's auto-append. Then relaunch fresh with the fix
   (typically increased timeouts — see pitfall #4).
4. **Create a reduced plan for mid-plan failure:** copy the remaining steps from
   the original plan into a new file. Remove all dependencies on already-merged
   steps by setting them to `[]` — the plan parser validates dependencies against
   the plan's own step IDs only.
5. **Relaunch with reduced plan:** use the same `--dev-cmd`, `--review-cmd`, and
   `--out`. Use a different `--feature` name (e.g. `--feature my-feature-rest`)
   to avoid branch collisions with the rejected run.
6. **Build + test gates:** always include `--build-cmd` and `--test-cmd` to catch
   breakage early.
7. **If Claude times out on VERIFY:** switch `--review-cmd` from claude-tmux to
   GLM-5.2 (`pi -p --provider zai --model glm-5.2 --thinking high`). GLM is ~3x
   faster per phase, has no extended-thinking pauses, and has been validated across
   6+ code loop steps.

### Validated model pairings (2026-07)

| Role | Primary | Fallback (quota/timeout) |
|------|---------|--------------------------|
| DEV (BUILD/FIX) | `codex exec -c model='gpt-5.6-sol' -c model_reasoning_effort='high'` | `pi -p --provider zai --model glm-5.2 --thinking high` → `pi -p --provider deepseek --model deepseek-v4-pro --thinking high` |
| REVIEW (CRITIC/VERIFIER) | Claude Sonnet via tmux (`--timeout 1200 --hard-timeout 1800`) | GLM-5.2 (`pi -p --provider zai --model glm-5.2`) |
| CHALLENGER (spec/plan) | Claude Sonnet via tmux (reads files from disk, no prompt embedding) | DeepSeek V4 Pro (`pi -p --provider deepseek --model deepseek-v4-pro --thinking high`) or GLM-5.2 |

**Key changes from 2026-07-14:**
- **Claude Sonnet replaces Fable 5 as default REVIEW/CHALLENGER.** Fable 5 has a separate usage limit from Claude Pro's 5h quota, frequently blocks mid-run, and its extended thinking (8-12 min) makes plan-challenge timeouts common. Sonnet has no extended thinking, responds in ~2 min, produces reliable JSON, and stays on the 5h sliding quota.
- **DeepSeek V4 Pro is validated as DEV fallback.** When GLM-5.2 hits its 80-prompt/5h limit (Z.AI Lite), DeepSeek V4 Pro with `--thinking high` works as a reliable DEV for BUILD and FIX phases. Validated on P16 (plan integration): BUILD created a valid commit on first attempt.
- **DEV fallback chain:** Codex → GLM-5.2 → DeepSeek V4 Pro. If all three are exhausted, stop and resume later.

All three pairings validated end-to-end on 10-step plan across all 3 adversarial
stages (spec → plan → code loop), 1 cycle each. Claude succeeded at both the
files-on-disk review pattern (code loop) and the embedded-prompt JSON pattern
(spec/plan challenger).

## Retrospective logging

Every pipeline failure is automatically logged to `_retrospective/ISSUES.md` with:

- Phase name, branch, error message, and last 200 chars of stdout
- Date/time of failure
- Feature name from the spec

Review issues before planning v5:

```bash
cat ~/.hermes/skills/adversarial-code-loop/_retrospective/ISSUES.md
```

To manually add a note about a limitation you noticed, add an entry at the top of
`_retrospective/ISSUES.md` following the same format:

```markdown
### YYYY-MM-DD — Short title

- **Model combo:** GLM/DeepSeek/Claude/Codex + (role)
- **Symptom:** What went wrong
- **Root cause:** Why it happened
- **Fix/workaround:** How you worked around it
- **Would fix in v5 by:** Concrete design change
```

## --plan mode (multi-step plans)

**⚠️ NOT WIRED — see pitfall #1.** The `--plan` flag does not exist in the actual argparse of `adversarial_loop.py` or `adversarial_loop_v4.py`. The `phase_plan.py` module has `execute_step()` but no CLI entry point or plan-detection logic. Run each plan step as a separate code loop with `--spec` pointed at a focused spec for that step.

The documentation below describes the intended design that would be implemented in v5. It is preserved here for future implementation references.

Plan format (output of `adversarial-plan`):

```
### P1: Step title
- **Files:** /path/to/file1, /path/to/file2
- **Description:** What to implement
- **Dependencies:** []
- **Tests:** How to verify
- **Risks:** What could go wrong
```

**Format rules:** Files MUST be on a single line (comma-separated), NOT multi-line
bullet lists (`phase_plan.parse_plan` reads bullet keys by line only).
Dependencies MUST reference existing step IDs.

**Multi-repo support:** Each step's files are inspected by
`_resolve_step_workdir()` which detects the enclosing git repo. If all files belong
to the same repo, the step runs there. This allows cross-skill refactoring.
**Experimental** — see pitfalls #25 and #25b. The primary risk: if `--workdir` has no
`.git` of its own, the pipeline auto-initializes one and commits submodule references
instead of actual code changes. Prefer single-repo plans where possible.

## Changelog

- **v4.1.0** (2026-07-14): Added pitfalls #25b (multi-repo parent repo auto-init), #26 (claude-tmux --cwd required), #34 (REVIEW/VERIFY timeout propagation), #35 (mid-pipeline model fallback), #36 (plan resume after partial completion), #38 (pi wrong repo review). Added references: fable5-timeout-recovery.md, github-secret-scanning-bypass.md, delegate-task-review-timeout.md. Fixed `run_review()` and `run_verify()` timeout propagation bug (timeout defaulted to 600s despite pipeline `--timeout`). Fixed `_fail_phase` → `fail_phase` bug in adversarial_review.py (underscore prefix leaked from runner module).
