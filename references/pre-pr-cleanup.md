# Pre-PR Cleanup for Adversarial Loop Output

When an adversarial loop produces code destined for an upstream PR (especially
against NousResearch/hermes-agent or any project with Conventional Commits
requirements), two artifacts from the pipeline need to be cleaned before pushing:

## 0. Diagnosis: Stale Fork Main

**Before assuming the branch is polluted, check if your fork's `origin/main` is
simply behind `upstream/main`.** This is the most common cause of "too many files
changed" in a PR.

```bash
# Set up upstream remote (one-time)
git remote add upstream git@github.com:ORG/REPO.git

# Fetch both
git fetch upstream main
git fetch origin main

# TRUE comparison: vs upstream main
echo "=== Real PR commits (vs upstream) ==="
git log --oneline upstream/main..origin/YOUR_BRANCH | head -10

echo "=== Misleading comparison (vs fork main) ==="
git log --oneline origin/main..origin/YOUR_BRANCH | head -10

# Real diff size
echo "=== Real diff (vs upstream) ==="
git diff --stat upstream/main..origin/YOUR_BRANCH | tail -5
```

If `upstream/main..YOUR_BRANCH` shows only your feature commits (e.g. 3)
while `origin/main..YOUR_BRANCH` shows 30+, the branch is fine — the PR
against upstream will show the correct diff. **No cleanup needed.**

If `upstream/main..YOUR_BRANCH` also shows 30+ unrelated commits, the branch
actually has garbage. Proceed with cleanup (Section 4).

## 1. `.gitignore` delta

**Problem:** PHASE 0 appends `--out` patterns (`.adversarial-loop/` by default)
to `.gitignore`. This change is committed via `git add -A` in BUILD and
propagates into the squash merge. The upstream project shouldn't receive
`.adversarial-loop/`, `*.orig`, or `*.rej` entries.

**Detection:**
```bash
git diff HEAD~1..HEAD -- .gitignore
```

If it shows only artifact patterns (`.adversarial-loop/`, `*.orig`, `*.rej`),
it needs removal.

**Fix (after squash-merge into parent branch):**
```bash
# Restore the upstream .gitignore
git checkout HEAD -- .gitignore

# Amend the squash commit (no message change needed)
git commit --amend --no-edit
```

**Fix (for `--no-merge` loops — branch not yet merged):**
```bash
# Before manual merge, check what was auto-added
git diff branch-point..loop-branch -- .gitignore

# If dirty, restore upstream version before merging
git checkout main
git merge --squash loop-branch
git checkout HEAD -- .gitignore
git commit -m "feat: ..."
```

## 2. Commit naming

**Problem:** The pipeline uses pipeline-internal names:
- BUILD: `build: <feature> — <summary>`
- FIX:   `fix: <feature> — address finding(s) (round N)`
- Merge: `squash: <feature> — adversarial approved`

These don't follow [Conventional Commits](https://www.conventionalcommits.org/)
and will be flagged by upstream reviewers.

**Fix:** After squash-merge, rewrite the commit message:
```bash
git commit --amend -m "feat(cli): add on_status_bar_render hook to narrow width tier"
```

**For multi-step plans** with stacked squash commits:
```bash
# Option A: rebase and reword each
git rebase -i HEAD~N   # change each 'pick' to 'reword'

# Option B: squash everything into one conventional-format commit
git rebase -i HEAD~N   # change all but the first to 'fixup'
# Then amend the remaining one
git commit --amend -m "feat(scope): single descriptive message"
```

**Verification:**
```bash
git log --oneline -1
# Should show: feat(cli): add on_status_bar_render hook to narrow width tier
```

## 3. Full pre-Push checklist

```bash
# 1. Fetch upstream main
git fetch upstream main

# 2. Check what actually changed vs upstream
git log --oneline upstream/main..HEAD
git diff --stat upstream/main..HEAD | tail -5

# 3. Verify commit messages follow conventional commits
git log --oneline upstream/main..HEAD

# 4. Check for .gitignore pollution
git diff upstream/main..HEAD -- .gitignore | wc -l
# Should be 0

# 5. Check for unwanted artifact dirs in the diff
git diff upstream/main..HEAD --name-only | grep -E '\.adversarial|\.orig|\.rej' || echo "Clean"

# 6. Push
git push -u origin HEAD
```

## 4. Full Branch Cleanup (when branch IS polluted)

When the branch actually contains unrelated commits (not just stale fork main),
use a **non-interactive rebase** since `reword` requires a TTY.

### Strategy: Edit all commits (avoids `reword` TTY problem)

`reword` opens an editor and fails with `"stdin is not a terminal"` in
non-TTY shells. `edit` stops the rebase and lets you use
`git commit --amend -m "..."` which works everywhere.

```bash
# 1. Start rebase with every commit set to 'edit'
GIT_SEQUENCE_EDITOR="sed -i 's/^pick/edit/'" git rebase -i upstream/main
```

The rebase pauses at each commit. For each stop:

### Fix: remove unwanted file + rename commit
```bash
git checkout upstream/main -- .gitignore     # restore file to upstream version
git add .gitignore                             # stage the restoration
git commit --amend -m "feat(scope): proper conventional commit message"
git rebase --continue
```

### Just rename (no file changes needed)
```bash
git commit --amend -m "feat(scope): proper message"
git rebase --continue
```

### Force push after cleanup

If the cleaned local branch has a different name than the PR branch:
```bash
git push origin LOCAL_CLEAN_BRANCH:PR_BRANCH_NAME --force
```

If same name:
```bash
git push --force origin PR_BRANCH_NAME
```

The PR stays open — it still points to the same remote branch name.

### Common Pitfalls During Cleanup

| Pitfall | Solution |
|---------|----------|
| Rebase fails with `stdin is not a terminal` | Use `edit` mode (not `reword`). `git commit --amend -m "..."` works in all shells. |
| Rebase aborted midway, detached HEAD | `git rebase --abort` returns to the original state safely |
| After abort, stale branch artifacts | `git checkout YOUR_BRANCH && git log --oneline upstream/main..HEAD` to verify |
| Force push rejected by branch protection | Use `--force-with-lease` instead of `--force` |
| PR shows 0 commits after force push | The PR branch name doesn't match — check `gh pr view N --json headRefName` |

## 5. Full end-to-end cleanup example

Session validated 2026-07-13: PR #63824 against NousResearch/hermes-agent.

```bash
# Diagnosis
git fetch upstream main
git log --oneline upstream/main..origin/feat/status-bar-hook-all-widths
# Only 3 commits (P1, P2, P3) — OK
# But origin/main was stale: git log origin/main..branch showed 37 commits

# Problem 1: .gitignore had .adversarial-loop/, *.orig, *.rej in P1 commit
# Problem 2: commit messages were "squash: ... — adversarial approved"

# Fix: non-interactive rebase
GIT_SEQUENCE_EDITOR="sed -i 's/^pick/edit/'" git rebase -i upstream/main

# Stop 1 (P1):
git checkout upstream/main -- .gitignore
git add .gitignore
git commit --amend -m "feat(plugins): add on_status_bar_render hook to narrow width tier (P1)"
git rebase --continue

# Stop 2 (P2): just rename
git commit --amend -m "feat(cli): add on_status_bar_render hook to narrow status bar width (P2)"
git rebase --continue

# Stop 3 (P3): just rename
git commit --amend -m "feat(cli): add on_status_bar_render hook to medium and wide status bar widths (P3)"
git rebase --continue

# Verify
git log --oneline upstream/main..HEAD
git diff upstream/main..HEAD -- .gitignore | wc -l  # → 0

# Force push (local branch name ≠ PR branch name)
git push origin feat/status-bar-hook-all-widths-fix:feat/status-bar-hook-all-widths --force
```
