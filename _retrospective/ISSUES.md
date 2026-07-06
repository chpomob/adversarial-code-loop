# What adversarial-code-loop v4 SHOULD do better

This is the retrospective / technical-debt tracker for the v4 skill.
Every time you hit a limitation or bug with the v4 pipeline, note it
here with enough detail to inform a v5 redesign.

---

## How to report an issue

Add a new entry at the top:

```markdown
### YYYY-MM-DD — Short title

- **Model combo:** GLM/DeepSeek/Claude/Codex + (role)
- **Symptom:** What went wrong (timeout, wrong output, crash, etc.)
- **Root cause:** Why it happened (bursting at the seams in a module, missing feature, design mistake)
- **Fix/workaround:** How you worked around it
- **Would fix in v5 by:** Concrete design change
```

---

## Issues encountered

### 2026-07-06 — Verify JSON parsing fails when model wraps output in markdown

- **Model combo:** DeepSeek (verify)
- **Symptom:** verify_1 failed: invalid JSON after retry
- **Root cause:** `strip_json_wrapper()` existed in jsonio but was not the first attempt in the verify path. The verify phase also sent the diff as stdin text (heavy) instead of letting the model explore the workdir.
- **Fix/workaround:** Rewrote `phase_verify.py` with `_try_parse_json()` — 3 extraction strategies (strip markdown, extract {}, extract []). Also changed verify to use code-on-disk like review (model runs `git diff HEAD~1..HEAD` itself).
- **Would fix in v5 by:** Don't send diffs to models at all — always let them explore the workdir with git.

### 2026-07-06 — Codex sandbox flags conflict in --review-cmd

- **Model combo:** Codex (review)
- **Symptom:** CRITIQUE phase failed (exit 1) — Codex asked for approval on read-only sandbox
- **Root cause:** `--sandbox read-only --dangerously-bypass-approvals-and-sandbox` is a contradictory flag combination. Codex CLI ignores `--dangerously-bypass` when `read-only` is set, falling back to approval-required mode.
- **Fix/workaround:** Use `--sandbox danger-full-access` for review roles (no read-only mode). Documented in SKILL.md pitfalls.
- **Would fix in v5 by:** Abstract sandbox configuration per role (review = read-only by default, with a distinct approval bypass path).

### 2026-07-06 — Pipeline v3 uses concurred file-write protocol causing file corruption

- **Model combo:** Codex (DEV/FIX)
- **Symptom:** Codex wrote prose to server/src/main.rs instead of code, destroying 1108 lines
- **Root cause:** The v3 pipeline forces the model to communicate through stdout and then writes that stdout to disk. If the model outputs prose (description of the fix) instead of diff/patch, the file is overwritten with prose.
- **Fix/workaround:** The v4 git workflow solves this: models write files to disk, and the orchestrator stages+commits. If a model writes prose, `git checkout HEAD` restores instantly.
- **Would fix in v5 by:** Already fixed in v4 (git-workflow). Structural fix: never let model output (stdout) become source code.

### 2026-07-06 — GLM --thinking high takes 8-12+ minutes with no progress feedback

- **Model combo:** GLM-5.2 (all roles)
- **Symptom:** Pipeline appears frozen for 5-10 minutes. No files written, no output.
- **Root cause:** GLM with `--thinking high` does extended reasoning before producing any output. The pipeline's stdout-only output model gives zero feedback during this time.
- **Fix/workaround:** Accept the delay as a quality trade-off. Add periodic "still thinking" logging.
- **Would fix in v5 by:** Add heartbeat/timeout predictions based on prompt size. Show "estimated thinking time: ~N min" before starting.

### 2026-07-06 — Claude (tmux) in FIX role timeouts on large refactors

- **Model combo:** Claude (FIX)
- **Symptom:** FIX phase exits with code 3 after extended thinking
- **Root cause:** Claude's extended thinking (8-12 min for Fable 5) hits the hard-timeout when the fix requires large code changes. The tmux wrapper's `--max-turns` and `--hard-timeout` are not propagated correctly from the pipeline.
- **Fix/workaround:** Increased hard-timeout to 1200s, max-turns to 25. For small fixes, prefer GLM or direct patches.
- **Would fix in v5 by:** Phase-level timeout configuration per role. Adaptive timeouts based on prompt size.

### 2026-07-06 — Findings JSON schema mismatch between write and storage

- **Model combo:** GLM (review)
- **Symptom:** GLM wraps JSON in markdown code fences, which the v3 pipeline cannot parse
- **Root cause:** The v3 review phase feeds the entire codebase as stdin text, producing a large prompt. Models naturally wrap their JSON output in markdown fences.
- **Fix/workaround:** `jsonio.strip_json_wrapper()` strips markdown fences. The v4 pipeline uses this in both review and verify.
- **Would fix in v5 by:** Use structured output (tools/function calling) instead of parsing model-generated JSON.

### 2026-07-06 — No progress feedback during async model calls

- **Model combo:** All
- **Symptom:** User doesn't know if the pipeline is building, thinking, or crashed
- **Root cause:** The orchestrator runs models as subprocesses and only outputs after they complete. No periodic "phase X is running for N minutes" messages.
- **Fix/workaround:** Use external terminal monitoring (tmux capture, artifact file polling).
- **Would fix in v5 by:** Add a progress callback / heartbeat system. Pipeline periodically prints phase + elapsed time.

### 2026-07-06 — Resume is fragile (state.json can be incomplete)

- **Model combo:** Infrastructure
- **Symptom:** `--resume` can crash if state.json is missing keys like `parent_branch`
- **Root cause:** The state.json format evolved during development but backward compatibility wasn't maintained.
- **Fix/workaround:** Added fallback lookups and error paths. Documented "only resume from the same v4 version that wrote the state."
- **Would fix in v5 by:** Versioned state schema. `state.version` field used for migration/compatibility checks.

### 2026-07-06 — build failed for unknown

- **Phase:** build
- **Branch:** loop/acl-spec-mi5q/1
- **Error:** DEV exited 124: TIMEOUT after 1s
- **Stdout (last 200 chars):** ''
- **Auto-logged by pipeline**

### 2026-07-06 — Timeouts fixes sont inutiles, le progrès devrait décider

- **Symptom:** Claude/Fable 5 (extended thinking 15-20 min) tué par le timeout alors qu'il produit. Codex bloqué sur quota non détecté.
- **Root cause:** Le timeout est un mur absolu. Aucune distinction entre "réfléchit" et "bloqué".
- **Would fix in v5 by:** Pas de timeout. Boucle de monitoring : si le process est vivant ET produit (stdout, fichiers, tmux), on attend. Si mort ou quota detecté dans le stdout/stderr, on arrête. Infini patience pour la réflexion, réaction instantanée pour les échecs.
