# Post-loop extraction workflow

**The loop generates, it does not write.** Unless the FIXER returned a usable
`updated_code` + `target_file`, always extract the code from the artifacts and
apply it manually. An exit verdict of REJECT is expected when the file on disk
hasn't changed — don't re-run the loop, just extract and apply.

## Standard extraction (single file)

```python
import re
from pathlib import Path

# 1. Read BUILDER output
content = Path(".adversarial-loop/01_code.md").read_text()

# 2. Extract the code block and write it
match = re.search(r'```python\n(.+?)\n```', content, re.DOTALL)
if match:
    Path("scripts/target.py").write_text(match.group(1))
```

Then apply the FIXER's improvements from `loop_N_03_fix.md`: it outputs JSON
with `"action": "fixed"` entries, each containing a `code_diff` — apply them
as patches on top of the BUILDER code.

## Multi-file extraction (e.g. C++ firmware)

When the pipeline runs **without `--workdir`**, the BUILDER produces ALL code
inline in `01_code.md`, in spec order:

```python
import re
blocks = re.findall(r'```cpp\n(.*?)```', content, re.DOTALL)
# Block 0 = header (.h)
# Block 1 = implementation (.cpp)
# Block 2+ = includes, menu entries, tests
```

Write each block to its file, patch shared files (includes, menu entries) with
targeted edits, then run the project build to confirm compilation.

## FIXER sandbox deadlock sub-case

When the FIXER (Codex `--sandbox danger-full-access`) writes files to disk but
returns empty `updated_code`, the VERIFIER systematically REJECTs. The BUILD
output is typically high-quality and complete — recover from it:

1. Confirm `01_code.md` and `02_review.json` exist
2. Kill the looping pipeline (it will not converge)
3. Extract code blocks from `01_code.md` (see above)
4. Check for stray sandbox artifacts and remove them
5. Apply the review findings from `02_review.json` manually where critical
6. Build and fix remaining compilation errors

**Prevention:** run with `--workdir` only when the FIXER must edit existing
files. For new code, omit `--workdir` so the FIXER produces inline
`updated_code`. Or use `--max-loops 1` and extract the BUILD output directly,
skipping FIX/VERIFY entirely (faster, no deadlock).

## Verification (always)

```bash
python3 -m py_compile <extracted>.py        # or the project build
```

Verify each spec finding programmatically when feasible (grep for the
expected constructs), compile, run tests, then commit.
