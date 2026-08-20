# 🗂️ Guía rápida — mnsync

> Una página para tener al lado del monitor. El detalle está en el [README](README.md).

## Los comandos, en orden

| # | Comando | Qué hace | ¿Escribe? |
|---|---|---|---|
| 1 | `mnsync doctor` | Revisa que todo esté bien configurado | No |
| 2 | `mnsync groups --course <curso>` | Muestra tus grupos de Moodle con sus números | No |
| 3 | `mnsync fetch --course <curso>` | Baja las notas a un Excel para revisar | No |
| 4 | `mnsync plan --course <curso>` | Dice qué se subiría | No |
| 5 | `mnsync rehearse --course <curso>` | Ensayo contra el sistema real | No |
| 5b | `mnsync leer-ensayo <archivo.jsonl>` | Ver qué habría escrito el ensayo | No |
| 6 | `mnsync sync --course <curso> --commit` | **Sube las notas de verdad** | **Sí** |
| 7 | `mnsync plan --course <curso>` | Comprobación: todo debe decir «ya estaban» | No |

## Opciones útiles

```bash
--group 38525      # trabajar con un solo grupo
--allow-update     # permitir cambiar notas ya puestas
--out otra/carpeta # dónde dejar los archivos (por defecto: salida/)
```

## La columna «accion» del plan

| Dice | Hacés |
|---|---|
| `upload` | Nada, es lo normal |
| `skip_already_set` | Nada — ver muchas es buena señal |
| `mark_not_presented` | Confirmar que no entregó |
| `would_overwrite` | 🛑 Revisar |
| `skip_not_in_roster` | 🛑 Revisar cédula en Moodle **o** grupo en `courses.yml` |
| `skip_retirado` | Nada |
| `review` | 🛑 Mirar con calma |

## Códigos de salida

`0` todo bien · `1` error · `2` un freno detuvo un grupo (**no se escribió nada** ahí)

## Si algo sale mal

```bash
mnsync doctor
```

Revisa la configuración, no escribe nada, y dice qué falta.

## Recordatorios

- Nada se escribe sin `--commit`.
- El `.env` con tus contraseñas **nunca** va a Git.
- Los `.xlsx` y `notas_plan*.csv` llevan cédulas de estudiantes.
