# pi (GLM-5.2) reviews wrong git repo despite correct subprocess cwd

## Symptom

GLM-5.2 (via `pi -p --provider zai --model glm-5.2 --thinking high`) REVIEW
output in `02_review.json` shows a commit hash and file paths that do NOT
belong to the pipeline's `--workdir`. Example:

```json
{
  "id": "A1",
  "file": "(commit 92ce650 \"build: refacto-p6\")",
  ...
}
```

The review claims the commit under review is empty (`git diff HEAD~1..HEAD`
produces nothing), but the BUILD commit actually exists on the correct branch
in `--workdir`. The reviewer is examining a completely different git repo
(typically `hermes-agent/` if invoked from its scripts directory).

## Root cause

`pi` runs as a subprocess of `adversarial_loop.py`. The pipeline passes
`cwd=workdir` to `subprocess.Popen(cwd=...)`, so `pi` *starts* in the correct
directory. However, `pi` spawns its own internal file-access and git tools
which may navigate to a parent or sibling repo by crawling `.git` directories
upward, ignoring the initial CWD. Unlike `codex exec -C <dir>` (which has an
explicit `-C` directory flag) or `claude-tmux --cwd <dir>` (which passes `-c`
to tmux), `pi` has no equivalent working-directory flag.

## Validation

This was reproduced on 2026-07-13 with `pi -p --provider zai --model glm-5.2
--thinking high` reviewing `hermes-quota-status` plugin (P9: keyring
hardening, 320 lines added). The BUILD commit existed on
`loop/feature/P9/1` in the plugin repo, but GLM's REVIEW found commit
`92ce650` from the hermes-agent repo and reported an empty diff. P8 and P14
(also reviewed by GLM-5.2 in the same pipeline) succeeded — the misdirection
is intermittent, probably depending on which CWD `pi` internal subprocesses
inherit.

## Diagnosis

1. Check `02_review.json` — if `"file": "(commit <SHA> ...)"` instead of a
   real path like `"__init__.py"`, the reviewer is in the wrong repo.
2. Verify the SHA exists: `git cat-file -t <SHA>` in `--workdir` vs. in
   sibling repos. If it only exists in another repo, pi is misdirected.
3. The BUILD commit on the loop branch *is* correct — `git log
   loop/<feature>/<step>/<N>` shows the commit with the right content.

## Workaround

The code on the loop branch is valid — only the review was misdirected.
Merge manually:

```bash
cd <workdir>
git merge --squash loop/<feature>/<step>/<N>
git commit -m "squash: <feature>/<step> — manual merge"
```

Then delete the loop branch and continue. Verify with `git diff HEAD~1..HEAD
--name-only` that the expected files changed. Then create a reduced plan
(pitfall #35) for remaining steps.

## Long-term fix

Either:
- Add a `--cwd` equivalent to `pi` CLI
- Detect in `phase_plan.execute_step()` that the reviewer is in the wrong
  repo by checking `git rev-parse --show-toplevel` from the reviewer's CWD
  before executing the review prompt
- Restrict `pi` to a directory boundary via `unshare --mount` or chroot
