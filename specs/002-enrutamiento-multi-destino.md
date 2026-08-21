# 002 — Enrutamiento multi-destino

| | |
|---|---|
| **Estado** | Implementado |
| **Versión** | 1.1 |
| **Fecha** | 2026-08-21 |
| **Depende de** | [001 — Modelo de dominio](001-modelo-de-dominio.md) |
| **Lo usa** | [003 — Aplicación de escritorio](003-aplicacion-escritorio.md) |
| **Prioridad** | Alta. Hay un defecto activo que pierde notas en silencio. |

---

## 1. El defecto

Con la estructura real de los cursos (D-02, D-04), `mnsync` **deja sin subir a la
mayoría de los estudiantes de un grupo, sin dar error**.

El motor asocia cada centro universitario con un único destino:

```python
# vendor/grade-uploader/notasparciales_upload.py:1568
cu_grupo_map: dict[str, int]      # un destino por CU

# :1608
grupo = cu_grupo_map[cu]          # dos estudiantes del mismo CU → un solo destino posible
```

Esa estructura no puede representar D-04. Y la salida aparente tampoco funciona:

```python
# :1498
out[cu.strip()] = int(g.strip())  # --cu-grupo 42=1 --cu-grupo 42=2
                                  # la segunda asignación pisa la primera, sin avisar
```

`src/mnsync/config.py` agrava el problema: `Group` (líneas 123-145) lleva un solo
`cu`/`grupo`, `uploader.py:147` emite un solo `--cu-grupo`, y `_validate_groups`
(líneas 291-297) rechaza repetir un `moodle_group_id`, cerrando el último rodeo
posible.

### Comportamiento observado

Con `cu: "42"` configurado y estudiantes de diez CU en el grupo:

| Estudiantes | Resultado |
|---|---|
| Los de CU 42 en el grupo 1 | Suben correctamente |
| Los de CU 42 en el grupo 2 (D-04) | `skip_not_in_roster` — nunca se escriben |
| Los de los otros nueve CU | `skip_not_in_roster`, motivo `CU 'NN' no tiene mapeo` (:1596-1606) |

Ninguna corrida posterior los recupera, porque el defecto es determinista.

## 2. Requisitos

### Enrutamiento

**R-01.** El destino de una nota **DEBE** resolverse por estudiante, no por centro
universitario. *(D-04, D-05)*

**R-02.** El sistema **DEBE** admitir varios destinos para un mismo centro
universitario dentro de una misma corrida. *(D-04)*

**R-03.** El sistema **DEBE** juntar los estudiantes de todos los grupos de Moodle
seleccionados antes de resolver destinos, y **NO DEBE** hacer que el destino de un
estudiante dependa del grupo de Moodle del que provino. *(D-11)*

**R-04.** La unidad de escritura **DEBE** ser el destino oficial `(cu, grupo)`, no el
grupo de Moodle. *(D-11)*

**R-05.** El descubrimiento de destinos **DEBE** sondear los grupos 1 a 5 de cada
centro universitario presente, **DEBE** hacerlo solo con operaciones de lectura, y
**DEBERÍA** dejar de sondear un CU cuando todos sus estudiantes ya tienen destino.
*(D-09)*

### Estudiantes sin destino

**R-06.** Un estudiante que no aparezca en ningún roster **NO DEBE** detener la subida
de los demás. *(D-07)*

**R-07.** Un estudiante sin destino **DEBE** aparecer nombrado de forma destacada en el
reporte de la corrida. Por ser un caso poco frecuente, **NO DEBE** quedar sepultado
entre filas de detalle. *(D-07)*

**R-08.** Si el sondeo encuentra la misma cédula en dos rosters, el sistema **DEBE**
tratarlo como un fallo del sondeo y **NO DEBE** presentarlo como un problema de
matrícula del estudiante. El mensaje va dirigido a quien desarrolla. *(D-05)*

### Distinguir un vacío normal de una configuración rota

**R-09.** El sistema **DEBE** clasificar los estudiantes sin destino por **patrón**, no
por proporción:

| Patrón | Significado | Comportamiento exigido |
|---|---|---|
| Algunos estudiantes de un CU sin destino, el resto con destino | Vacío de matrícula (D-07) | Se nombran; **DEBE** subir el resto |
| **Todos** los estudiantes de un CU sin destino | Ese CU no resolvió ningún destino | **DEBE** detener ese CU |
| **Todos** los estudiantes de **todos** los CU sin destino | Códigos de contexto equivocados | **DEBE** detener la corrida antes de escribir nada |

> **Fundamento.** Juzgado solo por proporción, un grupo pequeño con dos vacíos
> legítimos es indistinguible de una corrida apuntada al `modelo` equivocado. La forma
> los separa: los vacíos caen sobre individuos dispersos; una configuración rota
> arrasa centros universitarios enteros. Además, un freno que salta en corridas
> normales termina desactivado, y entonces no protege nada.

### Preservar lo que ya protege

**R-10.** La verificación de alcance de grupo en Moodle (`moodle_export.py:232-265`)
**NO DEBE** relajarse. Moodle responde a un grupo que no reconoce sirviendo el curso
entero sin dar error, y por D-12 eso significa estudiantes de otros profesores.

**R-11.** La verificación de rosters idénticos entre grupos de Moodle
(`sync.py:65-89`) **DEBE** conservarse sin cambios. Compara grupos *distintos* de
Moodle, y que coincidan sigue siendo un error real.

**R-12.** El freno de cantidad **DEBE** contarse por destino oficial, no por grupo de
Moodle. *(R-04)*

> El valor actual (`max_changes: 40`, `config.py:154`) fue dimensionado para un grupo.
> Con 40 estudiantes por varios instrumentos entre diez CU, una corrida legítima lo
> supera siempre.

**R-13.** El script del submódulo **NO DEBE** modificarse. Sus cuatro capas de
seguridad —prueba por defecto, `--allow-update` obligatorio, justificación registrada y
verificación posterior— dependen de entrar por su CLI.

**R-14.** ~~Se autoriza un único cambio al submódulo, en una función de solo lectura:
`_discover_grupo_for_cu` (`:1502`) **DEBE** devolver todos los grupos con coincidencia
—no solo el de mayor solapamiento— y su `max_grupo` **DEBE** bajar de 15 a 5.~~
**Retirado en v1.1: no hizo falta.** El submódulo quedó **sin modificar**, y R-13 se
cumple por completo.

> **Por qué se retiró.** El sondeo terminó haciéndose desde `mnsync`, usando el propio
> `plan` del script como sonda: las filas que no son `skip_not_in_roster` son
> exactamente las que el roster oficial reconoce. Así el emparejamiento lo sigue
> haciendo el código ya probado, y la autodetección interna del script deja de
> intervenir —ver §3, «Un archivo por centro universitario»—. Cambiar una función que
> ya nadie llama habría sido tocar el submódulo sin ganar nada.

### Datos personales

**R-15.** La configuración persistida **DEBE** contener únicamente la lista de destinos
`(cu, grupo)`. **NO DEBE** contener cédulas ni ningún dato personal.

> **Fundamento.** La configuración que produce el asistente la consume también el flujo
> automático de GitHub Actions, y por tanto vive en un repositorio. Lo que entra al
> historial de git no sale nunca. La ubicación individual de cada estudiante **DEBE**
> recalcularse en cada corrida contra el servidor, y **NO DEBE** persistirse.

## 3. Diseño

### Abanico de destinos

Se invoca el script del submódulo **una vez por destino**, y `mnsync` fusiona los
planes resultantes. Cada invocación tiene exactamente un destino, con lo que la
suposición «un CU, un grupo» del script se cumple trivialmente.

| | Modificar el submódulo | **Abanico en `mnsync`** ✔ |
|---|---|---|
| Cambios al motor probado | Reescribe `_build_plan` | Ninguno |
| Quién empareja contra el roster | Código nuevo | El código ya probado |
| Costo | Una llamada | N llamadas de lectura |
| Datos personales en configuración | Requiere mapa por estudiante | Solo la lista de destinos (R-15) |

Se elige el abanico: el emparejamiento contra el roster es la parte que no puede estar
mal, y este camino no la toca. Además satisface R-15 por construcción.

### Un archivo por centro universitario

Al planificar un destino, el script no se limita al `--cu-grupo` que se le pasa:
**autodetecta un destino para cada centro universitario que encuentre en el xlsx**, y
lo hace sondeando el servidor grupo por grupo. Con diez CU y seis destinos eso son
cientos de consultas por corrida, y todas terminan descartadas, porque cada CU ya tiene
su propio plan hecho con su destino explícito.

La solución es no darle de qué autodetectar: `mnsync` junta a los estudiantes de todos
los grupos de Moodle y los reparte en **un archivo por centro universitario**. Cada
invocación recibe solo el archivo de su CU, con lo que el `--cu-grupo` explícito cubre
a todos los estudiantes del archivo y no queda nada por adivinar.

El agrupamiento previo (R-03) y el reparto por CU ocurren en el mismo paso, que es
donde el grupo de Moodle deja de importar.

### Recorte del plan a su destino

Como defensa en profundidad, cada plan se recorta a las filas cuyo `(cu, grupo)`
coincide con el destino que se pidió. Si alguna fila apareciera resuelta por
autodetección, sus datos serían justamente los que pierden a los estudiantes del
segundo grupo de un CU (D-04): no se descartan por prolijidad, sino porque no se puede
confiar en ellas.

Recortado cada plan a lo suyo, la unión de todos cubre a cada estudiante exactamente
una vez, y cada fila proviene de una consulta hecha con el destino correcto.

### Cambios por archivo

| Archivo | Cambio | Requisitos |
|---------|--------|------------|
| `src/mnsync/config.py` | `Group` (123-145) deja de llevar un `cu`/`grupo`; el destino pasa a ser una lista resuelta contra el servidor. `_validate_groups` (265-297) mantiene prohibido repetir `moodle_group_id` y que dos orígenes apunten al mismo destino. `max_changes` (154) se cuenta por destino. | R-02, R-12, R-15 |
| `src/mnsync/sync.py` | La unidad de trabajo pasa a destino oficial. Se agrega el agrupamiento previo. `_assert_groups_are_distinct` (65-89) queda intacta. | R-03, R-04, R-11 |
| `src/mnsync/uploader.py` | `plan()`/`apply()` (133-188): una invocación por destino, planes fusionados. | R-01, R-02 |
| `src/mnsync/guard.py` | Se retira `UMBRAL_FUERA_DE_ROSTER` (27) y `_check_roster_match` (102-125) pasa al discriminador por patrón. El script ya emite motivos distintos en `:1603` y `:1630`. | R-06, R-09 |
| `src/mnsync/moodle_export.py` | Solo se añade `GradeExport.instituciones`, de lectura. La verificación de alcance queda intacta. | R-10 |
| `vendor/grade-uploader/notasparciales_upload.py` | **Sin cambios.** | R-13 |

## 4. Criterios de aceptación

Contra el servidor falso de `tests/`, con un escenario de dos grupos de Moodle, diez
centros universitarios, un CU repartido en dos destinos y un estudiante sin destino:

- [x] **CA-01** Todo estudiante con destino recibe su nota, incluidos los dos del mismo CU que van a destinos distintos. *(R-01, R-02)* — `test_ca01_cada_estudiante_llega_a_su_destino`, `test_ca01_un_cu_repartido_en_dos_destinos`
- [x] **CA-02** Cambiar de qué grupo de Moodle proviene un estudiante no altera su destino. *(R-03)* — `test_ca02_el_grupo_de_moodle_no_cambia_el_destino`
- [x] **CA-03** El estudiante sin destino aparece nombrado en el reporte y no impide que suban los demás. *(R-06, R-07)* — `test_ca03_estudiante_sin_destino_no_detiene_al_resto`, `test_reporte_nombra_a_quien_se_quedo_sin_destino`
- [x] **CA-04** Con un CU cuyos estudiantes no emparejan ninguno, se detiene ese CU y solo ese. *(R-09)* — `test_ca04_un_cu_sin_destino_no_arrastra_a_los_otros`, `test_un_cu_entero_sin_destino_bloquea_ese_cu`
- [x] **CA-05** Con códigos de contexto equivocados, no se escribe absolutamente nada. *(R-09)* — `test_ca05_contexto_equivocado_no_escribe_nada`, `test_todos_los_cu_sin_destino_apuntan_a_los_codigos`
- [x] **CA-06** Un roster manipulado para devolver una cédula repetida produce un fallo de sondeo, no un mensaje sobre el estudiante. *(R-08)* — `test_una_cedula_en_dos_rosters_es_un_fallo_del_sondeo`
- [x] **CA-07** La configuración escrita no contiene ninguna cédula. *(R-15)* — `test_ca07_la_configuracion_no_contiene_cedulas`
- [x] **CA-08** Las pruebas existentes de alcance de grupo siguen pasando sin modificarse. *(R-10, R-11)* — `test_moodle_export.py` y `test_aborta_si_los_dos_grupos_traen_los_mismos_estudiantes`, sin tocar

## 5. Historial

| Versión | Fecha | Cambio |
|---------|-------|--------|
| 1.1 | 2026-08-21 | Implementación. **R-14 retirado**: el submódulo quedó sin modificar. Se documentan dos mecanismos que la versión 1.0 no anticipaba: un archivo por centro universitario, y el recorte de cada plan a su destino. Los ocho criterios de aceptación quedan verificados. |
| 1.0 | 2026-08-21 | Versión inicial. |
