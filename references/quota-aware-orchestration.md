# Quota-Aware Multi-Step Adversarial Orchestration

Comment structurer un pipeline adversarial qui s'étend sur plusieurs
étapes, avec reprise automatique après exhaustion de quota.

---

## Problème

Quand on enchaîne 10+ étapes adversarial (tests + code), on finit
par heurter les quotas :
- **Fable 5** : quota 5h glissant (reset ~4h10 CET)
- **Codex** : rate limits OpenAI / usage caps

Perdre le travail à chaque quota = inefficace. La solution : sauver
l'état, programmer un cron, reprendre exactement là où on était.

---

## Principe général

```
Pour chaque étape :
    1. Hermes écrit la spec
    2. Boucle adversarial — tests (DEV=Fable5, REVIEW=Codex)
    3. Boucle adversarial — code (DEV=Fable5, REVIEW=Codex)
    4. Hermes compile, test, commit

Si quota atteint :
    1. Sauver l'état dans .omnisense-state.json
    2. Programmer cron pour reprise après reset
    3. Le cron reprend exactement à la phase bloquée
```

## Configuration recommandée

### DEV = Fable 5, REVIEW = Codex

Pour le code embarqué C/C++ où Fable 5 excelle en génération
et Codex en review firmware :

```bash
# Boucle tests (max-loops 1 suffit)
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/step-spec.md \
  --workdir /path/to/project \
  --dev-cmd "python3 ~/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model fable" \
  --review-cmd "codex exec --skip-git-repo-check --sandbox read-only" \
  --max-loops 1 --no-arbiter --timeout 600 \
  --out .adversarial-loop-step-tests

# Boucle code (max-loops 3 pour itérations correctives)
python3 ~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py \
  --spec /tmp/step-spec.md \
  --workdir /path/to/project \
  --dev-cmd "python3 ~/.hermes/skills/autonomous-ai-agents/hermes-agent/scripts/claude-tmux.py --yolo --model fable" \
  --review-cmd "codex exec --skip-git-repo-check --sandbox read-only" \
  --max-loops 3 --no-arbiter --timeout 900 \
  --out .adversarial-loop-step-code
```

---

## Machine à états du pipeline

```
pending → running_tests → tests_ok → running_code → approved → done
                               │                        │
                               ▼                        ▼
                         quota_blocked             compile_fail
                               │                        │
                               ▼                        ▼
                           cron wait               arbitrage
```

### Fichier d'état (.omnisense-state.json)

```json
{
    "phase": "phase0",
    "step": 1,
    "step_name": "spsc-queue",
    "step_status": "running_tests",
    "attempt": 1,
    "quota_blocked_until": null,
    "quota_blocked_source": null,
    "commits_made": 0
}
```

`step_status` : `pending`, `running_tests`, `tests_ok`,
`running_code`, `approved`, `compile_fail`, `quota_blocked`, `done`.

---

## Quota data sources (par provider)

Avant de pouvoir orchestrer, il faut savoir **où trouver les données de quota**.
Chaque provider expose l'info différemment — certains ne l'exposent pas du tout
via leur API publique.

### Claude (Anthropic)

| Champ | Valeur |
|---|---|
| Auth | OAuth token (`~/.claude/.credentials.json`) |
| Endpoint | `https://api.anthropic.com/api/oauth/usage` |
| Données | `five_hour.utilization` (%), `seven_day.utilization` (%), `resets_at` |
| Fiabilité | Excellente — endpoint officiel |
| Reset | 5h glissant |

### Codex (OpenAI)

| Champ | Valeur |
|---|---|
| Auth | OAuth token (`~/.codex/auth.json`) |
| Endpoint | `https://chatgpt.com/backend-api/wham/usage` |
| Données | `rate_limit.primary_window.used_percent` (%), `reset_at` (epoch) |
| Fiabilité | Bonne — endpoint officiel |
| Reset | Fenêtre glissante ~1h |

### Gemini (Google AI Studio) — via API publique

| Champ | Valeur |
|---|---|
| Auth | `GOOGLE_API_KEY` (API key) |
| Endpoint | `generativelanguage.googleapis.com/v1beta/models:generateContent` |
| Données | **Aucune** — pas de headers `x-ratelimit-*` sur les réponses 200 |
| Fiabilité | Nulle pour le quota — cf. Hermes bug #21399 |
| Détection 429 | Body JSON avec `QuotaViolation` contenant `quota_dimension`, `quota_limit`, `quota_usage` |

⚠️ L'API publique Gemini **ne retourne aucun header de quota** sur les réponses 200.
Google redirige vers https://aistudio.google.com/rate-limit pour voir ses limites.
La seule info fiable vient des réponses 429 (réactif).

### Gemini (Google Cloud Code / Antigravity) — via API interne OAuth

| Champ | Valeur |
|---|---|
| Auth | OAuth Google (compte perso, pas API key) |
| Endpoint 1 | `POST https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels` |
| Endpoint 2 | `POST https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist` |
| Données (modèles) | `models[].quotaInfo.remainingFraction` (0.0–1.0), `resetTime` (ISO), `isExhausted` |
| Données (plan) | `planInfo.monthlyPromptCredits`, `availablePromptCredits`, `planInfo.planType` |
| Fiabilité | Excellente — c'est l'API interne des IDEs Google |
| Reset | Par modèle via `resetTime` (souvent ~7 jours) |

Le token OAuth est stocké par `agy` dans `~/.gemini/oauth_creds.json` (refresh_token + access_token).

**Détails OAuth agy :** agy utilise **PKCE** (Proof Key for Code Exchange) pour l'authentification
— pas de `client_secret`. Le binaire contient les credentials OAuth suivants :
- Client ID : `884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com`
- Client secret : `[GOOGLE_CLOUD_CODE_CLIENT_SECRET]`
- ※ Impossible de rafraîchir le `refresh_token` d'agy en externe car émis par une app OAuth Google différente (consumer auth). Tentatives → `unauthorized_client`.

**antigravity-usage** a son propre flux OAuth (client ID distinct : `1071006060591-tmhssin2h21lcre235vtolojh4g403ep` — installed-app public credential, not confidential).
Il nécessite un `login` one-time séparé (n'utilise pas les tokens d'agy) :
```bash
npm install -g antigravity-usage   # ou via npx
antigravity-usage login            # one-time browser OAuth
antigravity-usage --json           # toutes les infos quota
antigravity-usage --method google  # force API Cloud (pas IDE local)
```
Son cache dure 5 min ; utiliser `--refresh` pour forcer un rafraîchissement.

**Approche alternative** (quand agy tourne en CLI) : se connecter au Language Server HTTP d'agy.
À chaque démarrage, agy écoute sur un port aléatoire (visible dans le log : `cli.log` contient
"Language server listening on random port at XXXX for HTTP"). L'outil antigravity-usage détecte
ce port en listant les ports d'écoute du process agy, puis interroge un endpoint Connect API local.
C'est le mode `--method local` d'antigravity-usage.
- ✅ Pas de login séparé
- ❌ Ne marche que quand agy tourne (pas pour background status bar)

La réponse `fetchAvailableModels` ressemble à :
```json
{
  "models": {
    "gemini-2.5-flash": {
      "displayName": "Gemini 2.5 Flash (Medium)",
      "quotaInfo": {
        "remainingFraction": 0.4,
        "resetTime": "2026-06-19T15:00:00Z",
        "isExhausted": false
      }
    }
  }
}
```

**Note d'implémentation** : le refresh OAuth nécessite un `client_id` Google interne.
Le plus simple est d'appeler `antigravity-usage --json` en sous-processus depuis le code Hermes.

### Résumé des symptômes

| Modèle | Reset | Symptôme | Source de donnée |
|--------|-------|----------|------------------|
| Fable 5 | ~4h10 CET | claude-tmux exit ≠ 0, "resets 4:10am" | Claude usage API |
| Claude (Sonnet/Opus) | 5h glissant | HTTP 429 ou `session_pct >= 100` | idem |
| Codex | ~1h glissant | HTTP 429 | OpenAI wham API |
| Gemini (public) | Minuit PT | HTTP 429 avec `QuotaViolation` | Réactif seulement |
| Gemini (Cloud Code) | ~7j par modèle | `isExhausted: true` | `fetchAvailableModels` (OAuth) |

Reprise : cron à T+1h après reset.

---

## Rôles

| Acteur | Responsabilité |
|--------|---------------|
| Hermes | Spec, lance les boucles, vérifie artefacts, compile, commit, gère quotas |
| DEV (Fable 5) | Produit tests et code |
| REVIEW (Codex) | Critique, valide, approuve/rejette |
| Cron Hermes | Reprend le travail après reset quota |
