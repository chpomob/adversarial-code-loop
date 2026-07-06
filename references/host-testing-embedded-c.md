# Tests host pour firmware embarqué C

## Problème

L'adversarial loop produit du code C pour ESP32/embarqué. Les tests
doivent pouvoir être compilés et exécutés sur le PC (host) pour un
feedback rapide.

## Frameworks disponibles

| Framework | Installation | Adapté pour |
|-----------|-------------|-------------|
| **Unity** (ThrowTheSwitch) | Aucune — 2 fichiers .c/.h à copier | Tests host rapides, pas de dépendance système |
| Criterion | `apt install libcriterion-dev` | Tests plus riches (fixtures, paramétrage) |
| CMocka | `apt install libcmocka-dev` | Mocking |

## Recommandation

Utiliser **Unity** pour les tests host dans les projets embarqués :
- Pas de dépendance système (`sudo apt`). Les fichiers sont dans le repo.
- Même framework que les tests embarqués ESP-IDF → unifier la stack.
- API assez riche pour les tests structurels (assertions, setUp, tearDown).

## Setup Unity

```bash
mkdir -p lib/unity
curl -sL "https://raw.githubusercontent.com/ThrowTheSwitch/Unity/master/src/unity.h" \
  -o lib/unity/unity.h
curl -sL "https://raw.githubusercontent.com/ThrowTheSwitch/Unity/master/src/unity_internals.h" \
  -o lib/unity/unity_internals.h
curl -sL "https://raw.githubusercontent.com/ThrowTheSwitch/Unity/master/src/unity.c" \
  -o lib/unity/unity.c
```

Un template Makefile est disponible dans le skill :
`templates/Makefile.host-tests-unity`. Copier à la racine du projet.

Compilation manuelle :
```bash
gcc -std=c11 -Wall -Werror -Ilib -Ilib/unity -Ilib/spsc \
  -o build/test_host lib/unity/unity.c test/host/test_xxx.c -lm
```

## API Unity essentielle

| Macro | Usage |
|-------|-------|
| `TEST_ASSERT_TRUE(cond)` | Assertion booléenne |
| `TEST_ASSERT_FALSE(cond)` | Négation booléenne |
| `TEST_ASSERT_EQUAL(a, b)` | Égalité (int) |
| `TEST_ASSERT_EQUAL_UINT64(a, b)` | Égalité 64-bit |
| `TEST_ASSERT_EQUAL_FLOAT(a, b)` | Égalité flottante |
| `TEST_ASSERT_NULL(ptr)` | Pointeur nul |
| `TEST_ASSERT_NOT_NULL(ptr)` | Pointeur non nul |
| `TEST_ASSERT_EQUAL_MEMORY(a, b, len)` | Comparaison mémoire |

## Structure d'un fichier test

```c
#include "unity.h"

static spsc_t q;
static spsc_msg_t out;

void setUp(void)   { spsc_init(&q); }
void tearDown(void) { /* cleanup */ }

static void test_push_pop_one(void) {
    spsc_msg_t m = make_msg(42ul);
    TEST_ASSERT_TRUE(spsc_push(&q, &m));
    TEST_ASSERT_TRUE(spsc_pop(&q, &out));
    /* assertions sur les champs */
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_push_pop_one);
    return UNITY_END();
}
```
