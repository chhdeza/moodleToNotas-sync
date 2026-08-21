# Especificaciones

Documentos normativos de `mnsync`. Describen **qué** tiene que hacer el sistema y
**por qué**, no cómo está implementado hoy: el código es la respuesta a estos
documentos, no al revés.

## Índice

| # | Documento | Estado | Resumen |
|---|-----------|--------|---------|
| [001](001-modelo-de-dominio.md) | Modelo de dominio | Aprobado | Cómo se relacionan estudiantes, grupos de Moodle y grupos de Notas Parciales. Base de todo lo demás. |
| [002](002-enrutamiento-multi-destino.md) | Enrutamiento multi-destino | Aprobado | Corrección del defecto que deja estudiantes sin subir en silencio. |
| [003](003-aplicacion-escritorio.md) | Aplicación de escritorio | Borrador | Asistente de configuración y sincronización con un botón, para profesores no técnicos. |

## Convenciones

**Identificadores.** Cada afirmación normativa lleva un identificador estable
(`D-01`, `R-04`, `A-12`). Los identificadores **no se reutilizan**: si una regla se
retira, su número queda marcado como retirado y no vuelve a asignarse. El código y las
pruebas citan estos identificadores.

**Lenguaje normativo**, según el sentido de la RFC 2119:

| Término | Significado |
|---------|-------------|
| **DEBE** / **NO DEBE** | Requisito absoluto. Incumplirlo es un defecto. |
| **DEBERÍA** | Recomendación fuerte. Apartarse exige una razón escrita. |
| **PUEDE** | Opcional, a criterio de quien implementa. |

**Estados.** `Borrador` → `Aprobado` → `Implementado` → `Retirado`.

**Cambios.** Cada documento lleva su propio historial al final. Un cambio que
contradiga una afirmación ya aprobada exige una entrada nueva en ese historial,
explicando qué cambió y por qué.

## Sobre el idioma

Estos documentos están en español, igual que el resto del repositorio, porque su
público son profesores de la UNED de Costa Rica: quienes usan el programa, quienes lo
prueban y quienes eventualmente lo mantengan.
