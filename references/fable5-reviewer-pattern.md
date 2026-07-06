# Fable 5 as REVIEWER — Pattern & Behaviour (June 2026)

## Extended thinking

Fable 5 (Mythos-class) does deep extended thinking on every response. Typical timings:
- Simple ping: 3-5 seconds
- Read + review of a 423-line diff: 3-5 minutes of thinking
- Review of 50 files (390KB): 12 minutes of thinking
- Full code + test verification run: 5-10 minutes

The thinking indicators in tmux:
- "Shenaniganing…" — standard thinking state
- "Pouncing…" — initial analysis
- "Unravelling…" — deep reasoning
- "Crunched for Xm Ys" — completion summary
- "Baked for Xm Ys" — completion summary (alt)

## Tool usage during review

Fable 5 actively uses tools during its review:
1. **Read** — reads files, diffs, source code
2. **Bash** — runs shell commands (git diff, gradle, grep)
3. **MCP** — calls external MCP servers (e.g., `aosp-rag` to verify AOSP CalendarProvider2 source)

In a single review session verified (2026-06-09):
- Searched for 1 pattern (grep)
- Read 1 file (the diff)
- Called aosp-rag MCP 2 times (verified ExtendedProperties restriction)
- Ran 4 shell commands
- Read 3 additional files
- Built project
- Ran full test suite (115 tests, 0 failures)

## Orchestration pattern (manual tmux, NOT claude-tmux.py wrapper)

For multi-step review tasks, the wrapper script (`claude-tmux.py`) doesn't work well because:
- Fable 5's first response is often brief ("I'll review...") — it then waits for user input at ❯
- The wrapper expects a single response + Write tool call
- Fable 5 needs multiple turns to read, think, verify, write

**Manual tmux orchestration** is required:
```
# Start
tmux new-session -d -s fable5 -x 160 -y 40
tmux send-keys -t fable5 'claude --dangerously-skip-permissions --model fable' Enter
sleep 8 && tmux send-keys -t fable5 Enter  # trust dialog
sleep 3 && tmux send-keys -t fable5 Down && sleep 0.5 && tmux send-keys -t fable5 Enter  # bypass
sleep 5  # wait for ❯ prompt

# Send task (just the text — the file will be read by Claude)
tmux send-keys -t fable5 'Read /tmp/diff and review it...' Enter

# Monitor
sleep 60 && tmux capture-pane -t fable5 -p -S -5

# Clean up after review file is written
tmux send-keys -t fable5 '/exit' Enter
sleep 2 && tmux kill-session -t fable5
```

## In the adversarial loop

When used as `--review-cmd` with the wrapper script, Fable 5 review output is limited (typically 300-700 chars). This is because:
- The wrapper feeds stdin + waits for Write tool output
- Fable 5's extended thinking may time out the wrapper
- The wrapper's TUI fallback captures only the first response

For deep reviews, manual tmux + reading from a file is more reliable.

## Pricing awareness

- $10/M input, $50/M output tokens (2× Opus 4.8)
- Included in Pro/Max plans at no extra cost through June 22, 2026
- After June 22: consumption-based pricing
- Fable 5 is NEVER the default model — must be explicitly selected with `--model fable` or `/model fable`
