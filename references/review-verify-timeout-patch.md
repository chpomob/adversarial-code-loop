# REVIEW/VERIFY timeout propagation fix

## Symptom

`phase_review.run_review()` and `phase_verify.run_verify()` call
`providers.run_cmd()` with **no `timeout` argument**, defaulting to 600s
regardless of `--timeout` passed to the pipeline. Claude Fable 5 with
extended thinking (8-12+ min silent) dies with:

```
X review failed: REVIEW exited 124: TIMEOUT after 600s
```

The stderr says `TIMEOUT after 600s` — confirming the default, not the
pipeline's `--timeout` value.

## Root cause

Three files need the same pattern that `phase_build.run_build()` and
`phase_fix.run_fix()` already use:

| File | Missing parameter | Missing argument to `run_cmd` |
|------|------------------|------------------------------|
| `scripts/phases/phase_review.py` | `run_review()` signature | `providers.run_cmd()` call |
| `scripts/phases/phase_verify.py` | `run_verify()` signature | `providers.run_cmd()` call |
| `scripts/adversarial_loop.py` | call sites for both | `timeout=args.timeout` |

## Patch (validated 2026-07-13)

### phase_review.py
```python
def run_review(
    diff_text: str,
    review_cmd: str,
    providers: Any,
    jsonio: Any,
    workdir: str = "",
    timeout: int = 600,       # ← ADD
) -> dict:
    ...
    def _attempt(prompt_text):
        stdout, stderr, code = providers.run_cmd(
            review_cmd, stdin_text=prompt_text, role="critic",
            timeout=timeout,   # ← ADD
        )
```

### phase_verify.py
```python
def run_verify(
    findings: list,
    diff_text: str,
    review_cmd: str,
    providers: Any,
    jsonio: Any,
    timeout: int = 600,       # ← ADD
) -> dict:
    ...
    def _attempt(prompt_text):
        stdout, stderr, code = providers.run_cmd(
            review_cmd, stdin_text=prompt_text, role="verifier",
            timeout=timeout,   # ← ADD
        )
```

### adversarial_loop.py
Two call sites:

Line ~385 (REVIEW):
```python
review = phase_review.run_review(
    diff, review_cmd, providers, jsonio,
    workdir=workdir, timeout=args.timeout)
```

Line ~420 (VERIFY):
```python
verify = phase_verify.run_verify(
    findings, diff, review_cmd, providers, jsonio,
    timeout=args.timeout)
```

## Verification

The ad-hoc check (run after patching) passed:
```
PASS — timeout propagation verified:
  ✓ phase_review.py: run_review(timeout=…) + run_cmd(timeout=timeout)
  ✓ phase_verify.py: run_verify(timeout=…) + run_cmd(timeout=timeout)
  ✓ adversarial_loop.py: both calls pass timeout=args.timeout
```

## Downstream impact

Without this fix, Claude REVIEW on any non-trivial diff (>3 files changed or
threading changes) consistently dies at 600s regardless of `--timeout`. The
BUILD and FIX phases are unaffected (they already pass timeout correctly).
