# Spec Writing for Adversarial Dev Loops

## Why specs matter
A well-written spec is the single biggest factor in achieving single-cycle APPROVED.
Codex (DEV) executes faithfully — garbage in, garbage out. Fable 5 (REVIEW) catches
spec-level omissions but can't fill gaps the spec creator left.

Five consecutive phases of Jeu de Cochons (2026-06-18) all approved in 1 cycle with Codex DEV +
Fable 5 REVIEW: Fondations, Moteur de règles, Animation, UI, Intégration.

## Spec structure that works

### 1. Context header
Tell the DEV what already exists so it doesn't recreate or break things:
```markdown
## Contexte
Le projet existe déjà avec src/config.js, src/types.js, src/main.js.
Tu ne modifies PAS ces fichiers — tu AJOUTES sous src/engine/.
```

### 2. File-by-file task list
Each file gets its own section with:
- Exact path (e.g. `src/engine/evaluate.js`)
- Function signatures with parameter and return types
- Edge cases and error handling
- What to import from existing modules

### 3. Test expectations
List exactly which test files to create and what each should verify.
The DEV will often write MORE tests than asked — this is good.
```markdown
## Tests
### tests/engine/dispatch.test.js
- Créer une partie à 2 joueurs
- ROLL_REQUESTED : vérifier que turnScore augmente
- BANK_REQUESTED hors phase DECIDING → doit retourner une erreur
- ...
```

### 4. Verification checklist
Exact commands that must pass:
```markdown
## Vérifications
1. `npm run test` — TOUS les tests passent
2. `npm run lint` — 0 erreur
```

### 5. Constraints section
Languages, frameworks, patterns to use or avoid:
```markdown
## Contraintes
- JavaScript ES2022+, modules ES, pas de TypeScript
- Pas de dépendance externe
- Le moteur est PUR : pas de DOM, pas de setTimeout
- Tout le code et commentaires en français
```

## Patterns that prevent FIX cycles

| Spec pattern | Prevents what failure |
|---|---|
| Exact function signatures with types | DEV invents wrong API |
| "Ne modifie PAS les fichiers existants" | DEV breaks Phase 1 in Phase 2 |
| List imports from existing modules | DEV re-implements things that already exist |
| Expected file structure tree | DEV puts files in wrong directories |
| Edge cases in task description | REVIEWER finds dozens of missing edge cases |
| "ZERO fichier image externe" | DEV uses external assets that don't exist |
| Verification commands (npm test, lint) | DEV doesn't run tests, CI would fail |

## When specs fail (observed)

| Spec problem | Result |
|---|---|
| Too vague ("implement the engine") | 20+ reviewer findings, 3+ FIX cycles |
| Missing imports list | DEV duplicates code or uses wrong modules |
| No file paths given | DEV puts everything in one file |
| No constraints on language | DEV mixes French/English or uses TypeScript |
| No test expectations | DEV writes zero tests, reviewer flags it |

## The `all_fixed=False` + `APPROVED` pattern

When the FIXER reports `all_fixed=False` with 0 bytes written but the VERIFIER
still APPROVES, this means the FIXER (Codex) pushed back on the REVIEWER's (Claude)
findings and the VERIFIER (also Claude) agreed with the pushback. This is a feature,
not a bug — the adversarial loop correctly resolved a disagreement without code
changes. It means the original BUILD code was already correct and the reviewer's
findings were either invalid or not applicable.

## Recovery spec for prose-overwritten files

When a DEV (especially Claude/Fable 5 via tmux) overwrites a source file with a
Markdown implementation report instead of executable code, the fix needs a
specialized spec that explicitly forbids prose. This spec must:

1. **State the prohibition at the top, in caps, in French:**
   ```
   PAS de prose, PAS de rapport — TU ÉCRIS DU CODE JAVASCRIPT EXÉCUTABLE
   ```

2. **Tell the DEV to READ the corrupted file** and restore it as code:
   ```
   Lis le controller.js CASSÉ actuel pour comprendre ce qui doit être restauré
   ```

3. **List the specific bugs** from the review that need fixing (so the DEV
   has a concrete TODO, not an open-ended prompt)

4. **Include verification commands** that would immediately fail on prose:
   ```
   `src/controller.js` est du JavaScript valide (pas du Markdown !!!)
   ```

5. **Use Codex as DEV** for the recovery loop — Codex writes executable code
   reliably, while Claude may repeat the prose-overwrite pattern.

Validated 2026-06-18 (jeu-de-cochons Phase 6b): Codex DEV + Codex REVIEW, 4 findings,
1 cycle APPROVED, controller.js restored from Markdown back to working JS.
