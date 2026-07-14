# Partial-Merge Gap-Fill Pattern

## When to use this pattern

When the adversarial pipeline targets a feature that **already exists in upstream**
but **only partially** — the spec describes something that's been merged by someone
else but with gaps (missing code paths, incomplete coverage, no tests).

**Trigger signals:**
- The review comment on your PR says "this exists in main now, but only in one branch"
- `git diff` against upstream shows the hook/feature is already in some code paths
- Someone else merged a partial implementation of your spec'd feature

## Workflow

### Step 1: Gap analysis (before writing spec)

Don't spec the feature from scratch. First, inventory what exists:

```bash
# Check if the feature already exists upstream
grep -n "on_status_bar_render" cli.py
grep -rn "VALID_HOOKS" hermes_cli/plugins.py

# Check ALL code paths (not just one)
# Look for every width branch, every render path
grep -n "_build_status_bar_text" cli.py
grep -n "width <" cli.py

# Check if your own plugin is compatible
grep -n "register_hook" your_plugin/*.py
```

### Step 2: Build a gap matrix

| Path / Concern | Status | Action needed |
|---|---|---|
| Full-width (≥ 76) | ✅ Exists | None |
| Medium (52-75) | ❌ Missing | Add hook |
| Narrow (< 52) | ❌ Missing | Add hook |
| Tests for full-width | ❌ Missing | Add tests |
| Tests for medium | ❌ Missing | Add tests |
| Tests for narrow | ❌ Missing | Add tests |
| Plugin compatibility | ❓ Verify | Check snapshot keys match |

### Step 3: Write a gap-focused spec

The spec should explicitly state what already exists and what needs filling:

```yaml
---
name: "extend-status-bar-hook"
targets:
  - file: cli.py
    description: "Add on_status_bar_render invocation to narrow and medium width render paths"
  - file: tests/test_status_bar.py
    description: "Regression tests for all three width branches"
existing:
  - path: full-width
    status: present
    file: cli.py
    line: 5228
verify_existing: true
---
```

### Step 4: Spec requirements

Must include:
1. **Existing state** — document what's already there so the DEV doesn't redo it
2. **Gaps only** — each requirement maps to a gap in the matrix, not re-stating the whole feature
3. **Plugin compatibility** — if a user plugin depends on the hook, list its snapshot key expectations
4. **Verification** — commands that prove the existing path wasn't broken

### Step 5: Plan

The plan should have:

- **P1: Assessment** — Read existing hook code, snapshot shape, plugin code. (No code change — pure discovery.)
- **P2: Add medium-width hook** — Follow the exact same pattern as the full-width path.
- **P3: Add narrow-width hook** — Adapt for the narrower format (usually different separators, no fragment list).
- **P4: Tests for all three paths** — Hook registration, callback isolation, width-isolated rendering.

### Step 6: Code loop

Use the standard adversarial-code-loop with the gap-focused plan. The REVIEWER may
flag existing code as broken — that's expected. The FIXER should push back on
findings that target pre-existing code outside the spec scope.

## Validated example

**Context:** PR #32299 (`feat(plugins): add on_status_bar_render hook`) was reviewed
by hermes-sweeper (teknium1) and found incomplete. By the time the review landed,
upstream/main already had the hook in the **full-width branch only** (line 5228),
but the **narrow and medium branches** were still missing it.

**Gap analysis result:**
```
Full-width: on_status_bar_render ✓  (exists at cli.py:5228)
Medium:     on_status_bar_render ✗  (no hook in 52-75 width branch)
Narrow:     on_status_bar_render ✗  (no hook in < 52 width branch)
Tests:                              completely absent
Plugin:     hermes-quota-status ✓   (compatible, uses snapshot + **kwargs)
```

**Used when:** The user's PR branch was ~750K lines behind upstream/main and the
partial merge made a full rebase impractical. Instead, a new PR scoped to "fill the
gaps" was opened against current main.

## Pitfalls

1. **Don't re-implement what already exists.** The spec must clearly say "X already
   works in the full-width path — DO NOT touch it." The DEV may try to refactor it.
2. **The existing code may have its own bugs.** Note them in the spec but keep them
   out of scope unless they block the gap-fill.
3. **Plugin snapshot keys must match the existing hook.** If the existing hook passes
   `snapshot=snapshot` but the plugin expects `snapshot` as a positional arg with
   different key names, the gap-fill must either add the missing keys or update the
   plugin.
4. **The reviewer may flag gaps that are intentionally out of scope.** The plan should
   pre-empt this with a "known issues / out of scope" section the reviewer can reference.
5. **When upstream has advanced significantly** (100K+ lines), create a NEW branch from
   current main rather than rebasing the old PR branch. The gap-fill spec scopes the
   work narrowly enough that a clean branch is simpler than a deep rebase.
