# Codex DEV + agy (Antigravity) REVIEW — Proven Adversarial Combo

## When to use

- agy (Google Antigravity CLI) is your only / preferred reviewer model
- You want a Google model (Gemini 3.5 Flash, 3.1 Pro) as the critic
- Codex handles generation well (stdlib-only Python, embedded C, TypeScript)
- Spec involves OAuth, API clients, or multi-file plugin development
- agy reads files via its Read tool and writes code/confirmation via its
  Write tool — the agy review phase can inspect submission files

## Validated configuration

Validated 2026-06-12 on a 512-line Python module (OAuth + Cloud Code API
client + status bar integration):

```bash
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec spec.md \
  --workdir /path/to/project \
  --dev-cmd "codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --sandbox danger-full-access" \
  --review-cmd "agy -p --model 'Gemini 3.5 Flash (High)' --dangerously-skip-permissions --print-timeout 5m" \
  --max-loops 2 --no-arbiter --timeout 600
```

| Parameter | Value | Reason |
|-----------|-------|--------|
| `--max-loops` | 2 | Keeps token cost bounded; 1 cycle may suffice if DEV is thorough |
| `--no-arbiter` | on | agy REJECT + Codex FIX resolves most disagreements; arbiter adds another agy call |
| `--timeout` | 600s | Codex FIX phase for 3+ files can take 3-7 min (extended thinking + sandbox write overhead) |
| agy `--print-timeout` | 5m | Gives agy time to read files and write its review verdict |

No `--hard-timeout` needed — agy runs natively and responds in 2-5 min.

## Session observations (gemini_cloudcode.py, 512 lines)

| Phase | Model | Duration | Outcome |
|-------|-------|----------|---------|
| BUILD | Codex | ~5 min | Generated 512-line module + __init__.py + test updates |
| CRITIQUE | agy (Gemini 3.5 Flash High) | ~1 min | REJECT — 6 findings (2 high, 3 medium, 1 low) |
| FIX #1 | Codex | ~7 min | All 6 findings addressed (CSRF, thread safety, token cleanup, fallback, timestamp parsing, thread-local state) |
| VERIFY #1 | agy | ~1 min | APPROVE — all 6 resolved, 0 disputed |
| **Total** | | **~14 min** | **APPROVED in 1 cycle** |

### Review quality (agy / Gemini 3.5 Flash High)

- 6 findings on a 512-line spec: good depth
- High-severity findings were real (no CSRF state param, infinite refresh
  loop on token revocation, blocked fallback mechanism)
- Medium findings were actionable (TUI thread blocking, untyped timestamp
  AttributeError propagation, global mutable state)
- No false positives — all 6 findings were accepted by the FIX phase
- Consistent with DeepSeek review quality (7-10 findings) but slightly fewer
  findings per loc

### Codex FIX phase quality

- All 6 findings were addressed in one pass
- Added `secrets.token_urlsafe(32)` state param + callback validation
- Added `_delete_token()` on permanent auth failures
- Modified fallback logic in `__init__.py` to return `None` on auth errors
- Wrapped OAuth login in a daemon thread for TUI safety
- Deduplicated timestamp parsing into `parse_reset_time()` shared helper
- Moved global error state to `threading.local()`
- 23 tests passed (up from 17)

### Codex FIX speed

Slightly slower than Codex as DEV (~6 min vs ~3 min) because the FIX prompt
includes the full review JSON. For specs under 300 lines, expect 2-4 min.

## Timing considerations

Codex can take 10+ min to close stdout after writing files during
extended thinking — do NOT kill the process when it appears idle. The
files are written before stdout closes, so a timeout that kills the
subprocess DOES NOT lose file changes. On timeout recovery:

```bash
# Check what Codex wrote before the timeout
find . -newer 00_spec.txt -type f -not -path './.adversarial-loop/*' 2>/dev/null
git diff --stat
```

## agy architecture (internal)

agy v1.0.8 is a 169MB statically-linked Go binary that:

- Embeds **Playwright for Go** (`~/.cache/ms-playwright-go/1.57.0/`) — a
  Node.js + Chromium driver stack. The Playwright browsers must be
  installed separately (run `npx playwright install chromium` in the
  package dir, or the Go library downloads them on first use).
- **Does NOT launch its own Chrome** — it connects to an existing Chrome
  instance via the Chrome DevTools Protocol (CDP). It first reads
  `DevToolsActivePort` from `~/.config/chrome-data/SingletonLock` to
  discover the runtime port (not hardcoded 9222). If no Chrome is running,
  it connects to port 9222 and fails silently.
- **Authenticates via the system keyring** (secret service / D-Bus).
  Credentials for `chpomob@gmail.com` are stored there; no token files
  or gcloud CLI needed.
- Logs to `~/.gemini/antigravity-cli/log/cli-YYYYMMDD_HHMMSS.log` (glog
  format, readable without special tools).
- Config/state lives under `~/.gemini/antigravity-cli/` (implicit/
  directory with protobuf files, installation_id, etc.).

## Debugging silent agy failures

When `agy -p` exits 0 with no output (the most common failure mode):

```bash
# 1. Check the most recent log
tail -20 ~/.gemini/antigravity-cli/log/cli-$(date +%Y%m%d)*.log

# 2. Look for these telltale patterns:
#    "RESOURCE_EXHAUSTED (code 429)" — Gemini quota empty, check reset timer
#    "error getting token source: You are not logged into Antigravity" — auth lost
#    "Entering local chrome mode! This is WRONG" — Chrome CDP connection issue
#    "SingletonLock exists, overriding the port" — connected to existing Chrome OK

# 3. Verify agy can reach the API (no proxy issues)
#    URL: https://daily-cloudcode-pa.googleapis.com/v1internal:*

# 4. Check Chrome is running (renderer processes)
ps aux | grep chrome | grep -v grep | head -3
```

### Known limitation: agy --print mode and 429 quota errors

**agy v1.0.8 exits 0 without printing any error message when the Gemini
model returns HTTP 429 (RESOURCE_EXHAUSTED).** The model is selected and
the conversation is created, but the response phase gets a 429 and agy
treats this as a silent empty response instead of surfacing the error.

The error IS logged (see `~/.gemini/antigravity-cli/log/`), but the
pipeline caller sees exit 0 + empty stdout. This makes agy unreliable
for CI/pipe usage when quota is tight.

**Mitigations:**
- Check quota before using agy in a pipeline (hermes-quota-status plugin
  or `daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`)
- Fall back to another reviewer (codex, claude-tmux, pi-hermes) when
  agy returns no output
- Consider using agy interactively (`agy` without `-p`) where errors
  are shown in the TUI

## Vs other model pairings

| Aspect | Codex+AGY | Codex+DeepSeek | Fable+Codex |
|--------|-----------|----------------|-------------|
| Review depth | 6 findings / 512 loc | 7-10 / spec | 2-4 / spec |
| Finding quality | High (no false positives) | High (real bugs) | Moderate |
| Pipeline reliability | Good (no wrapper bugs) | FIX timeout ~30% (>3 files) | tmux wrapper ~40% |
| Total time | ~14 min | 8-15 min | 15-30 min |
| Quota pool | Gemini (agy uses agy quota) | DeepSeek (cheap) | Claude 5h (limited) |
| Best for | Python modules, plugins, stdlib-only code | Embedded C, algorithmic | Complex HW, BLE, large refactors |

## Known pitfall: agy review file access

Agy can read files from the working directory during its review phase
using its Read tool. Make sure the spec and any reference files are
present and readable. agy does NOT have access to the adversarial loop
artifacts directory by default — the review prompt from the pipeline
includes the diff and fix summaries.

## Recovery from killed / timed-out FIX phases

Same as `codex-deepseek-pattern.md`: Codex writes files to disk before
stdout closes. On timeout:

1. `find . -newer 00_spec.txt -type f -not -path './.adversarial-loop/*'`
2. Read `02_review.json` for the open findings
3. Verify the critical findings are addressed in the written files
4. Run full test suite
5. Commit
