# 003 — Aplicación de escritorio

| | |
|---|---|
| **Estado** | Borrador |
| **Versión** | 0.1 |
| **Fecha** | 2026-08-21 |
| **Depende de** | [001 — Modelo de dominio](001-modelo-de-dominio.md), [002 — Enrutamiento multi-destino](002-enrutamiento-multi-destino.md) |
| **Bloqueado por** | 002 tiene que estar implementado antes de empezar. |

---

## 1. Propósito

Sustituir la línea de comandos por una aplicación de Windows que un profesor sin
formación técnica pueda usar sin leer documentación ni editar archivos YAML.

**El objetivo concreto: que `courses.yml` deje de ser algo que un humano escribe.**
Todo su contenido pasa a ser algo que la aplicación descubre y el profesor confirma.

## 2. Público y alcance

**Veinte profesores de la UNED**, en despliegue controlado, sobre máquinas personales.
No es una distribución pública.

Las cuentas institucionales de la UNED (Moodle y Notas Parciales) son distintas de la
cuenta con la que el profesor inicia sesión en su propia máquina.

### Fuera de alcance

- Distribución pública o publicación en tiendas de aplicaciones.
- Versiones para macOS o Linux.
- Calificar: las notas las pone el profesor en Moodle.

## 3. Arquitectura

**A-01.** La interfaz **DEBE** ser una capa de presentación sobre el motor existente.
**NO DEBE** reimplementar la planificación, el emparejamiento de instrumentos ni las
rejas de seguridad.

**A-02.** La interfaz **DEBE** importar `mnsync.sync` directamente, **NO DEBE**
invocar el CLI de `mnsync` como subproceso.

> *Fundamento.* Hoy la cadena es CLI → subproceso → script del submódulo. Añadir la
> interfaz encima daría tres fronteras para arrastrar un mensaje de error legible, y el
> profesor recibiría lo peor de las tres. Se conserva la frontera del submódulo, que sí
> se gana su lugar (002, R-13).

**A-03.** El empaquetado **DEBERÍA** ser PyInstaller sobre Python y PySide6.

> El motor es Python y el submódulo ya trae un empaquetado PyInstaller funcionando
> (`vendor/grade-uploader/construir_exe.bat`). Se extiende un camino probado en lugar
> de adoptar un segundo entorno de ejecución.

## 4. Asistente de configuración

Se ejecuta una vez por curso. Produce la configuración que después consume tanto la
aplicación como el flujo automático.

**A-04.** Al elegir los grupos de Moodle, la aplicación **DEBERÍA** preseleccionar
aquellos cuyo título contenga el nombre del profesor (D-01), y **NO DEBE** ocultar los
demás.

> *Fundamento.* Los nombres varían en tildes, abreviaturas, prefijos y apellidos. Un
> filtro acertado el 95% de las veces esconde justo el grupo que hacía falta, sin
> forma de recuperarlo.

**A-05.** El asistente **DEBE** incluir un paso que recorra los menús de la pantalla de
Captura de Notas para obtener los nueve códigos de contexto (`ano`, `pac`, `tipo`,
`asignatura`, `escuela`, `catedra`, `encargado`, `tutor`, `modelo`).

> Estos códigos **no** se pueden deducir de Moodle. Es la pantalla más delicada del
> asistente.

**A-06.** El asistente **DEBE** mostrar el enrutamiento resuelto para que el profesor
lo confirme, agrupado por destino y con el conteo de estudiantes de cada uno.
**DEBE** mostrar por separado los estudiantes sin destino (002, R-07).

> Ejemplo del contenido exigido:
>
> | Destino | Estudiantes | |
> |---|---|---|
> | CU 42 (Desamparados) · Grupo 1 | 12 | ✓ |
> | CU 42 (Desamparados) · Grupo 2 | 3 | ✓ |
> | CU 04 (Alajuela) · Grupo 1 | 18 | ✓ |
> | **Sin grupo oficial** | **2** | ⚠ revisar |

**A-07.** El asistente **DEBE** mostrar el emparejamiento propuesto de columnas de
Moodle con instrumentos, y **DEBE** listar explícitamente las columnas que no logró
emparejar, para que el profesor las indique a mano.

**A-08.** El asistente **DEBE** verificar ambos ingresos —Moodle y Notas Parciales—
antes de darse por terminado.

## 5. Sincronización

**A-09.** El uso semanal **DEBE** reducirse a un botón, sin opciones ni banderas.

**A-10.** Antes de escribir, la aplicación **DEBE** mostrar una vista de diferencias
con las nueve acciones posibles del plan, no una reducción a dos.

**A-11.** Las sobrescrituras de notas ya puestas **DEBEN** autorizarse fila por fila.
**NO DEBE** existir una casilla global que autorice todas a la vez.

> *Fundamento.* «Este estudiante tiene 8.5 y Moodle dice 9.0» es una decisión
> individual. Cada autorización queda justificada con su código en el sistema de la
> UNED, como manda el procedimiento.

**A-12.** La aplicación **DEBE** ofrecer un botón «Probar sin escribir nada» que
ejecute la reja de escritura (`_uploader_shim.py:69-113`) contra el servidor real,
incluida su autoverificación previa (`cli.py:353-381`).

**A-13.** La confirmación previa a escribir **DEBE** advertir que una nota escrita no
se deshace desde este programa.

**A-14.** La aplicación **DEBE** verificar las credenciales guardadas en cada arranque,
y **DEBE** informar de una credencial vencida como tal —«parece que cambió su
contraseña»— y no como un fallo a mitad de una sincronización.

## 6. Credenciales

**A-15.** Las credenciales **DEBEN** guardarse en el Administrador de credenciales de
Windows, en dos entradas separadas. **NO DEBEN** guardarse en un archivo de texto
plano.

> *Lo que protege:* otro usuario de la misma máquina; que alguien copie el archivo; que
> OneDrive sincronice un `.env` a la nube.
>
> *Lo que no protege:* software malicioso corriendo como ese mismo usuario. Es
> inherente —la aplicación tiene que poder leerlas— y conviene decirlo en lugar de
> sugerir una seguridad que no existe.

**A-16.** El sistema **DEBE** admitir tres orígenes de credenciales que pueblen el
mismo objeto `Credentials`:

| Entorno | Origen |
|---|---|
| Aplicación de escritorio | Administrador de credenciales de Windows |
| GitHub Actions | Secretos del repositorio *(ya implementado)* |
| Desarrollo | `.env` *(ya implementado)* |

> `load_credentials` (`config.py:59-101`) ya lee variables de entorno con `.env` como
> capa opcional. El Administrador de credenciales se suma como tercer origen sin
> cambiar nada aguas abajo.

## 7. Distribución

**A-17.** El ejecutable **DEBE** ir firmado con un certificado de firma de código.

> Un `.exe` sin firmar dispara SmartScreen. Veinte personas no técnicas o no lo
> instalan, o aprenden a saltarse advertencias de seguridad — y eso último es peor que
> no distribuir nada.

**A-18.** La aplicación **DEBE** comprobar su versión al arrancar y **DEBE** poder
negarse a ejecutarse.

> Si una corrida escribe una nota equivocada, hay que poder detener las veinte
> instalaciones hoy, no cuando alguien se dé cuenta.

**A-19.** El despliegue **DEBE** ser escalonado: primero el autor, luego dos o tres
voluntarios sobre cursos reales, después el resto.

**A-20.** La aplicación **DEBERÍA** ofrecer la exportación de un paquete de
diagnóstico con el reporte y los registros. Ese paquete **NO DEBE** contener
credenciales y **DEBERÍA** permitir ocultar los nombres de los estudiantes.

> Va a llegar por correo electrónico.

## 8. Datos personales

**A-21.** Los archivos descargados con cédulas, nombres y notas **DEBEN** purgarse
automáticamente pasado un plazo corto, y la aplicación **DEBE** ofrecer un control
visible para borrarlos de inmediato.

> El flujo de CI ya limita los artefactos a 7 días (`sync.yml:163`). La misma
> disciplina aplica en local, sobre veinte máquinas personales. Ley 8968 de Costa Rica.

## 9. Automatización (segunda etapa, un solo usuario)

**A-22.** El flujo `.github/workflows/sync.yml` **DEBE** conservarse, y **DEBE**
consumir la misma configuración que produce el asistente.

> Su alcance es el autor únicamente, que prefiere no ejecutar la sincronización a mano.
> Los otros diecinueve profesores nunca lo tocan. De aquí sale el requisito R-15 de
> [002](002-enrutamiento-multi-destino.md): esa configuración vive en un repositorio,
> así que no puede contener cédulas.

## 10. Preguntas abiertas

| # | Pregunta |
|---|----------|
| Q-01 | ¿Certificado OV o EV? Afecta costo y la reputación inicial ante SmartScreen. |
| Q-02 | ¿Cómo se distribuyen las actualizaciones a las veinte máquinas? |
| Q-03 | ¿Qué plazo concreto para la purga automática de A-21? |

## 11. Historial

| Versión | Fecha | Cambio |
|---------|-------|--------|
| 0.1 | 2026-08-21 | Borrador inicial. |
