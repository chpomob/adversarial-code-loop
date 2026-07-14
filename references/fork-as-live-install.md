# Fork-as-Live-Install: adversarial pipelines on the Hermes Agent repo

When the Hermes Agent install directory IS your development fork
(`git@github.com:<you>/hermes-agent.git` with `upstream` → `NousResearch/hermes-agent`),
running adversarial pipelines against `--workdir /home/<you>/.hermes/hermes-agent`
operates on the live codebase. This adds constraints beyond the normal isolated-repo pattern.

## Risk summary

| Risk | Normal repo | Fork-as-live-install |
|------|-------------|----------------------|
| Squash-merge into parent | Safe — repo isn't running | Commits go into live install's main branch |
| Stash-pop conflict | Git conflict, manually resolvable | Hermes may fail mid-session from inconsistent files |
| Loop branch switch | Invisible to user | Hermes restarts from loop branch if user starts a new session |

## Mitigations

1. **Always pass `--no-merge`.** Prevents the pipeline from squash-merging into your live main
   branch. The loop branch (`loop/<feature>/<N>`) stays preserved for you to review and merge
   manually: `git checkout main && git merge --squash loop/<feature>/<N>`.

2. **Pre-commit or stash manually before launching.** The auto-stash (pitfall #5) handles
   dirty trees, but if the pipeline aborts mid-way the stash-pop can conflict and leave your
   working tree in a mixed state while Hermes reads from it. Manual pre-commit is safer:
   `git stash push -u -m "pre-pipeline <date>"`.

3. **Restart Hermes after the pipeline.** After the pipeline finishes and restores the
   original branch, Hermes may still reference stale bytecode. Run `hermes update --check` or
   start a fresh session to pick up any compiled changes.

## When to ignore these constraints

- You explicitly want the pipeline output merged into your fork's main branch immediately
  (e.g. a fast-forward fix on your own branch with no upstream PR planned).
- You are running spec-only or plan-only phases (`adversarial-spec` / `adversarial-plan`)
  which write only `.md` files under `.adversarial-*/` and don't modify source code.

## Validated pairing

This pattern was validated 2026-07-13: Codex (GPT-5.6-Sol, reasoning=medium) DEV +
Claude Fable 5 REVIEW, `--workdir /home/chpo/.hermes/hermes-agent`, `--no-merge` on all
three stages (spec, plan, code loop). The repository had 4 dirty files and the auto-stash
restored them cleanly on exit.
