# Codex DEV + pi.dev/DeepSeek REVIEW — Proven Adversarial Combo

## When to use

- Preferred pattern for embedded firmware development with host tests
- Claude quota exhausted, or you want to keep the 5h window for other work
- Spec is moderate complexity (1-4 files, no extended thinking needed)
- DeepSeek-v4-pro via pi.dev gives exhaustive reviews (7-10 findings per
  spec vs 2-4 for Codex); Codex generates solid embedded C with full Unity
  test suites

## Validated configuration

```bash
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec spec.md \
  --workdir /path/to/project \
  --dev-cmd "codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --sandbox danger-full-access" \
  --review-cmd "pi-hermes -p --provider deepseek --model deepseek-v4-pro --thinking low" \
  --max-loops 2 --no-arbiter --timeout 600 \
  --out .adversarial-loop-combo
```

Use `--timeout 600` for integration specs (3+ files): the Codex sandbox FIX
phase regularly exceeds 300s (bubblewrap write overhead); at `--timeout 300`
the FIX times out ~50% of the time.

| Spec type | Timeout | Files | FIX timeout risk |
|------|---------|----------|-------------------|
| Pure module (1-2 files) | 300s | < 3 | Low (~10%) |
| Integration (3-5 files) | 600s | 3-5 | Moderate (~30%) |
| Large integration (6+ files) | 600s+ | 6+ | High (~50%) |

## Field observations (10+ validated specs, OmniSense Phases A+B)

### Review quality (DeepSeek)
- 7-10 findings per spec vs 2-4 for Codex; finding count grows with spec
  complexity
- Catches real concurrency bugs (C11 seq-lock memory ordering), spec
  inconsistencies (tests that don't test what they claim), NaN safety,
  boundary conditions, dangling-pointer APIs
- Well-structured JSON findings with severity, but with trailing prose —
  the script's JSON extraction handles it
- `--thinking low` is required — `--thinking off` produces shallow findings
  ("add error handling") instead of real logic bugs
- Cold start: first call takes 30-60s; subsequent reviews 60-120s. Budget
  2-3 extra minutes per spec vs a Codex REVIEW. The APPROVE verdict is also
  more reliable (fewer false rejections)

### Codex DEV behavior
- Fast (~1-3 min per step)
- **Test inflation (known behavior):** Codex systematically adds tests beyond
  the spec (9 asked → 21 produced; 8 → 12; 6 → 16). Good signal (free
  coverage), but: the spec's test count becomes wrong, bonus tests may probe
  edge cases the BUILD didn't handle, and suites take ~10% longer.
- **Pre-addresses findings:** Codex as DEV consistently implements edge-case
  handling (NaN guards, bounds checks, atomic safety, temp+rename writes)
  before DeepSeek flags them. The FIX phase is often a formality — check
  `git diff` before assuming a FIX failure; the code may already be correct
  on disk. Consider `--max-loops 1` to save time and tokens.

### FIX timeout recovery
Codex sandbox writes to disk BEFORE the timeout, so a timed-out FIX usually
lost nothing. Do NOT re-run the loop:

1. `git diff --stat` for the modified files
2. Read `02_review.json` for the DeepSeek findings
3. Verify the critical findings are addressed
4. Build + test (`make all`, `pio run`)
5. Commit

### Quota independence
DeepSeek and Codex (OpenAI) use separate quota pools — a rate limit on one
does not affect the other. This combo distributes risk and consumes no
Claude quota.

## tmux session cleanup after a kill

After killing a background adversarial loop, tmux sessions created by
claude-tmux.py survive. Clean them up after each recovery:

```bash
tmux list-sessions 2>/dev/null | grep '^claude-tmux-' | cut -d: -f1 | \
  xargs -r tmux kill-session -t
```

## Vs Fable 5 DEV + Codex REVIEW

| Aspect | Fable+Codex | Codex+DeepSeek |
|--------|------------|----------------|
| Code quality | Excellent (extended thinking 8-12 min) | Good (fast, test inflation) |
| Review | 2-4 findings, sometimes shallow | 7-10 findings, real bugs |
| Pipeline reliability | tmux wrapper bug ~40% | FIX timeout ~30% (>3 files) |
| Total time | 15-30 min | 8-15 min |
| Quota | Claude 5h (limited) | DeepSeek (cheap, separate pool) |
| Best for | Complex HW specs, BLE HCI | Algorithmic specs, tests, UI integration |
