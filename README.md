# adversarial-code-loop

**BUILD → REVIEW → (FIX → VERIFY)^N → ARBITER.** A git-native adversarial development pipeline where one model writes code, another critiques the real `git diff`, the first fixes, the second validates, and an optional arbiter resolves deadlocks.

For Hermes Agent, Claude Code, Codex, or any LLM CLI.

## How it works

Every loop runs on an isolated git branch (`loop/<feature>/<N>`):

```
PHASE 0 ──→ GIT SETUP   (branch, stash, identity, gitignore)
PHASE 1 ──→ BUILD        (DEV model writes code, commits)
PHASE 2 ──→ REVIEW       (CRITIC model inspects `git diff <branch>..HEAD`)
PHASE 3 ──→ FIX          (DEV addresses findings, commits)
PHASE 4 ──→ VERIFY       (CRITIC checks each finding resolved)
   └── loop 3-4 until APPROVED or max-loops
PHASE 5 ──→ ARBITER      (resolves last dispute, optional)
MERGE     ──→ squash-merge into parent, or [REJECTED] marker
```

## Comparison

| Feature | adversarial-code-loop | claude-wizard | opencode-spec-kit |
|---------|----------------------|---------------|-------------------|
| Git-native (reviews real diffs) | ✅ | ❌ | ❌ |
| Multi-model (Codex DEV + Claude REVIEW) | ✅ | ❌ Single model | ❌ |
| Per-step plan mode | ✅ 15-step plan, multi-repo | ❌ | ❌ |
| Resume on interrupt | ✅ `--resume` from `state.json` | ❌ | ❌ |
| Build/test gates | ✅ `--build-cmd` / `--test-cmd` | ❌ | ❌ |

## Quick start

```bash
python3 scripts/adversarial_loop.py \
  --spec /path/to/spec.md \
  --workdir /path/to/project \
  --dev-cmd "codex exec --sandbox workspace-write" \
  --review-cmd "pi -p --provider zai --model glm-5.2 --thinking high"
```

See `SKILL.md` for full CLI reference and 30+ validated pitfalls.

## Dependencies

- Python ≥ 3.11
- Git ≥ 2.5
- A DEV CLI (codex, pi, claude-tmux, …)
- A REVIEW CLI (pi, claude-tmux, …)

Uses `adversarial-common` as the shared engine.

## License

MIT
