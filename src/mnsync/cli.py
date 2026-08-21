"""
La línea de comandos de mnsync.

Dos reglas de estilo, heredadas de ``grade-uploader`` porque los colegas ya
las conocen de ahí:

  - **Prueba por defecto.** Nada escribe sin un ``--commit`` explícito.
  - **Cada comando termina diciendo cuál sigue.** Nadie debería tener que
    recordar la secuencia ni volver al manual entre un paso y otro.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, credstore
from ._uploader_shim import vendor_script
from .config import Config, Course, Credentials, load_config, load_credentials, missing_credentials
from .discovery import verificar_contexto
from .errors import MnsyncError
from .moodle_export import MoodleSession
from .report import write_report
from .sync import fetch_groups, sync_course

ANCHO = 70


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------
def titulo(texto: str) -> None:
    print("=" * ANCHO)
    print(f" {texto}")
    print("=" * ANCHO)


def siguiente_paso(lineas: list[str]) -> None:
    """El bloque «▶ SIGUIENTE PASO» con el que termina cada comando."""
    print()
    print("─" * ANCHO)
    print(" ▶ SIGUIENTE PASO")
    print()
    for ln in lineas:
        print(f"   {ln}")
    print("─" * ANCHO)


def _work_dir(args: argparse.Namespace) -> Path:
    return Path(args.out or "salida").resolve()


def _cargar(args: argparse.Namespace) -> tuple[Config, Credentials]:
    cfg = load_config(Path(args.config) if args.config else None)
    creds = load_credentials()
    return cfg, creds


def _grupos_pedidos(course: Course, args: argparse.Namespace):
    if not getattr(args, "group", None):
        return None
    return [course.group_by_moodle_id(int(args.group))]


def _origen_de_credenciales() -> str:
    """
    De dónde salieron las credenciales que se están usando.

    Con tres orígenes posibles conviene decirlo: si alguien cambió la
    contraseña en un lado y el programa la está tomando del otro, este renglón
    es lo único que lo explica.
    """
    import os

    if os.environ.get("MOODLE_PASSWORD"):
        if Path(".env").exists():
            return "del archivo .env o del entorno"
        return "del entorno"
    if credstore.leer(credstore.SERVICIO_MOODLE) is not None:
        return "del Administrador de credenciales de Windows"
    return "origen desconocido"


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    titulo("REVISIÓN DE LA CONFIGURACIÓN")
    problemas: list[str] = []

    # 1. El submódulo
    script = vendor_script()
    if script.exists():
        print(" ✓ Script de Notas Parciales encontrado")
    else:
        print(" ✗ Falta el script de Notas Parciales")
        problemas.append("Ejecutá:  git submodule update --init --recursive")

    # 2. Credenciales
    creds = load_credentials(require=False)
    faltan = missing_credentials(creds)
    if faltan:
        print(f" ✗ Faltan credenciales: {', '.join(faltan)}")
        if credstore.disponible():
            problemas.append(
                "Ejecutá «mnsync credenciales» para guardarlas en el sistema, "
                "o copiá «.env.example» como «.env» y completá esos valores."
            )
        else:
            problemas.append("Copiá «.env.example» como «.env» y completá esos valores.")
    else:
        print(f" ✓ Credenciales configuradas ({_origen_de_credenciales()})")
        print(f"     · Moodle: {creds.moodle_username} en {creds.moodle_url}")
        print(f"     · Notas Parciales: {creds.np_user}")
    if not creds.np_is_real_server:
        print(f" ! NP_BASE_URL apunta a «{creds.np_base_url}» (no al sistema real)")

    # 3. courses.yml
    cfg = None
    try:
        cfg = load_config(Path(args.config) if args.config else None)
        print(f" ✓ Configuración válida: {len(cfg.courses)} curso(s)")
        for c in cfg.courses:
            print(f"     · {c.id}: curso Moodle {c.moodle_course_id}, {len(c.groups)} grupo(s)")
            for g in c.groups:
                print(f"         - grupo de Moodle {g.moodle_group_id}: {g.label}")
            if c.destinations:
                destinos = ", ".join(d.label for d in c.destinations)
                print(f"         destinos en Notas Parciales: {destinos}")
            else:
                print("         destinos: se descubren solos la primera vez")
    except MnsyncError as e:
        print(f" ✗ {e.mensaje}")
        if e.remedio:
            problemas.append(e.remedio)

    # 4. Conexión (solo si hay con qué)
    if not faltan and not args.offline:
        print()
        print(" Probando conexión con Moodle...")
        try:
            s = MoodleSession(creds.moodle_url)
            s.login(creds.moodle_username, creds.moodle_password)
            print(" ✓ Moodle responde y aceptó las credenciales")
        except MnsyncError as e:
            print(f" ✗ Moodle: {e.mensaje}")
            if e.remedio:
                problemas.append(e.remedio)

    print("=" * ANCHO)

    if problemas:
        print()
        print(" Hay cosas por arreglar:")
        for p in problemas:
            print(f"   • {p}")
        return 1

    primer_curso = cfg.courses[0].id if cfg and cfg.courses else "<tu-curso>"
    siguiente_paso(
        [
            "Todo listo. Mirá qué haría el programa, sin escribir nada:",
            "",
            f"   mnsync plan --course {primer_curso}",
        ]
    )
    return 0


# ---------------------------------------------------------------------------
# credenciales
# ---------------------------------------------------------------------------
def cmd_credenciales(args: argparse.Namespace) -> int:
    """
    Guarda las contraseñas en el Administrador de credenciales de Windows.

    Es la alternativa al archivo `.env`, y la que usa la aplicación de
    escritorio: quedan cifradas contra la cuenta de Windows del profesor, en
    vez de en un archivo de texto que cualquier carpeta sincronizada puede
    llevarse a la nube.
    """
    import getpass

    titulo("CREDENCIALES GUARDADAS EN EL SISTEMA")

    if not credstore.disponible():
        print(" ✗ Esta máquina no ofrece un almacén de credenciales.")
        print()
        print(" Usá el archivo «.env» en su lugar. Mirá «.env.example».")
        return 1

    if args.borrar:
        for servicio in (credstore.SERVICIO_MOODLE, credstore.SERVICIO_NP):
            credstore.borrar(servicio)
        print(" ✓ Credenciales borradas del sistema.")
        print()
        print(" Si tenías un «.env», ese sigue como estaba: este comando no lo toca.")
        return 0

    if args.ver:
        _mostrar_credenciales_guardadas()
        return 0

    print()
    print(" Se piden dos veces: una para Moodle y otra para Notas Parciales.")
    print(" Las contraseñas no se ven mientras se escriben, y no quedan en")
    print(" ningún archivo.")
    print()

    try:
        print(" ── Moodle ──")
        usuario_moodle = input("   Usuario: ").strip()
        clave_moodle = getpass.getpass("   Contraseña: ")
        print()
        print(" ── Notas Parciales ──")
        print("   (el usuario va sin @uned.ac.cr)")
        usuario_np = input("   Usuario: ").strip()
        clave_np = getpass.getpass("   Contraseña: ")
    except (EOFError, KeyboardInterrupt):
        print()
        print(" Cancelado. No se guardó nada.")
        return 130

    faltan = not all([usuario_moodle, clave_moodle, usuario_np, clave_np])
    if faltan:
        raise MnsyncError(
            "Quedó algún campo vacío, así que no se guardó nada.",
            remedio="Volvé a ejecutar el comando y completá los cuatro valores.",
        )

    credstore.guardar(credstore.SERVICIO_MOODLE, usuario_moodle, clave_moodle)
    credstore.guardar(credstore.SERVICIO_NP, usuario_np, clave_np)

    print()
    print(" ✓ Guardadas en el Administrador de credenciales de Windows.")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Comprobá que el sistema de la UNED las acepta:",
            "",
            "   mnsync doctor",
        ]
    )
    return 0


def _mostrar_credenciales_guardadas() -> None:
    """Los usuarios guardados. **Nunca** las contraseñas."""
    print()
    for etiqueta, servicio in (
        ("Moodle", credstore.SERVICIO_MOODLE),
        ("Notas Parciales", credstore.SERVICIO_NP),
    ):
        cred = credstore.leer(servicio)
        if cred is None:
            print(f"   {etiqueta:<18} (nada guardado)")
        else:
            print(f"   {etiqueta:<18} {cred.username}")
    print()
    print(" Las contraseñas no se muestran nunca, ni siquiera a vos.")
    print("=" * ANCHO)


# ---------------------------------------------------------------------------
# cursos
# ---------------------------------------------------------------------------
def cmd_cursos(args: argparse.Namespace) -> int:
    """
    Los cursos que ves en Moodle, con su número.

    Es para no tener que copiarlo de la barra del navegador. Si la lista sale
    vacía o incompleta —depende del tema visual de Moodle— el número se puede
    escribir a mano igual.
    """
    creds = load_credentials()

    s = MoodleSession(creds.moodle_url)
    s.login(creds.moodle_username, creds.moodle_password)
    cursos = s.list_courses()

    titulo("TUS CURSOS EN MOODLE")
    print()
    if not cursos:
        print(" No se pudo leer la lista de cursos desde tu página de inicio.")
        print()
        print(" No es grave: abrí tu curso en Moodle y mirá la barra de direcciones.")
        print(" El número que sigue a «id=» es el que va en «course_id».")
        print()
        print("   https://aprende.uned.ac.cr/course/view.php?id=8067")
        print("                                                 ▲")
        print("                                                 course_id")
        return 0

    configurados = _cursos_configurados(args)
    for c in cursos:
        marca = "✓ configurado" if c.id in configurados else ""
        print(f"   {c.id:>8}  {c.name:<48} {marca}")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Poné el número del tuyo en courses.yml, como «course_id»,",
            "y después mirá qué grupos tiene:",
            "",
            "   mnsync groups --course <tu-curso>",
        ]
    )
    return 0


def _cursos_configurados(args: argparse.Namespace) -> set[int]:
    """Los course_id que ya están en courses.yml, si el archivo existe."""
    try:
        cfg = load_config(Path(args.config) if args.config else None)
    except MnsyncError:
        return set()
    return {c.moodle_course_id for c in cfg.courses}


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
def cmd_groups(args: argparse.Namespace) -> int:
    cfg, creds = _cargar(args)
    course = cfg.course(args.course)

    s = MoodleSession(creds.moodle_url)
    s.login(creds.moodle_username, creds.moodle_password)
    grupos = s.list_groups(course.moodle_course_id)

    titulo(f"GRUPOS DEL CURSO {course.moodle_course_id} EN MOODLE")
    if not grupos:
        print(" Este curso no tiene grupos configurados en Moodle.")
        print()
        print(" Sin grupos no se puede separar a tus estudiantes de los de otros")
        print(" profesores, así que la sincronización no puede continuar.")
        return 1

    configurados = {g.moodle_group_id for g in course.groups}
    print()
    for g in grupos:
        marca = "✓ configurado" if g.id in configurados else "  (sin configurar)"
        print(f"   {g.id:>8}  {g.name:<40} {marca}")
    print()
    print(" Los que dan «✓ configurado» ya están en courses.yml.")
    print()
    print(" Para agregar uno, copiá esto bajo «moodle: groups:» de tu curso:")
    print()
    faltantes = [g for g in grupos if g.id not in configurados]
    ejemplo = faltantes[0] if faltantes else grupos[0]
    print(f"        - id: {ejemplo.id}")
    print(f'          name: "{ejemplo.name}"')
    print()
    print(" No hace falta indicar centro universitario ni grupo de Notas")
    print(" Parciales: un grupo de Moodle reúne estudiantes de varios centros,")
    print(" y el programa averigua solo a dónde va cada uno.")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Cuando courses.yml tenga tus grupos, revisá que todo cuadre:",
            "",
            f"   mnsync plan --course {course.id}",
        ]
    )
    return 0


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> int:
    cfg, creds = _cargar(args)
    course = cfg.course(args.course)
    work = _work_dir(args)

    titulo(f"DESCARGA DE NOTAS — {course.id}")
    exports = fetch_groups(course, creds, work, groups=_grupos_pedidos(course, args))

    print()
    for ge in exports:
        print(f" ✓ {ge.group.label}: {len(ge.export)} estudiante(s)")
        print(f"     · {ge.xlsx.name}")
        if ge.export.grade_headers:
            print(f"     · columnas de nota: {', '.join(ge.export.grade_headers)}")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Abrí esos archivos en Excel si querés revisarlos. Después:",
            "",
            f"   mnsync plan --course {course.id}",
        ]
    )
    return 0


# ---------------------------------------------------------------------------
# destinos
# ---------------------------------------------------------------------------
def cmd_destinos(args: argparse.Namespace) -> int:
    """
    Averigua a qué grupos de Notas Parciales van a parar tus estudiantes.

    Es lo mismo que hace la primera sincronización, pero por separado y sin
    escribir nada, para poder anotar el resultado en ``courses.yml`` y que las
    corridas siguientes no tengan que volver a averiguarlo.
    """
    cfg, creds = _cargar(args)
    course = cfg.course(args.course)
    work = _work_dir(args)

    titulo(f"GRUPOS DE NOTAS PARCIALES — {course.id}")
    print()
    print(" Bajando tus grupos de Moodle y preguntándole al sistema de la UNED")
    print(" dónde está matriculado cada estudiante. Todo es de lectura: no se")
    print(" escribe ni una nota.")
    print()

    check = verificar_contexto(course, creds, work, groups=_grupos_pedidos(course, args))

    if not check.ok:
        print(f" ✗ {check.mensaje}")
        if check.remedio:
            print()
            print(f"   {check.remedio}")
        return 1

    print(f" ✓ {check.resumen}")
    print()
    print(f"   {'Grupo oficial':<28} {'Estudiantes'}")
    print(f"   {'-' * 28} {'-' * 11}")
    for cu in sorted(check.por_cu):
        for d in check.por_cu[cu]:
            cuantos = check.estudiantes_en.get(d.key, 0)
            print(f"   CU {d.cu} · grupo {d.grupo:<16} {cuantos}")
    print("=" * ANCHO)

    repartidos = [cu for cu, ds in check.por_cu.items() if len(ds) > 1]
    if repartidos:
        print()
        print(f" El centro universitario {', '.join(sorted(repartidos))} aparece en más de")
        print(" un grupo. Es normal: no todos sus estudiantes están en el mismo.")

    if check.sin_destino:
        print()
        print(f" ⚠ {len(check.sin_destino)} estudiante(s) no aparecen en ningún grupo")
        print("   oficial. Sus notas no se van a subir:")
        print()
        for cedula, nombre, cu in check.sin_destino:
            print(f"     {cedula:<14} {nombre or '(sin nombre)':<32} CU {cu}")
        print()
        print("   Suele significar que no quedaron matriculados en esta asignatura.")
        print("   Consultalo con registro antes del cierre de actas.")

    sin_emparejar = check.columnas_sin_emparejar
    if sin_emparejar:
        print()
        print(" ⚠ Estas columnas de Moodle no se reconocieron y NO se van a subir:")
        print()
        for i in sin_emparejar:
            print(f"     · {i.columna}")
        print()
        print("   Indicá a mano cuál instrumento les corresponde, con «item_map»")
        print("   en courses.yml. Mirá «courses.example.yml».")

    print()
    print(" Para que las próximas corridas no tengan que volver a averiguarlo,")
    print(" copiá esto en courses.yml, dentro de tu curso:")
    print()
    print("    destinos:")
    for d in check.destinos:
        print(f'      - cu: "{d.cu}"')
        print(f"        grupo: {d.grupo}")

    siguiente_paso(
        [
            "Ahora mirá qué se subiría, sin escribir nada:",
            "",
            f"   mnsync plan --course {course.id}",
        ]
    )
    return 0


# ---------------------------------------------------------------------------
# plan / sync
# ---------------------------------------------------------------------------
def _ejecutar(args: argparse.Namespace, *, commit: bool, fence: Path | None = None) -> int:
    cfg, creds = _cargar(args)
    course = cfg.course(args.course)
    work = _work_dir(args)

    modo = "SINCRONIZACIÓN (SE VA A ESCRIBIR)" if commit else "PRUEBA (no se escribe nada)"
    titulo(f"{modo} — {course.id}")

    report = sync_course(
        course,
        creds,
        work,
        commit=commit,
        groups=_grupos_pedidos(course, args),
        allow_update=True if getattr(args, "allow_update", False) else None,
        fence_journal=fence,
    )

    print()
    print(report.to_console())
    print()
    ruta = write_report(report, work)
    print(f" Reporte completo: {ruta}")
    print("=" * ANCHO)

    if report.hubo_errores:
        return 1

    if report.hubo_bloqueos:
        siguiente_paso(
            [
                "Un freno de seguridad detuvo al menos un grupo y NO se escribió.",
                "Abrí el reporte y mirá por qué:",
                "",
                f"   {ruta.name}",
            ]
        )
        return 2

    if commit:
        siguiente_paso(
            [
                "Listo. Para confirmar que quedó todo, volvé a correr la prueba:",
                "",
                f"   mnsync sync --course {course.id}",
                "",
                "Deberían salir todas como «ya estaban».",
            ]
        )
    else:
        siguiente_paso(
            [
                "Si el reporte se ve bien, este comando SÍ escribe de verdad",
                "en el sistema de la UNED:",
                "",
                f"   mnsync sync --course {course.id} --commit",
            ]
        )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    return _ejecutar(args, commit=False)


def cmd_sync(args: argparse.Namespace) -> int:
    return _ejecutar(args, commit=args.commit)


# ---------------------------------------------------------------------------
# rehearse
# ---------------------------------------------------------------------------
def cmd_rehearse(args: argparse.Namespace) -> int:
    """
    Ensayo contra el servidor **real**, con la escritura tapiada.

    Ejercita todo lo que un servidor falso no puede validar —el login NTLM de
    verdad, el roster de verdad, los instrumentos de verdad— y deja por
    escrito qué habría escrito una corrida real.
    """
    cfg, creds = _cargar(args)
    course = cfg.course(args.course)
    work = _work_dir(args)
    work.mkdir(parents=True, exist_ok=True)

    diario = work / f"rehearsal_{course.id}_{datetime.now():%Y%m%d_%H%M}.jsonl"

    titulo(f"ENSAYO — {course.id}")
    print()
    print(" Se conecta al sistema REAL de la UNED y hace todo el proceso,")
    print(" pero la única llamada que escribe queda interceptada.")
    print(f" Cada escritura bloqueada se anota en: {diario.name}")
    print()

    # La reja se verifica a sí misma antes de tocar nada.
    if not _fence_funciona(diario.parent):
        print(" ✗ No se pudo confirmar que la reja de escritura quedó instalada.")
        print()
        print("   El ensayo NO continúa. Una reja que no puede demostrar que")
        print("   está puesta no protege nada.")
        return 1
    print(" ✓ Reja de escritura verificada")

    report = sync_course(
        course,
        creds,
        work,
        commit=True,  # recorre el camino de escritura completo...
        groups=_grupos_pedidos(course, args),
        allow_update=True if getattr(args, "allow_update", False) else None,
        fence_journal=diario,  # ...pero la reja intercepta cada envío
    )

    print()
    print(report.to_console())
    print()
    interceptadas = _contar_lineas(diario)
    print(f" Escrituras interceptadas: {interceptadas}")

    if not interceptadas:
        # Sin escrituras no hay diario que leer, y mandar al profesor a buscar
        # un archivo que no existe convierte un buen resultado en un susto.
        print()
        print(" No había nada pendiente: todas las notas ya estaban puestas en el")
        print(" sistema. El ensayo recorrió el camino completo de escritura y no")
        print(" encontró nada que escribir, que es exactamente lo que uno quiere")
        print(" ver cuando ya sincronizó.")
        print("=" * ANCHO)
        siguiente_paso(
            [
                "No hace falta hacer nada más. Cuando pongás notas nuevas en",
                "Moodle, volvé a empezar por:",
                "",
                f"   mnsync plan --course {course.id}",
            ]
        )
        return 0

    print(f" Detalle: {diario}")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Revisá ese archivo línea por línea: es exactamente lo que una",
            "corrida real escribiría. Cuando estés conforme:",
            "",
            f"   mnsync sync --course {course.id} --commit",
        ]
    )
    return 0


def _fence_funciona(tmp_dir: Path) -> bool:
    """
    Comprueba en un subproceso que la reja se instala y deja su marca.

    Se ejercita el mecanismo de verdad —no se confía en que "debería andar"—
    porque de esta comprobación depende que el ensayo no escriba.
    """
    import subprocess

    codigo = (
        "import os,sys;"
        "sys.path.insert(0, r'" + str(Path(__file__).resolve().parents[1]) + "');"
        "from mnsync._uploader_shim import install_write_fence, FENCE_SENTINEL_ENV;"
        "from pathlib import Path;"
        "install_write_fence(Path(r'" + str(tmp_dir / '.fence_check.jsonl') + "'));"
        "import requests;"
        "s=requests.Session();"
        "r=s.post('http://127.0.0.1:9/x/actualizarNotas', data='{}');"
        "print('SENTINEL=' + os.environ.get(FENCE_SENTINEL_ENV,''));"
        "print('BLOCKED=' + str(r.status_code == 200))"
    )
    try:
        p = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    salida = p.stdout or ""
    return "SENTINEL=1" in salida and "BLOCKED=True" in salida


def _contar_lineas(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


# ---------------------------------------------------------------------------
# leer-ensayo
# ---------------------------------------------------------------------------
def cmd_leer_ensayo(args: argparse.Namespace) -> int:
    """
    Muestra en forma legible el diario que deja ``rehearse``.

    Ese archivo es la pieza que más importa revisar antes de escribir de
    verdad, así que tiene que poder leerse sin saber de JSON.
    """
    import json

    ruta = Path(args.archivo)
    if not ruta.exists():
        raise MnsyncError(
            f"No se encontró el archivo «{ruta}».",
            remedio="Los diarios de ensayo quedan en la carpeta «salida» con el nombre rehearsal_*.jsonl",
        )

    filas: list[tuple[str, str, str, str, str]] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            entrada = json.loads(linea)
        except json.JSONDecodeError:
            continue
        try:
            grupo = entrada["payload"]["grupo"]
            cu = str(grupo.get("CentroUniversitario", {}).get("Codigo", ""))
            num = str(grupo.get("Numero", ""))
            prom = grupo["ListaPromedios"][0]
            cedula = str(prom["Estudiante"]["Cedula"])
            nota_obj = prom["ListaNotas"][0]
            instrumento = str(nota_obj["InstrumentoEvaluacion"]["Codigo"])
            # TipoNota 1 = "no presentó"; el número que viaja es un relleno.
            valor = (
                "NO PRESENTÓ" if int(nota_obj.get("TipoNota", 3)) == 1
                else str(nota_obj.get("Nota", ""))
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        filas.append((f"{cu}/{num}", cedula, instrumento, valor, ""))

    titulo(f"ENSAYO — {ruta.name}")
    print()
    if not filas:
        print(" El ensayo no interceptó ninguna escritura.")
        print()
        print(" Significa que no había nada nuevo que subir: todas las notas")
        print(" ya estaban puestas en el sistema.")
        print("=" * ANCHO)
        return 0

    print(f" Estas son las {len(filas)} escrituras que una corrida real haría.")
    print(" NINGUNA se envió: todas quedaron interceptadas.")
    print()
    print(f"   {'CU/Grupo':<10} {'Cédula':<14} {'Instrumento':<14} {'Nota'}")
    print(f"   {'-' * 10} {'-' * 14} {'-' * 14} {'-' * 12}")
    for destino, cedula, instrumento, valor, _ in filas:
        print(f"   {destino:<10} {cedula:<14} {instrumento:<14} {valor}")
    print("=" * ANCHO)

    siguiente_paso(
        [
            "Revisá esta lista contra Moodle. Si está bien, y solo entonces,",
            "podés pasar a escribir de verdad con «sync --commit».",
            "",
            "Empezá chiquito: poné max_changes: 1 y subí una sola nota.",
        ]
    )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mnsync",
        description=(
            "Sincroniza las notas de Moodle con el sistema de Notas Parciales de la UNED. "
            "Por defecto no escribe nada: hay que pedirlo con --commit."
        ),
    )
    p.add_argument("--version", action="version", version=f"mnsync {__version__}")
    p.add_argument("--config", help="Ruta a courses.yml (por defecto: ./courses.yml)")
    p.add_argument("--out", help="Carpeta para archivos y reportes (por defecto: ./salida)")

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="¿Está todo bien configurado?")
    d.add_argument("--offline", action="store_true", help="No probar la conexión")
    d.set_defaults(func=cmd_doctor)

    cr = sub.add_parser(
        "credenciales",
        help="Guardar las contraseñas en el sistema, en vez de en un archivo",
    )
    cr.add_argument("--ver", action="store_true", help="Mostrar qué usuarios hay guardados")
    cr.add_argument("--borrar", action="store_true", help="Borrar las credenciales guardadas")
    cr.set_defaults(func=cmd_credenciales)

    cu = sub.add_parser("cursos", help="Ver tus cursos de Moodle con su número")
    cu.set_defaults(func=cmd_cursos)

    g = sub.add_parser("groups", help="Ver los grupos del curso en Moodle")
    g.add_argument("--course", required=True, help="id del curso en courses.yml")
    g.set_defaults(func=cmd_groups)

    f = sub.add_parser("fetch", help="Descargar las notas de Moodle (sin subir nada)")
    f.add_argument("--course", required=True)
    f.add_argument("--group", help="Limitar a un grupo de Moodle")
    f.set_defaults(func=cmd_fetch)

    de = sub.add_parser(
        "destinos",
        help="Ver a qué grupos de Notas Parciales van tus estudiantes",
    )
    de.add_argument("--course", required=True)
    de.add_argument("--group", help="Limitar a un grupo de Moodle")
    de.set_defaults(func=cmd_destinos)

    pl = sub.add_parser("plan", help="Ver qué se subiría, sin escribir nada")
    pl.add_argument("--course", required=True)
    pl.add_argument("--group", help="Limitar a un grupo de Moodle")
    pl.set_defaults(func=cmd_plan)

    s = sub.add_parser("sync", help="Sincronizar (con --commit escribe de verdad)")
    s.add_argument("--course", required=True)
    s.add_argument("--group", help="Limitar a un grupo de Moodle")
    s.add_argument("--commit", action="store_true", help="Escribir de verdad en la UNED")
    s.add_argument(
        "--allow-update",
        action="store_true",
        help="Permitir cambiar notas que ya están puestas (queda justificado en el sistema)",
    )
    s.set_defaults(func=cmd_sync)

    r = sub.add_parser(
        "rehearse",
        help="Ensayo contra el servidor real con la escritura interceptada",
    )
    r.add_argument("--course", required=True)
    r.add_argument("--group", help="Limitar a un grupo de Moodle")
    r.add_argument("--allow-update", action="store_true")
    r.set_defaults(func=cmd_rehearse)

    le = sub.add_parser(
        "leer-ensayo",
        help="Mostrar en forma legible el diario que dejó «rehearse»",
    )
    le.add_argument("archivo", help="Ruta al archivo rehearsal_*.jsonl")
    le.set_defaults(func=cmd_leer_ensayo)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except MnsyncError as e:
        print()
        print("─" * ANCHO)
        print(" ✗ No se pudo continuar")
        print()
        print(f"   {e.mensaje}")
        if e.remedio:
            print()
            print(f"   ¿Qué hacer?  {e.remedio}")
        print("─" * ANCHO)
        return 1
    except KeyboardInterrupt:
        print("\n Cancelado. No se escribió nada.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
