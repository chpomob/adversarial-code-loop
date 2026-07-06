# GLM-5.2 / pi Prose-Writing Failure Mode

## The Problem
When pi/GLM-5.2 is used as DEV or FIXER in the adversarial loop, it may write prose/markdown reports INTO the source files instead of executable code. Observed cases:
- Replaced `src/main.cpp` (1214 lines of working C) with `<<<SEE BELOW>>>` placeholder (2026-07-01)
- Wrote a 38KB implementation report into `controller.js` instead of JS code

## Root Cause
The standard BUILDER and FIXER personas are designed for models that output code via stdout. They say things like: "If file writes are refused, produce the complete source code inline in markdown code blocks" and "your output must include an `updated_code` field containing the complete corrected source code."

pi/GLM-5.2 interprets these instructions literally — it writes the report/markdown INTO the file via its Write tool, treating the instruction as "write this to the file" rather than "output this as your response."

## Fix (adversarial-common v1.2.0+)
Provider-specific personas `builder-pi.md` and `fixer-pi.md` are auto-selected when `pi` is detected as the provider. These tell the model to use its Write/Bash/Edit tools to modify files directly. Key instruction changes:
- BUILDER: "Use your Write tool to modify the EXACT source files — do NOT propose changes, APPLY them"
- FIXER: "Use your Write tool to modify source files DIRECTLY — apply each fix"

## Detection
The loop script uses `persona_for_role("builder", dev_cmd)` which calls `detect_provider(cmd)`. When the command contains `pi` (checked via `"pi " in cmd or cmd.strip() == "pi" or "/pi " in cmd`), the pi-specific persona is loaded.

## Recovery from Prose Overwrites
1. `git checkout <corrupted_file>` to restore
2. Re-run with `--dir` mode and explicit anti-prose spec
3. If pi still fails after 2 attempts, switch to Codex as DEV
4. For simple well-understood fixes, use `patch` tool directly (bypasses adversarial loop)

## Also: pi does NOT support `--project-dir` mode
pi does not implement the sentinel file protocol (`done.sentinel` + output file). Always use `--dir` or `--stdin` when pi is a DEV/FIXER model.
