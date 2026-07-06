# Fix claude-tmux-wrapper v1.5.1 — Adversarial Review Findings

## Target file
`/home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py`

## Context
This is the claude-tmux wrapper script (495 lines, Python 3). It spawns Claude in a tmux session, appends a "write your output to file X, then create X.done" instruction to the prompt, and waits for the model to complete. An adversarial review (Fable 5 + Codex) identified 16 findings including 2 blockers.

## What to fix

### Blocker 1 — Done marker in shell echo causes premature completion detection
The shell command at line 243 echoes the `done_marker` string into the pane as part of the command text:
```python
cmd = f"{shlex.join(cmd_parts)} < {shlex.quote(prompt_file)}; printf '\\\\n{done_marker}:%s\\\\n' \\\"$?\\\""
```
This causes `done_marker in pane` (line 313) to match BEFORE Claude even starts processing, because the echoed command text sits in the pane. The wrapper then counts 3 idle polls and exits with strict-mode failure (exit 5), since the output file hasn't been written yet.

**Fix:** Omit the `done_marker` from the echoed command text. Instead, use a shell technique that does not echo the marker. Options:
- Use `subprocess.run()` to check Claude's exit status separately after the tmux session ends, rather than embedding it in the shell command
- Or use `tmux send-keys -l` (literal) to avoid echo, and check exit via a separate mechanism
- Or simply remove the exit status marker entirely and rely on the file/done protocol (which is now reliable with v1.5.0)

### Blocker 2 (cross-validated) — Timeout git-status fallback returns success with empty stdout
In the timeout path (lines 329-343), the wrapper runs `git status --porcelain` and if the workdir is dirty, it prints a note to stderr and returns exit 0:
```python
if git_changed:
    print("Note: modèle a écrit directement sur le disque ...", file=sys.stderr)
    return 0
```
This means a real timeout (model hung, never produced output) is silently converted to success if the git working directory happens to be dirty. The caller receives empty stdout and exit 0 — indistinguishable from a real successful run.

**Fix:** Exit code 3 (timeout) should be returned regardless of git dirtiness. The note about git changes can remain on stderr for diagnostics, but the exit code must be 3. Callers can always check `git diff --stat` themselves.

### Major — output.strip() corrupts whitespace-sensitive responses
Line 373: `sys.stdout.write(file_response.strip())` strips leading/trailing whitespace from the model's output. For JSON responses this is harmless, but for any whitespace-sensitive output (YAML, indented code blocks, etc.), the content is corrupted.

**Fix:** Use `sys.stdout.write(file_response)` without `.strip()`. Same for line 405 and 463.

### Major — Default --no-danger stalls on Write permission dialogs
The default mode (`--no-danger`) does not auto-confirm dialogs. Claude will prompt "Claude Code wants to write to X" — a permission dialog that blocks until the user clicks "Yes". The wrapper sits in its poll loop forever, waiting for the file/done protocol to complete, never getting it.

**Fix:** Detect the Write permission dialog text in the pane and auto-confirm it with "y\n" or the appropriate keystroke, even in --no-danger mode. The trust dialog and bypass-permissions dialogs should remain behind --yolo, but the Write permission prompt is a routine operational dialog that should always be auto-confirmed.

### Major — .done sentinel path is attacker-creatable in /tmp
`done_file = output_file + ".done"` (line 194) where `output_file` is in `/tmp` (from `mkstemp`). While the output file itself is secure (0600, non-guessable name from mkstemp), the `.done` suffix path is predictable. An attacker (or another process) can create `{output_file}.done` before the model does, causing a false completion signal.

**Fix:** Write the .done sentinel to a path under a private directory, not as a sibling of the output file. Options:
- Create the done file with mkstemp as well, and include its path in the instruction to the model
- Or create a private temp directory with `tempfile.mkdtemp()` and place both files there
- Or use `os.open()` with `O_CREAT | O_EXCL` to atomically create the done file, failing if it already exists

### Major — 'trust' substring match too broad
Line 259: `if wait_for_pane_text(session, "trust", timeout=10):` matches any pane content containing "trust" anywhere. The comment says it targets the workspace trust dialog, but the substring is too generic — any tool output, error message, or even model output mentioning "trust" would trigger a blind Enter.

**Fix:** Match a more specific phrase from the actual dialog, e.g. "Is this a project you created or one you trust?" or "Yes, I trust this folder". Use `wait_for_pane_text` with a longer unique substring.

### Major — tmux failure handling inconsistent
Various tmux subprocess calls (lines 218-225, 244-246, 259-267) can raise `CalledProcessError` or `TimeoutExpired`. Some are caught by the generic `except subprocess.CalledProcessError` at line 467, but others (like the `has-session` check at line 218-221) are uncaught.

**Fix:** Wrap ALL tmux subprocess calls in try/except. On failure, print the error to stderr and exit 2 (usage/tmux error).

### Major — Dead code: busy variable computed but never used
With the v1.5.0 fix, `busy = pane_is_busy(tail)` (line 297) is computed every poll iteration but never referenced. The function `pane_is_busy()` and the `SPINNER_INDICATORS` tuple are dead code.

**Fix:** Remove the `busy = pane_is_busy(tail)` line, the `pane_is_busy()` function, and the `SPINNER_INDICATORS`/`TOKEN_COUNTER_RE` constants. If they might be useful for debugging later, keep them but comment out.

### Minor — os.chdir mutates global process state
Line 232: `os.chdir(args.cwd)` changes the working directory of the entire wrapper process. The tmux `-c` flag (line 233) already sets the pane's starting directory. The `os.chdir` is redundant and affects subsequent file operations.

**Fix:** Remove the `os.chdir(args.cwd)` call. The tmux `-c` flag is sufficient.

### Minor — --session-name can kill pre-existing sessions
Lines 218-226 check for an existing session with the given name and kill it before creating a new one. If someone reuses a session name (e.g., `review`), an unrelated session with the same name is destroyed.

**Fix:** If the session already exists AND was created by a previous wrapper invocation (check via a marker file or PID), kill and recreate. If it's an unrelated session, print an error and exit 2.

### Minor — Stale SPINNER_INDICATORS and related code
If `busy` is removed, the spinner indicators and token counter regex are only referenced by `pane_is_busy()` which becomes dead code. Same for `TOKEN_COUNTER_RE`.

**Fix:** Remove all of them.

## Non-goals for this fix
- Do NOT change the file/done protocol architecture (it works reliably with v1.5.0)
- Do NOT change the `--strict`/`--no-strict` behavior
- Do NOT refactor the argparse configuration
- Do NOT change the manual tmux orchestration section in SKILL.md
- Focus only on the script (`claude-tmux.py`), not SKILL.md or references

## Verification
After each change, verify syntax with: `python3 -c "import ast; ast.parse(open('/home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py').read())"`
At the end, verify the script still runs with: `echo "test" | python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --help`
