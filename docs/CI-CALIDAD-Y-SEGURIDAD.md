# 🛡️ Calidad y seguridad reutilizables

> **Objetivo:** documentar los dos flujos reutilizables de este repositorio para poder activarlos, sin copiar y pegar nada, en cualquier otro repositorio de [chhdeza](https://github.com/chhdeza).

Este repositorio no solo corre sus propias revisiones de calidad y seguridad: **las aloja** para que otros repositorios las llamen. La diferencia importa: si mañana aparece una vulnerabilidad en una de las herramientas, o simplemente se quiere afinar una regla, se corrige **una sola vez acá** y todos los repositorios que apuntan a este archivo lo reciben en su próxima corrida — nadie tiene que acordarse de ir a copiar el cambio a diez repositorios distintos.

---

## 🗂️ Qué hay y qué hace cada cosa

| Archivo | Qué revisa | Herramientas |
|---|---|---|
| [`.github/workflows/security-checks.yml`](../.github/workflows/security-checks.yml) | Seguridad | [gitleaks](https://github.com/gitleaks/gitleaks) (secretos filtrados), [OSV-Scanner](https://github.com/google/osv-scanner) (CVEs en dependencias), [Semgrep](https://semgrep.dev/) (patrones de código inseguro) |
| [`.github/workflows/quality-checks.yml`](../.github/workflows/quality-checks.yml) | Calidad | [Super-linter](https://github.com/super-linter/super-linter) (decenas de linters, uno por lenguaje/formato; incluye actionlint y zizmor para los propios flujos de GitHub Actions) |
| [`.github/workflows/calidad-y-seguridad.yml`](../.github/workflows/calidad-y-seguridad.yml) | — | El flujo que activa los dos anteriores **en este repositorio**. Sirve de ejemplo de uso. |

Los dos primeros son flujos `workflow_call`: no corren solos, alguien tiene que llamarlos. El tercero es ese "alguien" para este repositorio.

Todas las herramientas son gratuitas y de código abierto, y ninguna necesita un token de pago para el uso que se les da acá. Cada archivo trae en su propio encabezado el detalle de qué hace y por qué se configuró así — esto es el resumen para decidir rápido si conviene adoptarlos.

---

## 🔌 Cómo usarlos en otro repositorio de chhdeza

En el repositorio que se quiera revisar, crear `.github/workflows/calidad-y-seguridad.yml` con:

```yaml
name: Calidad y seguridad

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  seguridad:
    permissions:
      contents: read
    uses: chhdeza/moodleToNotas-sync/.github/workflows/security-checks.yml@main

  calidad:
    permissions:
      contents: read
    uses: chhdeza/moodleToNotas-sync/.github/workflows/quality-checks.yml@main
    with:
      # false la primera vez en un repositorio con historia: revisar TODO
      # el árbol ahogaría el primer PR en hallazgos viejos. Una vez que el
      # repositorio esté limpio, se puede pasar a true (o dejarlo en false
      # y agregar un flujo aparte que corra semanalmente con true).
      validate-all: false
```

Eso es todo. No hace falta instalar nada en el repositorio nuevo ni declarar el lenguaje que usa: ambos flujos son genéricos y se adaptan solos a lo que encuentren.

> [!IMPORTANT]
> Los flujos reutilizables de GitHub Actions solo se pueden llamar entre repositorios cuando el que los aloja es **público**, o cuando es privado pero su configuración en *Settings → Actions → General → Access* permite explícitamente el acceso desde otros repositorios de la cuenta. `chhdeza/moodleToNotas-sync` es público, así que cualquier repositorio puede llamarlo sin configuración adicional.

### Ajustar el rigor sin tocar el archivo compartido

Ambos flujos aceptan `fail-on-findings: false` para adoptarlos en modo "solo informa" mientras se limpia un repositorio existente, sin bloquear PRs desde el primer día:

```yaml
  seguridad:
    permissions:
      contents: read
    uses: chhdeza/moodleToNotas-sync/.github/workflows/security-checks.yml@main
    with:
      fail-on-findings: false
```

Una vez que el repositorio esté al día, quitar esa línea (o pasarla a `true`) activa el bloqueo real.

---

## ⚠️ Salvedades a tener presentes

- **gitleaks y cuentas de organización.** `gitleaks-action` es gratis sin condiciones para repositorios de una **cuenta personal** (como `chhdeza`). Si algún día uno de estos repositorios se muda a una organización de GitHub, hay que sacar una licencia gratuita en [gitleaks.io](https://gitleaks.io) y agregarla como secreto `GITLEAKS_LICENSE`.
- **El resultado "bonito" en la pestaña Security requiere GitHub Advanced Security en repositorios privados.** Por eso `upload-sarif` viene apagado por defecto en `security-checks.yml`: así el flujo pasa o falla igual en cualquier repositorio, público o privado. En un repositorio público se puede prender con `upload-sarif: true` (y agregar el permiso `security-events: write` al job que lo llama) para ver los hallazgos de OSV-Scanner en *Security → Code Scanning*.
- **Ninguno de los dos flujos toca el código del repositorio que los llama.** Solo lo leen. `security-checks.yml` declara `permissions: contents: read` a nivel de flujo y de cada job; nada escribe, hace commit ni abre PRs.
- **Los submódulos no se revisan.** Ambos flujos hacen `checkout` sin submódulos a propósito: cada repositorio responde por su propio código, no por el de terceros que trae incluido como dependencia.

---

## 🔄 Mantenimiento

`.github/dependabot.yml` en este repositorio ya vigila el ecosistema `github-actions` en `/.github/workflows`, así que los hashes fijados de gitleaks, osv-scanner, super-linter y demás se actualizan solos, semanalmente, vía PR. La versión de Semgrep (instalada por `pip`, no por una acción) es la única que queda fuera de ese radar y hay que subirla a mano de vez en cuando en `security-checks.yml`.
