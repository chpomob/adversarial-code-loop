# Review on Committed Code (not just diff)

## The principle

The reviewer works **inside the loop branch checkout** with full access to all files on disk. They are NOT given a concatenated diff on stdin. This is different from v3.

**Why:**
- A diff shows changed lines but hides surrounding context (function body, imports, module structure, architecture)
- Full file access lets the reviewer explore related files, grep for patterns, and understand the broader impact
- The loop branch already has the code committed — `git diff HEAD~1..HEAD` shows exactly what changed

## How it works

`phase_review.py` builds a prompt that tells the reviewer:

```
You are reviewing code in workdir <path> (loop branch HEAD).
To see what changed in the last commit:
  git diff HEAD~1..HEAD   — line-by-line diff
  git log -1 -p            — full diff with commit message
To inspect full context:
  cat <filepath>
Output JSON ONLY.
```

The reviewer then:
1. Runs git commands to see the diff
2. Reads files from disk for context (`cat`, `grep`, `head`, etc.)
3. Produces findings referencing real files and lines

## Advantages over stdin-based review

| Concern | stdin (v3) | Committed code (v4) |
|---------|-----------|-------------------|
| Context | Only changed lines | Full files available |
| Exploration | Impossible | `cat`, `grep`, `git log` |
| Token cost | Full codebase concatenated | Minimal prompt, model navigates |
| Architecture | Hidden | Visible |
| Imports/types | Not available | Can check |

## Caveats

- The reviewer must have `git` available and the workdir must be a git checkout of the loop branch
- For models that can't run shell commands (some API-only providers), fall back to providing the diff text on stdin
- The prompt is kept under 1K tokens — the model navigates the rest itself
