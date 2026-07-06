# Migration: `claude -p` → `claude-tmux.py` Pipe Pattern

**Context:** `claude -p` (headless print mode) draws from Agent SDK credit ($20/mo Pro) since June 2026. Interactive `claude` via tmux wrapper stays on the 5h sliding quota. All skills must use claude-tmux.py instead.

## Simple pipe replacement

### Before (BANNED)
```bash
echo "context" | claude -p "instruction" --model opus --max-turns 10 --output-format json > output.json
```

### After
```bash
echo "context" | python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --prompt "instruction" --yolo --model opus --timeout 600 --hard-timeout 1200 --max-turns 20 > output.json 2>output.err
```

Key changes:
- `claude -p` → `python3 <abs-path>/claude-tmux.py`
- Add `--yolo` (skip permissions dialogs)
- Add `--timeout 600 --hard-timeout 1200` (inactivity + absolute caps)
- Add `--prompt "instruction"` to prepend the instruction before stdin context
- Drop `--output-format json` (not available in interactive mode; prompt the model to output JSON instead)
- 2>stderr redirect recommended for debugging (model switches, timeout warnings)

## Multi-line instruction (bash pipe)

When the instruction is a multi-line string that was previously embedded in `claude -p "..."`:

### Before (BANNED)
```bash
python3 -c "..." | claude -p "Produis un RAPPORT en markdown:
# Title
## Section A
## Section B
Inclus les insights." --model opus --max-turns 10 > output.md
```

### After
```bash
python3 -c "..." | python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --prompt "Produis un RAPPORT en markdown: # Title ## Section A ## Section B Inclus les insights." --yolo --model opus --timeout 600 --hard-timeout 1200 --max-turns 20 > output.md
```

The prompt string sits on one line (no literal `\n` needed — markdown headers render fine with spaces between them via `--prompt`).

## Multi-line context + instruction (echo pipe)

When both context and instruction are in an echo string piped to claude:

### Before (BANNED)
```bash
echo "$SAFETY
<UNTRUSTED_CTX>
$CTX
</UNTRUSTED_CTX>
Do the task." | claude -p "$(cat)" --model opus --max-turns 15 --output-format json > output.json
```

### After
```bash
echo "$SAFETY
<UNTRUSTED_CTX>
$CTX
</UNTRUSTED_CTX>
Do the task." | python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model opus --timeout 600 --hard-timeout 1200 --max-turns 20 > output.json 2>output.err
```

Here the full prompt (both instruction and context) is piped via stdin. No `--prompt` needed.

## Claude with `--allowedTools` and `--workdir`

### Before (BANNED)
```bash
echo "..." | claude -p "Write code..." --model opus --max-turns 15 --allowedTools "Read,Write,Edit,Bash" --workdir "$REPO" > output.json
```

### After
```bash
echo "..." | python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model opus --timeout 600 --hard-timeout 1200 --max-turns 20 --allowedTools "Read,Write,Edit,Bash" --workdir "$REPO" > output.json 2>output.err
```

`--allowedTools` and `--workdir` are passed through to `claude` by the wrapper — they work identically in interactive mode.

## Verifying a migration

1. Run the command and check exit code (0 = success)
2. Check that output file is non-empty and contains the expected content
3. Check stderr for `MODEL_SWITCHED` warnings (model fallback)
4. If output is prose instead of JSON, the prompt needs to explicitly demand JSON format

## References

- claude-tmux-wrapper skill for wrapper details
- adversarial-code-loop pitfall #11 for background on why claude -p is banned
- Skills patched in this migration (2026-06-16): triangle-code-analyze, triangle-code-architect, triangle-code-develop, triangle-code-review, triangle-agents, hermes-agent-skill-authoring, claude-code, ponytail-audit-workflow
