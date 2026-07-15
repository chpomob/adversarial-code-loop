# claude-tmux adversarial review findings (2026-07-14)

These findings were produced by an adversarial review (Codex Architect + GLM-5.2 Inspector)
of `claude-tmux.py` on 2026-07-14. All have been fixed.

## Fixed findings

### 1. Hard timeout returned 0 instead of 3 (blocker)
**Problem:** When `--hard-timeout` expired, `hard_timeout_hit=True` was set but never consumed.
Execution fell through to the success path and returned 0 with partial output — indistinguishable
from a clean completion.

**Fix:** Added `if hard_timeout_hit: print("hard-timeout", file=sys.stderr); return 3` before
the success return path. The soft-timeout path (--timeout) already returned 3.

### 2. Hardcoded username `chpo@` in pane-heuristic finish detection (blocker)
**Problem:** `wait_for_pane_text(session, "chpo@", ...)` would fail for any user other than `chpo`.

**Fix:** Removed `"chpo@"` from the pane fallback pattern. The `\u276f` (❯) prompt indicator alone
is sufficient. Additionally added `re.search(r"[#$%>]\s*\Z", prompt_line)` as a generic shell-prompt
detector for covering other prompt styles.

### 3. Output file read before done.sentinel confirmation (major)
**Problem:** `output.txt` was returned as success whenever nonempty, even before `done.sentinel`
existed. This meant partial output from a still-running Claude could count as complete.

**Fix:** The wait loop now checks `done_sentinel` BEFORE the `hard_deadline` check:
```python
if os.path.exists(done_sentinel):
    break
if hard_deadline ...:
    hard_timeout_hit = True
    break
```
The pane heuristic was also demoted to a last-resort fallback (`pane_fallback_ready` flag)
rather than breaking the loop immediately.

### 4. Trust-dialog detection matched bare word `"trust"` (major)
**Problem:** `wait_for_pane_text(session, "trust", timeout=10)` would fire on any pane content
containing "trust" — not just the Claude startup dialog.

**Fix:** Replaced with `"trust the files"` (the actual Claude dialog text). Also replaced the
"bypass permissions" match with a more specific substring.

### 5. Dead code: duplicate `read_nonempty_file(output_file)` (minor)
**Problem:** Two consecutive identical reads of `output_file` — the second was unreachable.

**Fix:** Removed the second block.

## Design principle (critical)

The claude-tmux wrapper must **never** modify the pipeline prompt. Its only addition to stdin
is the output-capture instruction. Specifically:

**DO NOT** prepend or append behavioral modifiers:
- "Do NOT run shell commands"
- "Output ONLY JSON"  
- "You MUST respond with raw JSON"

The pipeline already sends those instructions. Adding them causes Claude to produce prose
instead of JSON or run commands the pipeline didn't request.

**The correct pattern:**
```python
prompt += (
    f"\n\nWhen you are done, write your response to {output_file} "
    f"using the Write tool. "
    f"After the file is written, create an empty file at {done_sentinel} "
    f"using the Write tool to signal completion."
)
```

## Model aliases (claude-tmux)

| Alias | Maps to | Notes |
|-------|---------|-------|
| `opus` | Claude Opus | Stable, no extended thinking |
| `sonnet` | Claude Sonnet 4 | Good balance. No extended thinking. |
| `fable` | Claude Fable 5 | Extended thinking 8-12 min. Separate usage cap from 5h quota. |
| `best` | Claude Fable 5 | Same as `fable`. |
| `haiku` | Claude Haiku | Fastest. |
| `claude-*` | Any full model ID | Must match `claude-[A-Za-z0-9._-]+` pattern. |

## Timeout guidance for adversarial pipeline

| Model | --timeout (silence) | --hard-timeout | --cwd needed? |
|-------|--------------------|----------------|---------------|
| Fable 5 | 900-1800s (15-30 min) | 2400-3000s (40-50 min) | Yes |
| Sonnet | 600s (10 min) | 1200-1800s (20-30 min) | Yes |
| Opus | 600s (10 min) | 1200s (20 min) | Yes |
