# Monitoring Long-Running Loops

## Golden rule

**Never promise "I'll check back in X minutes" without actually doing it.** Either:

1. Launch with `notify_on_complete=true` and wait for the notification (recommended).
2. Use an explicit polling loop that you can verify ran.
3. Do other work while the loop runs.

The user hates false promises — "je reviens dans 5 min" that never happens. If you can't commit to actual monitoring, use option (1) and stay silent.

## Pattern A: Background + notify (best)

```python
terminal(background=True, notify_on_complete=True, command="...")
# Keep working on other things. The system notifies you when done.
```

Pros: zero effort, reliable. Cons: you don't see mid-run progress.

## Pattern B: Polling loop (when you want progress)

```bash
for i in 1 2 3 4 5; do
  sleep 30
  ls -la artifacts/  # check for new files
  if grep -q '"APPROVE"' artifacts/verdict.json; then
    echo "APPROVED"; break
  fi
done
```

Use when you need to know about intermediate states (BUILD done? REVIEW started?).

## Pattern C: Wait for a specific file

```bash
while [ ! -f artifacts/final.json ]; do sleep 15; done
echo "Done!"
```

## What NOT to do

```markdown
"Je reviens dans 5 min pour check."  # ← NEVER. You won't.
"Je surveille, status dans un moment."  # ← Vague. Useless.
```

Either commit to a real polling loop with `sleep` intervals, or use `notify_on_complete`.
