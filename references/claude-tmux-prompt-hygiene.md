# claude-tmux: Prompt Hygiene for Adversarial Pipeline Use

**Validated 2026-07-14.** The claude-tmux wrapper exists to run Claude via tmux (stay on
5h sliding quota) instead of `claude -p` (Agent SDK monthly cap). It must work as a
transparent stdin→stdout pipe — the adversarial pipeline sends the *exact* prompt it
wants the model to receive, and the wrapper must pass it through unchanged.

## Golden rule: the wrapper captures output, not behavior

The wrapper's job is to:
1. Read stdin (contains the full prompt from the pipeline)
2. Start a tmux session running `claude` with that prompt
3. Wait for Claude to finish
4. Collect Claude's output and write it to stdout

The wrapper must **NOT**:
- ❌ Prepend instructions ("Do NOT run shell commands", "You MUST output JSON")
- ❌ Append behavioral modifiers ("Write the exact output that the task demands")
- ❌ Rephrase or reformat the pipeline's prompt in any way

The only append is the *output capture instruction*:
```
"When you are done, write your response to {output_file}
using the Write tool. After the file is written, create an
empty file at {done_sentinel} using the Write tool to signal
completion."
```

This is passive — it tells Claude *how* to deliver the result, not *what* to produce.

## What goes wrong when you modify the prompt

**Symptom:** CHALLENGE phase fails with `"invalid JSON after retry"`.
**Cause:** The wrapper prepended `"Do NOT run shell commands"` or `"You MUST respond
with ONLY JSON"` after the pipeline already said `"Output ONLY valid JSON"`. Claude
receives two inconsistent instructions and either runs shell commands anyway (confused)
or produces prose instead of JSON.

**Symptom:** REVIEW phase produces empty findings even when BUILD committed code.
**Cause:** The wrapper appended `"Write the exact output that the task above demands"`
which overwrites the pipeline's instruction to review the git diff. Claude writes a
summary of the task instead of reviewing the code.

## Implementation (v2 output mechanism)

The working version (342 lines, /home/chpo/claude-tmux-wrapper/claude-tmux.py) uses:

```
tmpdir = /tmp/claude-tmux-<pid>/
├── output.txt       # Claude writes response here
└── done.sentinel    # Claude creates this when done
```

```python
prompt += (
    f"\n\nWhen you are done, write your response to {output_file} "
    f"using the Write tool. "
    f"After the file is written, create an empty file at {done_sentinel} "
    f"using the Write tool to signal completion."
)
```

No other modification to `prompt`. The wait loop checks for `done_sentinel` existence,
then reads `output.txt`. Fallback to pane scraping only on timeout.

## Restoration if deleted

The working copy lives at `/home/chpo/claude-tmux-wrapper/claude-tmux.py` (outside the
skills directory, safe from git stash/checkout operations). If the skills directory
auto-init or a stash conflict deletes it, restore with:

```bash
cp /home/chpo/claude-tmux-wrapper/claude-tmux.py \
  ~/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py
```

The `autonomous-ai-agents` directory is NOT tracked by any skill repo and can be
recreated on demand.

## When claude-tmux cannot be used

The plan CHALLENGE phase embeds the full plan text + full spec text in the prompt.
For specs with 12+ requirements and 27+ criteria (~240 lines), the prompt is too
large for Claude Fable 5's extended thinking — the silence period exceeds 20 min
even with `--timeout 1200`. Fall back to GLM-5.2 for the plan challenge:

```bash
--review-cmd "pi -p --provider zai --model glm-5.2 --thinking high"
```

Claude remains the best reviewer for CODE LOOP REVIEW phases (git diff, files on disk),
where the prompt is under 1K tokens and extended thinking is 8-12 min.
