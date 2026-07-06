# v4 Implementation Plan — Step by Step

## Principes
- Chaque étape produit un livrable testable (compile, test unitaire, ou git diff vérifiable)
- `adversarial_loop_v3.py` reste accessible inchangé jusqu'à la fin
- Chaque étape est implémentée via adversarial loop (Codex DEV + GLM/Claude REVIEW)
- Les tests sont dans le même commit que l'implémentation

---

## Étape 0 : Préparation

**Livrable :** Structure de dossiers + v3 préservée + preuve que tout marche

```bash
# Copie de sauvegarde de v3
cp adversarial_loop.py adversarial_loop_v3.py

# Créer structure v4
mkdir -p scripts/phases/
touch scripts/__init__.py
touch scripts/phases/__init__.py
```

**Validation :**
```bash
# v3 toujours accessible
python3 adversarial_loop_v3.py --help  # doit marcher
# v4 skeleton existe
ls scripts/phases/__init__.py
```

---

## Étape 1 : gitops.py — le coeur git

**Fichier :** `adversarial-common/gitops.py`

**Fonctions à implémenter :**

```python
# Toutes les fonctions lèvent GitError (exception personnalisée) en cas d'échec

def ensure_git_available() -> bool
  # Vérifie que git est installé et version >= 2.0

def detect_enclosing_repo(workdir: str) -> Optional[str]
  # Remonte les parents pour trouver un .git existant

def auto_init(workdir: str) -> None
  # git init + commit initial "Initial commit" + user.name/user.email locaux

def stash_dirty(workdir: str) -> Optional[str]
  # git stash push, retourne l'identifiant du stash

def unstash(workdir: str, stash_id: str) -> None
  # git stash pop

def create_loop_branch(workdir: str, feature: str, parent_branch: str) -> str
  # Crée loop/feature/N (N = max existant + 1)
  # Retourne le nom de la branche

def record_branch_point(workdir: str) -> str
  # Retourne le SHA du merge-base avec la branche parente

def commit_all(workdir: str, message: str) -> None
  # git add -A && git commit
  # Si rien à commit, force un commit vide

def get_diff(workdir: str, base: str, head: str = "HEAD") -> str
  # Retourne le diff texte entre base et head

def squash_merge(workdir: str, feature: str, parent: str, message: str) -> None
  # Squash + merge dans parent

def reject_marker(workdir: str, feature: str, message: str) -> None
  # Commit "[REJECTED] ..." sur la branche de loop

def tag_with_evidence(workdir: str, tag_name: str, evidence_file: str) -> None
  # Crée un tag git annoté avec le contenu de final.json

def ensure_gitignore(workdir: str, pattern: str) -> None
  # Ajoute .adversarial-loop/ au .gitignore si pas présent

def get_current_branch(workdir: str) -> str
def branch_exists(workdir: str, name: str) -> bool
def delete_branch(workdir: str, name: str) -> None
def sanitize_feature_name(name: str) -> str
```

**Validation :**
```bash
python3 -c "from adversarial_common import gitops; print(gitops.ensure_git_available())"
# Tests unitaires dans un temp dir
cd /tmp && mkdir test-git && cd test-git
python3 -c "
import sys; sys.path.insert(0, '.')
from adversarial_common import gitops
gitops.auto_init('.')
assert gitops.get_current_branch('.') == 'main'
print('gitops OK')
"
```

---

## Étape 2 : Mise à jour des personnas (git-aware)

**Fichiers :** `adversarial-common/personas/{builder,critic,fixer,verifier,judge,builder-pi,fixer-pi}.md`

Chaque persona reçoit un bloc en tête :

```markdown
## Git workflow rules

You are working inside an automated git-based pipeline. Your actions follow these rules:

- **BUILD phase:** Write complete, working code. All new and modified files will be staged and committed automatically after you finish.
- **REVIEW phase:** You receive a `git diff` showing exactly what changed. Each finding MUST reference a real file and line visible in the diff. Do NOT report pre-existing issues outside the diff.
- **FIX phase:** Address each finding concretely. Your changes are committed as a new fix round.
- **VERIFY phase:** Check each finding against the current code. A finding is **resolved** if the problematic code is gone or corrected. Mark it **rejected** with evidence if you disagree. Mark it **disputed** if you need the arbiter.
- **ARBITER phase:** Resolve disputed findings. Your decision is final.
```

**Validation :** lecture humaine + vérification que chaque fichier a le bloc git rules.

---

## Étape 3 : Phase modules

### 3a — phase_build.py

```python
def run_build(spec, dev_cmd, workdir, timeout, providers) -> dict
  # Run DEV model
  # Stage and commit: "build: <feature> — <summary>"
  # Return {"exit_code": 0, "commit_sha": "..."}
```

### 3b — phase_review.py

```python
def run_review(diff_text, review_cmd, providers, schema) -> dict
  # Run REVIEW model with diff as input
  # Validate JSON output against v4 schema
  # Return {"findings": [...], "verdict": "..."}
```

### 3c — phase_fix.py

```python
def run_fix(findings, dev_cmd, workdir, timeout, providers) -> dict
  # Present findings to DEV model
  # Stage and commit: "fix: <feature> — address finding(s) (round N)"
  # Return {"exit_code": 0, "commit_sha": "..."}
```

### 3d — phase_verify.py

```python
def run_verify(findings, diff_text, review_cmd, providers) -> dict
  # Run VERIFY model with findings + diff
  # Validate JSON output
  # Return {"results": [{"id": "A1", "status": "resolved"}, ...], "verdict": "APPROVE|REJECT"}
```

### 3e — phase_arbiter.py

```python
def run_arbiter(findings, dev_cmd, review_cmd, arbiter_cmd, providers) -> dict
  # Run ARBITER model with unresolved disputes
  # Return {"verdict": "APPROVE|REJECT", "conditions": [...]}
```

### 3f — phase_git.py

```python
def setup_git(workdir, feature, parent_branch) -> dict
  # Wrapper autour de gitops.py pour le setup complet de PHASE 0
  # Return {"branch": "loop/feature/N", "branch_point": "sha", "stash_id": "..."}

def finalize_git(workdir, feature, parent, verdict, evidence_path) -> None
  # Wrapper pour merge/squash ou reject marker
```

**Validation pour chaque phase :**
```bash
python3 -c "from scripts.phases.phase_build import run_build; print('build phase loaded')"
python3 -c "from scripts.phases.phase_review import run_review; print('review phase loaded')"
# ... etc pour toutes les phases
```

---

## Étape 4 : Orchestrateur — adversarial_loop.py

Le fichier central qui :
1. Parse les arguments (argparse)
2. Lit la spec
3. Appelle `phase_git.setup_git()` (PHASE 0)
4. Appelle `phase_build.run_build()` (PHASE 1)
5. Appelle `phase_review.run_review()` (PHASE 2)
6. Boucle : fix → verify (PHASES 3-4)
7. Optionnellement arbiter (PHASE 5)
8. Appelle `phase_git.finalize_git()` (merge/reject)

**Points critiques :**
- Gestion des timeouts (subprocess timeout + cleanup)
- Gestion des états (state.json pour --resume)
- Lockfile pour éviter les doubles runs
- Traduction des findings IDs entre les phases (les IDs doivent être stables)
- Tous les appels aux modèles passent par `providers.py` de `adversarial-common`

**Validation :**
```bash
# Test avec un spec minimal et un répertoire temporaire
cd /tmp/test-v4
echo "# test spec" > spec.md
python3 /path/to/adversarial_loop.py --spec spec.md --max-loops 1 --no-arbiter
git log --oneline  # doit montrer les commits build + fix + merge
```

---

## Étape 5 : SKILL.md — documentation

Documenter :
- Nouveaux flags (--build-cmd, --test-cmd, --no-merge, --feature, --resume)
- Nouveaux exit codes
- Workflow git
- Findings JSON schema
- Pitfalls de la v4 (merge conflict, dirty tree, nested repo, etc.)
- Migration guide depuis v3

---

## Étape 6 : Tests d'intégration

Scénarios à valider :
1. **Happy path** — spec simple → BUILD → REVIEW → APPROVED → merge
2. **Dirty working tree** — travail non commité avant le lancement → stash/restore
3. **Aucun git** — `git` pas installé → message clair, exit 2
4. **Multiples loops** — spec complexe → 2+ cycles FIX/VERIFY
5. **Max-loops REJECT** — spec impossible → REJECT, branche conservée
6. **Merge conflict** — parent avance pendant la loop → abort, exit 1
7. **Malformed JSON** — REVIEW retourne du texte pas du JSON → retry puis exit 1
8. **Timeout** — DEV dépasse le timeout → cleanup puis exit 1
9. **--no-merge** — loop réussie, pas de merge → vérifier que la branche existe
10. **--build-cmd** — échec du build → REJECT avant même la REVIEW
11. **--resume** — crash au milieu → state.json existe, --resume reprend

Chaque test est un script bash dans `tests/`:
```bash
#!/bin/bash
# test_happy_path.sh
cd $(mktemp -d)
git init && git add -A && git commit -m "init"
echo "# Add a function" > spec.md
python3 adversarial_loop.py --spec spec.md --max-loops 1 --no-arbiter
# Vérifier que git log montre les commits
[ $(git log --oneline | wc -l) -ge 2 ] && echo "PASS" || echo "FAIL"
```

---

## Étape 7 : Remplacer v3

```bash
# Quand tout est vert :
mv adversarial_loop_v3.py scripts/adversarial_loop_v3.py  # archivé
# Le nouveau adversarial_loop.py est en place
```

**Validation finale :**
```bash
python3 adversarial_loop.py --help  # v4
# Ancien workflow (sans git) doit encore marcher
python3 adversarial_loop.py --spec spec.md --workdir /tmp/test --no-merge
```

---

## Résumé des étapes

| # | Étape | Fichier(s) | Tests |
|---|-------|-----------|-------|
| 0 | Préparation + backup v3 | scripts/ + _v3.py backup | v3 --help marche |
| 1 | gitops.py | adversarial-common/gitops.py | Tests unitaires dans /tmp |
| 2 | Personnas git-aware | 7 fichiers .md | Lecture humaine |
| 3a | phase_build.py | scripts/phases/phase_build.py | Import OK |
| 3b | phase_review.py | scripts/phases/phase_review.py | Import OK |
| 3c | phase_fix.py | scripts/phases/phase_fix.py | Import OK |
| 3d | phase_verify.py | scripts/phases/phase_verify.py | Import OK |
| 3e | phase_arbiter.py | scripts/phases/phase_arbiter.py | Import OK |
| 3f | phase_git.py | scripts/phases/phase_git.py | Import OK |
| 4 | Orchestrateur | scripts/adversarial_loop.py | Happy path /tmp |
| 5 | Documentation | SKILL.md | Relecture |
| 6 | Tests intégration | tests/*.sh | 11 scénarios |
| 7 | Remplacement v3 | scripts/adversarial_loop.py | Tout vert |
