# claude-tmux.py fixes (2026-07-14)

The claude-tmux wrapper (`/home/chpo/claude-tmux-wrapper/claude-tmux.py`) was fixed to behave exactly like `claude -p` from the outside — same stdin/stdout/stderr contract, same exit codes — but running Claude via tmux (5h sliding quota) instead of Agent SDK billing.

## Bugs fixed by adversarial code loop (Codex DEV + GLM REVIEW)

| Bug | Fix | Validated |
|-----|-----|-----------|
| `--hard-timeout` expired → `return 0` (partial output treated as success) | Now `return 3` (EXIT_TIMEOUT) after teardown | APPROVED 2026-07-14 |
| `chpo@` hardcoded in pane-heuristic finish detection (only worked for one user) | Replaced with generic prompt-regex `[#$%>]\s*\Z` | APPROVED 2026-07-14 |
| `output.txt` read before `done.sentinel` (partial output could end the run) | `done_sentinel` check placed BEFORE `hard_deadline` check in the wait loop | APPROVED 2026-07-14 |
| Trust-dialog matched bare word `"trust"` (any "trust" in pane → unsolicited Enter) | Changed to `"trust the files"` (actual dialog text) | APPROVED 2026-07-14 |
| Pane heuristic killed Claude prematurely on prompt appearance | Changed to deferred `pane_fallback_ready` flag — only used after normal wait expires | APPROVED 2026-07-14 |
| Dead code: double `read_nonempty_file(output_file)` | Removed second (unreachable) block | APPROVED 2026-07-14 |

## Prompt hygiene (critical)

The wrapper must NEVER modify the pipeline's prompt with behavioral instructions. Its only addition is:

```python
prompt += (
    f"\n\nWhen you are done, write your response to {output_file} "
    f"using the Write tool. "
    f"After the file is written, create an empty file at {done_sentinel} "
    f"using the Write tool to signal completion."
)
```

No prepended "Do NOT run shell commands" or "Output ONLY JSON" — the pipeline already sends those. Adding them causes Claude to produce prose instead of JSON.

## Key design decisions

1. **done.sentinel is the authoritative completion signal.** The wait loop checks for it first, then hard_deadline, then pane heuristic. This prevents partial output from being returned as success.
2. **Hard timeout returns 3 (EXIT_TIMEOUT).** The pipeline timeout branch (else clause) also returns 3. Automation consumers can distinguish clean completion (0) from truncation (3).
3. **Pane heuristic is a last-resort fallback.** Only used after the normal wait loop expires AND `pane_fallback_ready` was set by seeing a shell prompt after a Claude response bullet (●).
4. **Temporary directory uses PID-based naming** (not `mkdtemp`). Known limitation: predictable path, race condition possible. Not yet fixed.
