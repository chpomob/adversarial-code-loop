# Git History Cleanup After Adversarial Loops

## Problem

Adversarial loops commit to the working branch with `git add -A`, which includes:
- `.adversarial-loop-*/` — build artifacts, review JSONs, fix output (MB each)
- `target/` — build cache (GB+)
- `PLAN_*.md`, `01_code.md` — planning and intermediate files

These bloat the git history and should NOT be shared.

## Solution A: filter-branch (for already-committed artifacts)

```bash
git filter-branch --force --index-filter '
  git rm -r --cached --ignore-unmatch \
    .adversarial-loop-p1-t1/ \
    .adversarial-loop-p1-t2/ \
    PLAN_CODEX.md \
    PLAN_CORRECTION.md \
    target/
' main..HEAD

git gc --prune=now --aggressive
```

## Solution B: fresh archive (for sharing)

Create a clean repo with just the source changes and a .gitignore:

```bash
# 1. Create clean base
mkdir /tmp/clean-repo && cd /tmp/clean-repo
git archive /path/to/original main | tar -x

# 2. Generate clean patches (excludes artifacts)
cd /path/to/original
for sha in <list-of-fix-commits>; do
  git format-patch -1 --stdout "$sha" -- . \
    :!.adversarial-loop-* :!.adversarial-review-chatter :!PLAN_*.md \
    :!01_code.md :!target/ \
    | (cd /tmp/clean-repo && git am --ignore-whitespace)
done

# 3. Add review artifacts (optional — useful for the recipient)
cp -r /path/to/original/.adversarial-review-chatter /tmp/clean-repo/
cd /tmp/clean-repo
git add .adversarial-review-chatter && git commit -m "chore: add review reports"

# 4. Create .gitignore
cat >> .gitignore << 'EOF'
target/
**/debug/
**/release/
chatter.db
.chatter.db-*
.idea/
.vscode/
*.swp
.DS_Store
Cargo.lock
EOF
git add .gitignore && git commit -m "chore: gitignore"

# 5. Zip
cd /tmp && zip -r clean-repo.zip clean-repo/
```

## Solution C: format-patch (lightest, no git history)

```bash
git format-patch main..HEAD -- . \
  :!.adversarial-loop-* :!.adversarial-review-chatter :!PLAN_*.md \
  :!01_code.md :!target/
```
Send the .patch files. Recipient applies with `git am`.
