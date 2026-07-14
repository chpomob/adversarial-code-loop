# Plan format constraints

The `--plan` mode parser (`parse_plan` in phase_plan.py) has strict format requirements.

## Single-line bullet values

Bullet items MUST be `- **Key:** value` on a single line. Multi-line indented
lists are NOT supported:

### Correct
```markdown
- **Files:** /path/to/file1, /path/to/file2
- **Dependencies:** [P1, P2]
```

### INCORRECT — will not parse
```markdown
- **Files:**
  - /path/to/file1
  - /path/to/file2
```

## Step headings

Each step must begin with `### <ID>: <title>`:

```markdown
### P1: Title goes here
```

## Accepted keys

| Key | Format | Example |
|-----|--------|---------|
| `Files` | Comma-separated paths | `/a, /b, /c` |
| `Dependencies` | Python list literal | `[P1, P3]` |
| `Description` | Plain text | `Add feature X` |
| `Tests` | Plain text | `Verify with cargo test` |
| `Risks` | Plain text | `Deadlock if lock held` |
