# pi and the Sentinel File Protocol

## Problem
pi (the CLI coding agent) does NOT support the sentinel file protocol used by `adversarial-code-loop` in `--project-dir` mode.

The sentinel protocol works as follows:
1. The script sends a file tree listing to the model via stdin
2. The model explores using its built-in tools (Read/Bash)
3. After analysis, the model writes findings to a file and creates a `done.sentinel` marker
4. The script polls for `done.sentinel` and reads the findings file

Claude (via claude-tmux-wrapper) and Codex (via `-C <path>`) support this protocol. pi does not:
- pi receives the persona via stdin in interactive mode
- pi can Read/Bash to explore the project
- But pi's output goes to stdout, not to a sentinel file
- The script blocks waiting for done.sentinel, which never arrives

## Workaround
Always use `--dir` or `--stdin` mode when pi is a DEV, FIXER, or REVIEW model in any adversarial pipeline:

```bash
# WRONG - blocks forever
--dev-cmd "pi --provider zai --model glm-5.2"
# (script uses --project-dir)

# CORRECT
--dev-cmd "pi --provider zai --model glm-5.2"
# (script uses --dir or --stdin)
```

For `adversarial-code-review`, use `--dir <src_dir>` to concatenate files:
```bash
--dir firmware/src
```

## Validated
2026-07-01, omnisense firmware: `--project-dir` with pi/GLM-5.2 as DEV produced no output after 5+ minutes. Switching to `--dir firmware/src` worked correctly.
