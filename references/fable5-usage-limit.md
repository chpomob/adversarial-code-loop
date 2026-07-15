# Fable 5 usage limit

Fable 5 has a **separate usage cap** distinct from Claude Pro's 5h sliding quota.

**Symptom:** Claude via claude-tmux starts normally (bypass permissions dialog, reads prompt), then immediately displays `"You've reached your Fable 5 limit. Run /usage-credits to continue or switch models with /model."` and stops processing.

**When it happens:**
- Fable 5 has a daily or rolling usage cap independent of the 5h sliding window.
- Regular Claude Pro quota (`5h sliding window`) can still be green while Fable 5 is blocked.
- The user's `/usage-credits` command shows remaining Fable 5 credits.

**Fix:** switch to `--model sonnet` or `--model opus` which use the regular Claude Pro 5h sliding quota. Sonnet is preferred for plan-challenger and code-loop REVIEW because:
- No extended thinking (faster response, no 12-min silence)
- Reliable JSON output
- Lower token cost

**Switching:**
```bash
# Instead of:
--review-cmd "python3 /path/to/claude-tmux.py --yolo --model fable --timeout 1800 --hard-timeout 2400 --cwd /path"

# Use:
--review-cmd "python3 /path/to/claude-tmux.py --yolo --model sonnet --timeout 600 --hard-timeout 1200 --cwd /path"
```

**Validated:** 2026-07-15 during adversarial-features plan pipeline. Fable 5 hit its limit 25 minutes into the challenge phase. Switched to Sonnet which completed in ~2 minutes.
