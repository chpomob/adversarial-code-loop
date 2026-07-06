# God-Module Refactor Workflow

**Validated:** 2026-07-02, omnisense firmware, 11 steps, all APPROVED cycle 1
**Result:** main.cpp: 1444 → 281 lines (-80%), 10 new modules, 5 new host tests

## Strategy

Extract cohesive units from a god-module by doing **one module per adversarial dev loop step**:
1. Create new .h/.cpp files for each module
2. Move functions + static state from the god-module to the new files
3. Replace the old code with #include + function calls
4. Run tests, commit

## Ordering Rules

1. **Stateless modules first** (no shared state with the god-module)
2. **Stateful modules that own their own state** (statics moved with the code)
3. **Dependent modules last** (depend on already-extracted modules)
4. **The orchestration bridge goes last** (SPSC, drain, task creation — touches everything)

## Worked Example (omnisense firmware)

| Order | Module | Risk | Lines out of main.cpp | Tests |
|-------|--------|------|----------------------|-------|
| 1 | `ui/wifi_setup` | Low | -361 | Smoke |
| 2 | `app/logger` | Low | -124 | Smoke |
| 3 | `app/config_store` | Low | -58 | Existing |
| 4 | `core/band_agg` | Low | -31 | **New** |
| 5 | `core/subghz_agg` | Medium | -115 | **New** |
| 6 | `core/resp_tracker` | Medium | -80 | **New** |
| 7 | `core/traffic_policy` | Medium | -45 | **New** |
| 8 | `app/fingerprint_svc` | Medium | -202 | Smoke |
| 9 | `app/sensing_bridge` | **High** | -150 | Smoke |
| 10 | `app/wifi_link` | Low | -34 | Smoke |
| 11 | Slim main.cpp | Low | -146 | All |

## Key Points

- Each step must compile and pass tests before commit
- The most critical step (#9, sensing_bridge) should be done in the middle, not last — so regressions are caught early
- `--project-dir` mode does NOT work with pi as DEV. Use `--dir` (concatenate files) or `--stdin` (pipe content)
- Pure extraction — never rewrite logic during refactor
