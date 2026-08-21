# 001 — Modelo de dominio

| | |
|---|---|
| **Estado** | Aprobado |
| **Versión** | 1.0 |
| **Fecha** | 2026-08-21 |
| **Fuente** | Confirmado punto por punto con el encargado de cátedra |
| **Depende de** | — |
| **Lo usan** | [002](002-enrutamiento-multi-destino.md), [003](003-aplicacion-escritorio.md) |

---

## 1. Propósito

Fijar por escrito cómo se relacionan los estudiantes, los grupos de Moodle y los
grupos de Notas Parciales, para que ninguna decisión de implementación descanse sobre
una suposición no verificada.

Este documento describe **el mundo tal como es**, no el software. No contiene
requisitos: contiene hechos, y de esos hechos se derivan los requisitos de los otros
documentos.

## 2. Glosario

| Término | Definición |
|---------|------------|
| **Asignatura** | Una materia impartida en un cuatrimestre. |
| **Curso de Moodle** | El espacio en Moodle de una asignatura en un cuatrimestre. Se identifica con un `course_id`. |
| **Grupo de Moodle** | Subdivisión de un curso de Moodle. Existe para repartir la carga entre los profesores que imparten la asignatura. |
| **Tutor** | El profesor que imparte uno o más grupos de Moodle. |
| **Centro Universitario (CU)** | Sede regional a la que está adscrito un estudiante. Código numérico de dos dígitos. |
| **Grupo de Notas Parciales** | Subdivisión oficial de una asignatura, numerada, dentro de un centro universitario. |
| **Destino** | Un par `(cu, grupo)`. Es el lugar donde se escribe una nota. |
| **Roster** | La lista oficial de estudiantes de un destino, según el servidor de Notas Parciales. |
| **Cédula** | Identificador nacional del estudiante. Es la llave que une a una persona entre los dos sistemas. |
| **Instrumento** | Casilla calificable del sistema oficial (`Tar1`, `Proy1`). |

## 3. Los dos sistemas se organizan por ejes distintos

Esta es la raíz de toda la complejidad. Ninguna de las dos agrupaciones está mal;
responden preguntas diferentes, y **nada de una permite predecir la otra**.

| | Moodle | Notas Parciales |
|---|--------|-----------------|
| **Agrupa por** | Quién imparte | A qué centro universitario pertenece el estudiante |
| **Un grupo contiene** | Estudiantes de muchos CU | Estudiantes de un solo CU |
| **Papel en la sincronización** | Ámbito de recolección: *de quién* son estos estudiantes | Destino: *dónde* se escribe cada nota |

## 4. Hechos del dominio

Cada hecho es normativo para el diseño: el software **DEBE** comportarse como si fuera
cierto, y **NO DEBE** contener lógica que presuponga lo contrario.

### Estructura de Moodle

**D-01.** Un grupo de Moodle lo define el tutor que lo imparte. El nombre del tutor
aparece en el título del grupo.

> *Uso:* permite preseleccionar los grupos propios en el asistente. Es una ayuda de
> presentación, no una regla de filtrado — ver [003](003-aplicacion-escritorio.md), A-04.

**D-02.** Un grupo de Moodle contiene estudiantes de muchos centros universitarios
distintos. Un grupo de 40 estudiantes puede repartirse entre diez CU.

**D-11.** Un tutor puede impartir varios grupos de Moodle. Todos sus estudiantes se
juntan en un solo conjunto, y **el grupo de Moodle del que viene un estudiante no
influye en su destino**.

> *Consecuencia:* el grupo de Moodle es un ámbito de recolección. Una vez recolectados
> los estudiantes, su identidad de grupo se descarta.

**D-12.** Una asignatura en un cuatrimestre corresponde a **un** curso de Moodle. Sus
grupos existen para repartir la carga entre los varios profesores que la imparten, y a
un tutor se le asignan uno o más.

> *Consecuencia de seguridad:* un curso de Moodle **contiene estudiantes de otros
> profesores**. Descargar a nivel de curso en lugar de grupo los incluiría, y sus notas
> serían escritas por alguien sin competencia sobre ellas. Ver R-10.

### Estructura de Notas Parciales

**D-03.** Un grupo de Notas Parciales contiene estudiantes de exactamente un centro
universitario.

**D-04.** Un mismo centro universitario **puede** estar repartido en varios grupos
numerados de Notas Parciales para la misma asignatura.

> *Consecuencia:* conocer el CU de un estudiante **no** determina su destino. Toda
> estructura de datos que asocie un CU con un único destino es incorrecta por
> construcción. Ver [002](002-enrutamiento-multi-destino.md).

**D-09.** Los números de grupo de Notas Parciales son bajos. Sondear los grupos 1 a 5
encuentra todos los que existen para una asignatura.

### Los estudiantes

**D-05.** Un estudiante pertenece a **exactamente un** grupo de Notas Parciales por
asignatura. Nunca a dos, ni dentro del mismo CU ni entre CU distintos.

> *Uso:* es una invariante verificable. Si un sondeo encuentra la misma cédula en dos
> rosters, el defecto está en el sondeo, no en la matrícula del estudiante. Ver R-08.

**D-07.** Un estudiante **puede** estar asignado a un tutor en Moodle y no existir en
Notas Parciales. Es un caso de borde, poco frecuente pero real.

> *Consecuencia:* poco frecuente significa que cada aparición merece atención;
> legítimo significa que **NO DEBE** detener la subida del resto. Ver R-06 y R-07.

**D-10.** El grupo oficial de un estudiante no cambia a mitad de cuatrimestre.

> *Consecuencia:* la lista de destinos se puede resolver una vez y reutilizar durante
> todo el período. Es lo que hace posible un solo botón de sincronización.

### Datos e ingreso

**D-08.** La columna «Institución» que exporta Moodle siempre trae el código de centro
universitario entre paréntesis, con la forma `DESAMPARADOS (42)`.

**D-06.** Moodle no usa inicio de sesión único. El acceso es con usuario y contraseña
contra el formulario de ingreso.

> *Verificado el 2026-08-21:* el ingreso NTLM contra Notas Parciales también funciona
> con las credenciales institucionales actuales.

## 5. Cómo se decide el destino de un estudiante

Se deriva de D-03, D-04, D-05 y D-08:

1. Se lee el CU del estudiante de la columna «Institución» (D-08).
2. El CU **acota** los destinos que vale la pena consultar: los grupos 1 a 5 de ese CU
   (D-03, D-09).
3. Se consulta el roster de cada destino candidato. **La pertenencia al roster
   decide** (D-04).
4. El estudiante queda en el único destino que contiene su cédula (D-05), o en ninguno
   (D-07).

> **El CU acota la búsqueda; el roster decide el destino.** Es la frase que resume
> todo este documento.

## 6. Fuera de alcance

- Cómo se emparejan las columnas de Moodle con los instrumentos de Notas Parciales.
- Los códigos de contexto de la pantalla de Captura de Notas.
- Escalas de nota y su conversión.

## 7. Historial

| Versión | Fecha | Cambio |
|---------|-------|--------|
| 1.0 | 2026-08-21 | Versión inicial. Doce hechos confirmados uno por uno. D-07, D-09, D-11 y D-12 corrigen suposiciones erróneas previas. |
