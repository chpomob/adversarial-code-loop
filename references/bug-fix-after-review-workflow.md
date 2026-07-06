# Bug Fix After Review — Workflow Pattern

When a code review produces 5+ findings across multiple modules,
organize fixes into small adversarial-loop-sized specs rather than
a single large spec.

## The Pattern

1. **Taxonomy pass**: Group findings by module/severity. Each group
   becomes one spec (e.g. F1a = wifi_csi data races, F1b = ble_rssi
   data races, etc.).

2. **Batch by risk**:
   - Lot 1 (HIGH/CRITICAL): data races, memory safety, init bugs
   - Lot 2 (MEDIUM): dead code, edge cases, missing init
   - Lot 3 (LOW): cosmetic, performance, style

3. **Each spec targets 1-3 files max** and adds zero or very few tests.
   The review already validated the tests — the fix just needs to
   compile and pass.

4. **Launch order**: most critical first, simplest first. This maximizes
   the chance that every fix gets done even if quota runs out.

## Example: OmniSense Bug Fix Session

```
Lot 1 — Data races (4 specs, 4 files total)
  F1a  wifi_csi atomics
  F1b  ble_rssi atomics
  F1c  subghz atomics
  F1d  fusion volatile -> _Atomic

Lot 2 — Logic bugs (3 specs, 4 files)
  F2a  config SD init in setup()
  F2b  VHCI stale-response race
  F2c  call fusion_set_band_snr()

Lot 3 — Edge cases (2 specs, 2 files)
  F3a  millis() wraparound
  F3b  csi_doppler min subcarriers
```

## When FIX Times Out

If the FIX phase times out (common with Codex sandbox at 300s
on integration specs):

1. Kill the process
2. `git diff --stat` — verify the BUILD wrote the core changes
3. Read `02_review.json` — check if the findings are critical
4. Apply critical fixes manually
5. `make all && pio run` — if it compiles and tests pass, commit
6. Document skipped findings as technical debt

If Codex wrote files during BUILD (sandbox mode), they're always
recoverable via `git diff --stat` even if FIX never ran.
