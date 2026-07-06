# GLM-5.2 DEV + Claude REVIEW — validated pairing

## Commands

```bash
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec <spec.md> --workdir <project> \
  --dev-cmd "pi -p --provider zai --model glm-5.2 --thinking high" \
  --review-cmd "python3 /home/chpo/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model best --timeout 600 --hard-timeout 1200 --max-turns 25" \
  --max-loops 2 --no-arbiter --timeout 1200
```

## Strengths
- GLM produces compact, compilable code (no cascading out of spec scope)
- Claude finds deep issues (architecture, security, correctness) in its review
- Claude's verbosity is not an issue because review prompt is under 1K tokens (model reads files from disk)
- Works well for multi-file features (tested on private messages: 4 files, 660 lines)
- Completed in 1 cycle in ~11 min total

## Known behavior

| Phase | Duration | Notes |
|-------|----------|-------|
| BUILD (GLM) | 4-5 min | --thinking high, writes code to disk |
| REVIEW (Claude) | 4-5 min | Fable 5, reads files, runs git diff |
| FIX (GLM) | 3-4 min | --thinking high, addresses findings |
| VERIFY | 1-2 min | Uses DeepSeek (default review-cmd) or Claude |

## Tested with
- Private messages chatter feature (protocol + server + client, 660 lines)
- Multi-file refactors
- Single-file fixes
