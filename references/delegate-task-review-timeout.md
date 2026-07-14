# delegate_task Timeout for Long-Running Reviews

`delegate_task` subagents have a built-in 600s (10 min) timeout. Adversarial reviews
(architect → inspector → cross-review ×2 → synthesis) take **30-40 minutes** per skill
with Claude Fable 5 or GLM-5.2 with --thinking high. This means `delegate_task` CANNOT
be used to run the adversarial review pipeline — the subagent will timeout after 600s
with no useful result.

## Recommended approach

Run adversarial reviews directly via `terminal(background=true, notify_on_complete=true)`
with a generous `--timeout 2400`:

```bash
cd ~/.hermes/skills/adversarial-code-review/scripts
python3 adversarial_review.py --project-dir /path/to/target \
  --a-cmd "python3 /path/to/claude-tmux.py --yolo --model best --timeout 900 --hard-timeout 1800" \
  --b-cmd "codex exec -C /path/to/target" \
  --synth-cmd "python3 /path/to/claude-tmux.py --yolo --model best --timeout 900 --hard-timeout 1800" \
  --out /tmp/acr-target --timeout 2400
```

## If you must use delegate_task

Increase the delegation timeout in `config.yaml`:

```yaml
delegation:
  max_concurrent_children: 3
  # No per-task timeout in current config — subagent timeout is hardcoded at 600s
```

This is not currently configurable per-call. The 600s timeout is in the subagent runner
and cannot be overridden. Use terminal() directly.

## Validated 2026-07-14

3 out of 5 parallel adversarial-review delegations timed out at 600s. The actual
reviews (launched as terminal background processes) completed successfully, taking
20-35 min each depending on project size.
