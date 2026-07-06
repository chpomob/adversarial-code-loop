# Single-File Fix Workflow (adversarial review → adversarial loop)

Pattern validé 2026-06-02 pour corriger un fichier unique avec le pipeline complet.

## Workflow

```
adversarial-code-review --file target.py   → 15 findings
                      ↓
Écrire spec.md contenant : code actuel + findings + instructions de fix
                      ↓
adversarial-loop --spec spec.md --no-arbiter --max-loops 2 --timeout 900
                      ↓
Boucle APPROUVÉE → script écrit sur le disque
                      ↓
python3 -m py_compile target.py && python3 -m unittest test_target.py
```

## Préparation de la spec

La spec doit contenir :

1. **Le code source complet** du fichier à modifier (dans un bloc ```python)
2. **Tous les findings** de l'adversarial review, organisés par sévérité
3. **Instructions claires** de ce que chaque fix doit accomplir
4. **L'instruction critique** : `Produce ALL code INLINE in markdown code blocks. Do NOT attempt file writes or ask for permission. If Write is denied, output the complete source code inline in your response as ```python blocks. Do NOT wait for permission — deliver the code inline immediately.`

## Paramètres clés

| Paramètre | Valeur | Raison |
|-----------|--------|--------|
| `--spec` | spec.md | Pas de pipe stdin → pas de conflit background |
| Sans `--workdir` | (omettre) | Évite le deadlock sandbox `-C` → VERIFIER rejette |
| `--max-loops 2` | 2 loops max | 1 seul cycle suffit souvent ; 2e pour rattrapage |
| `--no-arbiter` | pas d'arbitre | Pas nécessaire si les findings sont clairs |
| `--timeout 900` | 15 min | Codex FIX + Opus peut prendre 10+ min |

## Ce qui se passe

1. **BUILDER (Codex)** : réécrit le fichier complet avec les fixs de la spec + typage strict + tests
2. **CRITIC (Claude Opus)** : trouve des findings dans le code généré (architecture, edge cases, tests)
3. **FIXER (Codex)** : adresse chaque finding, fournit `updated_code` + `target_file`
4. **VERIFIER (Claude Opus)** : valide que tous les findings sont résolus → APPROVE ou REJECT
5. Le script écrit `updated_code` sur le disque via le `target_file` du FIXER

## Vérifications post-loop

- Syntaxe : `python3 -m py_compile target_file`
- Tests : `python3 -m unittest test_file.py` (si le BUILDER en a produit)
- Vérifier le rapport `final.md` : verdict APPROVE = les findings sont résolus

## Cas réel : quota-status plugin (2026-06-02)

- Fichier : `__init__.py` (143 lignes → 342 lignes)
- Findings initiaux : 15 (1 blocker, 5 major, reste minor/nit)
- Spec : 10KB (code + 15 findings + instructions)
- Pipeline : ~11 min (BUILD 2min + CRITIQUE 2min + FIX 5min + VERIFY 2min)
- Résultat : APPROUVÉ cycle #1, 11 tests passent
- Améliorations : background thread, thread-safe cache, backoff, types stricts, logging

## Quand utiliser ce workflow

- Fichier unique (< 500 lignes) avec des problèmes identifiés par review
- Plugin, module, ou helper qui peut être réécrit sans dépendre du contexte projet
- Quand le coût (4 appels LLM) est justifié par la criticité du code

## Quand NE PAS utiliser

- Projet multi-fichiers → utiliser `--workdir` (et gérer le deadlock sandbox)
- Simple correction de syntaxe → `patch()` directement
- Exploration rapide → écriture manuelle
