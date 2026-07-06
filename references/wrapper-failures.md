# claude-tmux-wrapper Integration Failures with Adversarial Skills

## Scope

claude-tmux-wrapper v1.3.0+ has 7 documented integration failures (5 root
causes) when used as a subprocess of `adversarial_loop.py` /
`adversarial_review.py`. This is the canonical doc; the wrapper's own
behavior is documented in the `claude-tmux-wrapper` skill.

## Root Causes & Symptoms

| # | Root cause | Symptom | Frequency |
|---|-----------|---------|-----------|
| 1 | .done detection failure (Write tool bypass) | Subprocess stuck although BUILD/FIX finished | ~40% |
| 2 | Inactivity timeout vs extended thinking | Exit code 3 "timed out" | ~25% |
| 3 | Orphaned wrapper process after tmux kill | Pipeline stuck on Popen.wait() | ~15% |
| 4 | File/done protocol vs JSON-only output | Fable 5 emits non-JSON → parse crash | ~10% |
| 5 | Safety filter fallback (adversarial+keyword) → Opus | Fast, conservative responses; no extended thinking | ~10% |

### RC1 — .done file detection failure (Write tool bypass)

**Root cause:** Fable 5 (and increasingly Claude models) use the Write tool
to write files directly to disk, bypassing the file/done protocol that
claude-tmux.py expects. The wrapper watches for a `.done` signal file, but
Fable writes the output to actual project files and never creates `.done`.

**Symptom:** The tmux session shows the prompt `❯` (Fable is done), but
claude-tmux.py waits indefinitely for `.done`. The adversarial loop blocks
on `Popen.wait()`.

**Recovery (kill cascade):**
```bash
# 1. Kill the tmux pane
tmux kill-session -t claude-tmux-<PID>

# 2. If still blocked, kill the wrapper orphan (see RC3)
kill -9 $(pgrep -f "claude-tmux.*${SESSION_NAME}")

# 3. Check git diff for files written before the kill
git diff --stat
```

After the kill cascade:
- **BUILD stuck:** if `01_code.md` appeared, CRITIQUE will start — let it run.
  If it's still missing, extract the code from `/tmp/claude-tmux-output-*.txt`
  manually (read it BEFORE the wrapper exits — it cleans these files up).
- **FIX stuck:** the loop reports `X Phase 'FIX #1' failed (exit code -9)`.
  The FIX may have written files to disk via sandbox before the kill — check
  `git diff --stat`, evaluate the changes against `02_review.json`, compile,
  and commit manually. These writes are NOT captured in the artifacts (the
  sandbox fallback only runs when the phase returns non-JSON, not when it is
  killed).

**Prevention:** add an instruction to the spec discouraging direct file
writes ("Put ALL code in the response as code blocks; do NOT write to disk
directly; use the requested output file."). Always pass long timeouts (RC2)
so the wrapper outlives extended thinking.

### RC2 — Pane inactivity timeout vs extended thinking

**Root cause:** claude-tmux.py `--timeout` defaults to 60s (pane
inactivity). Fable 5 extended thinking takes 3-12+ minutes ("Concocting…
(7m 26s · ↓ 35.0k tokens)"). The spinner updates 1-column characters
(`·` `✽` `✻`), which the wrapper's `tmux capture-pane` polling does not
always register as activity.

**Symptom:** exit code 3 — "Claude timed out after 60s of pane inactivity".
`01_code.md` is empty/truncated, or `loop_N_03_fix.err` contains only the
timeout message.

**Fix:** always pass `--timeout 600 --hard-timeout 900` to claude-tmux.py
for Fable 5 sessions, and keep the pipeline's own `--timeout >= 900`:

```bash
--dev-cmd "python3 ~/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model fable --timeout 600 --hard-timeout 900"
```

**Inverse trap — the blinking cursor defeats the inactivity timeout:** once
Fable finishes and the pane sits at `❯`, the blinking cursor changes the pane
hash on every poll, so the inactivity deadline keeps resetting and
`--timeout` never fires. `--hard-timeout` is the ONLY reliable guard — set it
to a sane value (900s for Fable 5, not the 1800s default).

**Recovery if the timeout already fired:** the BUILD output (`01_code.md`)
and review (`02_review.json`) are preserved; only the killed phase is lost.
If the BUILD code is solid and the findings are manageable, skip re-running:
extract from `01_code.md`, address the critical findings manually, compile,
test, commit.

### RC3 — Orphaned wrapper process after tmux kill

**Root cause:** the pipeline launches subprocesses with
`start_new_session=True` (separate process group, needed for killpg on
timeout). The wrapper launches tmux, whose panes are managed by the tmux
*server* — not by the wrapper directly. Killing the tmux session kills the
server and the model, but the wrapper Python process survives as an orphan,
and the pipeline's `Popen.wait()` blocks on it forever.

```
adversarial_loop.py (PID A)
  └─ Popen: claude-tmux.py (PID B)   ← wait() blocks here
       └─ tmux server (PID C)        ← kill-session kills this
            └─ Fable 5 (PID D)       ← already finished
```

**Recovery:**
```bash
ps aux | grep -E 'adversarial_loop|claude-tmux|tmux'
tmux kill-session -t claude-tmux-<PID>     # if still alive
kill -9 <wrapper_PID>                      # or: pkill -9 -f "claude-tmux\.py.*fable"
```
The pipeline sees exit code -9 and continues (BUILD → CRITIQUE) or fails the
phase (FIX → recover sandbox writes via `git diff`, see RC1).

### RC4 — File/done protocol incompatible with JSON-only output

**Root cause:** the wrapper injects a "write your output to file X"
instruction. For strict JSON roles (CRITIC, VERIFIER), Fable outputs
markdown-wrapped JSON or prose with embedded JSON that breaks the JSON
parser.

**Workaround:** for reviewer roles that must emit strict JSON, don't use the
wrapper — use Codex CLI (native JSON handling), or manual tmux with strict
instructions: "Write STRICTLY valid JSON to /tmp/out.json using the Write
tool — no markdown, no code fences, no explanation."

### RC5 — Safety filter fallback (adversarial + offensive keywords)

**Root cause:** Fable 5 safety guardrails. The word "adversarial" in the
persona/spec plus offensive-security keywords (cybersec, attack, exploit,
hack, espionage, surveillance) triggers a silent fallback to Opus 4.8. No
error is shown — the session continues transparently on Opus.

**Detection:** no ✽/✻ thinking spinner in tmux, responses in seconds instead
of minutes, more conservative code, no automatic MCP discovery.

**Fix:** for offensive-security content, use Opus 4.8 or Codex explicitly in
the role command instead of Fable 5 — predictable behavior, no silent
fallback.

## Background runs killed by the orchestrating agent

When the pipeline runs in background (`terminal(background=true)`) and the
wrapper hangs (RC1/RC2-cursor), the agent should kill the background process
and recover from disk:

1. **Detect the hang**: `uptime > hard_timeout * 1.2` AND the tmux pane shows
   a fixed `❯` (no spinner, no advancing token counter). A pane with an
   active spinner is extended thinking — wait instead.
2. **Kill** the background process.
3. **Inspect artifacts** (`01_code.md`? `02_review.json`? `loop_N_03_fix.md`?)
   to see which phases completed.
4. **Inspect `git diff --stat`** for sandbox writes not captured in artifacts.
5. **Compile + test** (always), then commit if OK.
6. Do NOT re-run the loop if the code is already on disk — it burns quota for
   nothing.

## When to bypass the wrapper entirely

When 2+ root causes fire in the same session, switch to manual tmux
orchestration:

```bash
# Interactive tmux (preferred — stays on the 5h quota)
tmux new-session -d -s manual-loop -x 160 -y 40
tmux send-keys -t manual-loop \
  "cd $(pwd) && claude --dangerously-skip-permissions --model fable" Enter
sleep 8 && tmux send-keys -t manual-loop Enter                 # trust dialog
sleep 3 && tmux send-keys -t manual-loop Down Enter            # permissions
tmux send-keys -t manual-loop \
  "Read /tmp/spec.md and implement it. Write output to /tmp/out.json" Enter
```

## Monitoring

```bash
tail -f .adversarial-loop-*/log.txt                          # pipeline logs
watch -n 30 "ps aux | grep 'claude-tmux' | grep -v grep"     # orphan check
```
